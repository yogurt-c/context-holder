"""Codex CLI 설치 · 로그인 상태. API 키 없이 ChatGPT 구독 로그인을 쓴다.

- 상태: `codex login status` (종료 코드 0이면 로그인됨)
- 계정: `$CODEX_HOME/auth.json`의 id_token에서 이메일 · 요금제만 읽는다. 토큰은 쓰지 않는다
  (CLI가 다른 계정으로 로그인돼 있어도 화면에서 알아보게)
- 설치: 공식 설치 프로그램(chatgpt.com/codex/install.sh, ~/.local/bin). 버튼을 눌러야 실행한다
- 로그인: `codex login`이 브라우저 승인을 띄운다
- MCP: `~/.codex/config.toml`을 직접 고치지 않고 `codex mcp add`로 등록한다
"""

import base64
import json
import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from context_holder import claudesetup
from context_holder.claudesetup import MCP_NAME, TIMEOUT, ClaudeStatus, Run, run_command
from context_holder.tools import tool

INSTALL_SCRIPT = "https://chatgpt.com/codex/install.sh"
AUTH_CLAIMS = "https://api.openai.com/auth"


def codex_home() -> Path:
    folder = os.environ.get("CODEX_HOME")
    return Path(folder) if folder else Path.home() / ".codex"


def status(
    run: Run = run_command,
    codex: Callable[[], str] = lambda: tool("codex"),
    home: Path | None = None,
) -> ClaudeStatus:
    code, out = run([codex(), "login", "status"], TIMEOUT)
    if code == 127:
        return ClaudeStatus(False, False)
    if code != 0:
        return ClaudeStatus(True, False)
    if "api key" in out.lower():
        return ClaudeStatus(True, True, "API 키")
    claims = _id_claims(home or codex_home())
    plan = str((claims.get(AUTH_CLAIMS) or {}).get("chatgpt_plan_type") or "ChatGPT")
    return ClaudeStatus(True, True, plan, str(claims.get("email") or ""))


def install_command() -> list[str]:
    return ["/bin/bash", "-c", f"curl -fsSL {INSTALL_SCRIPT} | sh"]


def login_command(codex: str) -> list[str]:
    return [codex, "login"]


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
