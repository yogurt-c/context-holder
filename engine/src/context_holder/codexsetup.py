"""Codex CLI 설치 · 로그인 상태. API 키 없이 ChatGPT 구독 로그인을 쓴다.

- 위치: 공식 설치 프로그램이 까는 `~/.local/bin/codex`만 쓴다 (tools.OFFICIAL)
- 상태: `codex login status` (종료 코드 0이면 로그인됨). 요약에 쓰는 옵션이 없으면 옛 버전으로 본다
- 계정: `$CODEX_HOME/auth.json`의 id_token에서 이메일 · 요금제만 읽는다. 토큰은 쓰지 않는다
  (CLI가 다른 계정으로 로그인돼 있어도 화면에서 알아보게)
- 설치: 공식 설치 프로그램(chatgpt.com/codex/install.sh, ~/.local/bin). 버튼을 눌러야 실행한다
- 로그인: `codex login`이 브라우저 승인을 띄운다
- MCP: `~/.codex/config.toml`을 직접 고치지 않고 `codex mcp add`로 등록한다
"""

import base64
import json
import os
import re
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from context_holder import claudesetup
from context_holder.claudesetup import MCP_NAME, TIMEOUT, ClaudeStatus, Run, run_command
from context_holder.tools import tool

INSTALL_SCRIPT = "https://chatgpt.com/codex/install.sh"
REQUIRED = ("--output-schema", "--ignore-user-config")  # 요약에 쓰는 옵션 (llm.CodexCli)
LOGIN_PORT = 1455  # `codex login`이 브라우저 승인 응답을 받는 고정 포트
LSOF = "/usr/sbin/lsof"
AUTH_CLAIMS = "https://api.openai.com/auth"


def codex_home() -> Path:
    folder = os.environ.get("CODEX_HOME")
    return Path(folder) if folder else Path.home() / ".codex"


def status(
    run: Run = run_command,
    codex: Callable[[], str] = lambda: tool("codex"),
    home: Path | None = None,  # auth.json을 읽을 설정 폴더
    prefix: Sequence[str] = (),  # 계정 설정 폴더 (agentaccounts.command_prefix)
) -> ClaudeStatus:
    exe = codex()
    code, out = run([*prefix, exe, "login", "status"], TIMEOUT)
    if code == 127:
        return ClaudeStatus(False, False)
    outdated = _outdated(run, exe)
    if code != 0:
        return ClaudeStatus(True, False, outdated=outdated)
    if "api key" in out.lower():
        return ClaudeStatus(True, True, "API 키", outdated=outdated)
    claims = _id_claims(home or codex_home())
    plan = str((claims.get(AUTH_CLAIMS) or {}).get("chatgpt_plan_type") or "ChatGPT")
    return ClaudeStatus(True, True, plan, str(claims.get("email") or ""), outdated=outdated)


def _outdated(run: Run, exe: str) -> str:
    """요약에 쓰는 옵션이 없는 옛 버전이면 그 버전 ("0.22.0"), 아니면 ""."""
    _, help_text = run([exe, "exec", "--help"], TIMEOUT)
    if all(option in help_text for option in REQUIRED):
        return ""
    _, out = run([exe, "--version"], TIMEOUT)
    found = re.search(r"\d+\.\d+\.\d+", out)
    return found.group(0) if found else "알 수 없는 버전"


def install_command() -> list[str]:
    return ["/bin/bash", "-c", f"curl -fsSL {INSTALL_SCRIPT} | sh"]


def login_command(codex: str, prefix: Sequence[str] = ()) -> list[str]:
    return [*prefix, codex, "login"]


def logout_command(codex: str, prefix: Sequence[str] = ()) -> list[str]:
    return [*prefix, codex, "logout"]


def free_login_port(
    run: Run,
    kill: Callable[[int], None],
    sleep: Callable[[float], None],
    uid: int | None = None,
) -> str:
    """로그인 포트를 비운다. 비웠으면 "", 못 비우면 화면에 보일 이유.

    앞서 띄운 `codex login`이 남아 있으면(브라우저를 닫은 터미널 로그인 등) 새 로그인이 포트를
    못 열고 바로 끝난다. 이 사용자의 `codex login`만 끄고, 다른 프로그램은 건드리지 않는다.
    """
    holders = _listeners(run)
    if not holders:
        return ""
    me = os.getuid() if uid is None else uid
    for pid in holders:
        _, out = run(["/bin/ps", "-o", "uid=,command=", "-p", str(pid)], TIMEOUT)
        owner, _, command = out.strip().partition(" ")
        if owner != str(me) or not _is_codex_login(command):
            return (
                f"다른 프로그램이 Codex 로그인 포트({LOGIN_PORT})를 쓰고 있어요. "
                "그 프로그램을 닫고 다시 시도해 주세요."
            )
    for pid in holders:
        kill(pid)
    for _ in range(20):
        sleep(0.1)
        if not _listeners(run):
            return ""
    return "이전 Codex 로그인을 닫지 못했어요. 잠시 뒤 다시 시도해 주세요."


def _listeners(run: Run) -> list[int]:
    _, out = run([LSOF, "-nP", f"-iTCP:{LOGIN_PORT}", "-sTCP:LISTEN", "-t"], TIMEOUT)
    return [int(word) for word in out.split() if word.isdigit()]


def _is_codex_login(command: str) -> bool:
    words = command.split()
    return any(Path(word).name == "codex" for word in words) and "login" in words


def engine_command() -> list[str]:
    return claudesetup.engine_command()


def registered_command(home: Path | None = None) -> list[str] | None:
    """Codex 설정에 등록된 context-holder MCP 명령. 없으면 None."""
    try:
        data = tomllib.loads(((home or codex_home()) / "config.toml").read_text(encoding="utf-8"))
        server = data["mcp_servers"][MCP_NAME]
        return [str(server["command"]), *[str(a) for a in server.get("args") or []]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def register_mcp(run: Run = run_command, codex: str | None = None) -> tuple[bool, str]:
    """다시 등록한다 (있으면 지우고 넣는다). (성공, 출력)"""
    exe = codex or tool("codex")
    run([exe, "mcp", "remove", MCP_NAME], TIMEOUT)
    code, out = run([exe, "mcp", "add", MCP_NAME, "--", *engine_command()], TIMEOUT)
    return code == 0, out.strip()


def _id_claims(home: Path) -> dict[str, Any]:
    try:
        data = json.loads((home / "auth.json").read_text(encoding="utf-8"))
        payload = data["tokens"]["id_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return {}
    return claims if isinstance(claims, dict) else {}
