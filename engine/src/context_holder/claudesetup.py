"""Claude Code 설치 · 로그인 · MCP 등록 (plan 3.2). API 키 없이 구독 로그인을 쓴다.

- 상태: `claude auth status --json`
- 설치: 공식 설치 프로그램(claude.ai/install.sh). 사용자가 버튼을 눌러야 실행한다
- 로그인: `claude auth login --claudeai`가 브라우저 승인을 띄운다
- MCP: `~/.claude` 파일을 직접 고치지 않고 `claude mcp add --scope user`로 등록한다
  (Claude Code가 자기 설정을 병합한다). 등록하는 명령은 지금 돌고 있는 엔진이다.
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from context_holder.tools import tool

INSTALL_SCRIPT = "https://claude.ai/install.sh"
MCP_NAME = "context-holder"
TIMEOUT = 30

Run = Callable[[Sequence[str], float], tuple[int, str]]  # (명령, 제한 시간) → (종료 코드, 출력)


@dataclass(frozen=True)
class ClaudeStatus:
    installed: bool
    logged_in: bool
    detail: str = ""  # 구독 종류 (max, pro, team 등)
    account: str = ""  # 로그인한 계정 이메일. 이 기기의 설정 화면에만 보인다 (계정을 바꿨는지 확인)
    org: str = ""  # 팀 · 조직 계정이면 조직 이름
    outdated: str = ""  # 앱이 쓰는 옵션이 없는 옛 버전이면 그 버전 (업데이트가 필요하다)

    @property
    def label(self) -> str:
        """화면에 보일 계정 표시: "me@x.com · 조직 (max)". 아는 것만 붙인다."""
        who = " · ".join(part for part in (self.account, self.org) if part)
        if who and self.detail:
            return f"{who} ({self.detail})"
        return who or self.detail


def run_command(args: Sequence[str], timeout: float) -> tuple[int, str]:  # pragma: no cover
    try:
        done = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return done.returncode, done.stdout + done.stderr


def status(
    run: Run = run_command,
    claude: Callable[[], str] = lambda: tool("claude"),
    prefix: Sequence[str] = (),  # 계정 설정 폴더 (agentaccounts.command_prefix)
) -> ClaudeStatus:
    code, out = run([*prefix, claude(), "auth", "status", "--json"], TIMEOUT)
    if code == 127:
        return ClaudeStatus(False, False)
    try:
        data = json.loads(out)
    except ValueError:
        return ClaudeStatus(True, False)
    kind = data.get("subscriptionType") or data.get("authMethod") or ""
    return ClaudeStatus(
        True,
        bool(data.get("loggedIn")),
        str(kind),
        str(data.get("email") or ""),
        str(data.get("orgName") or ""),
    )


def install_command() -> list[str]:
    return ["/bin/bash", "-c", f"curl -fsSL {INSTALL_SCRIPT} | bash"]


def login_command(claude: str, prefix: Sequence[str] = ()) -> list[str]:
    return [*prefix, claude, "auth", "login", "--claudeai"]


def logout_command(claude: str, prefix: Sequence[str] = ()) -> list[str]:
    return [*prefix, claude, "auth", "logout"]


def engine_command() -> list[str]:
    """Claude Code가 실행할 MCP 서버 명령: 지금 돌고 있는 엔진."""
    if getattr(sys, "frozen", False):  # 앱에 묶인 엔진
        return [sys.executable, "mcp"]
    script = Path(sys.executable).with_name("ctx")  # 개발: .venv/bin/ctx
    if script.exists():
        return [str(script), "mcp"]
    return [sys.executable, "-m", "context_holder", "mcp"]


def translocated() -> bool:
    """macOS가 내려받은 앱을 임시 위치에서 실행 중인가 (App Translocation).

    이때 등록하면 MCP 명령이 사라질 경로를 가리킨다. 응용 프로그램 폴더로 옮긴 뒤 등록한다.
    """
    return "/AppTranslocation/" in engine_command()[0]


def claude_config() -> Path:
    """Claude Code 설정 파일. 사용자 범위 MCP 등록이 여기 있다 (CLAUDE_CONFIG_DIR을 따른다)."""
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(folder) / ".claude.json" if folder else Path.home() / ".claude.json"


def registered_command(config: Path | None = None) -> list[str] | None:
    """Claude Code에 등록된 context-holder MCP 명령. 없으면 None.

    `claude mcp get`은 서버를 실제로 띄워 확인해서 2초쯤 걸린다. 설정 파일만 읽는다.
    """
    try:
        data = json.loads((config or claude_config()).read_text(encoding="utf-8"))
        server = data["mcpServers"][MCP_NAME]
        return [str(server["command"]), *[str(a) for a in server.get("args") or []]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def register_mcp(run: Run = run_command, claude: str | None = None) -> tuple[bool, str]:
    """다시 등록한다 (있으면 지우고 넣는다). (성공, 출력)"""
    exe = claude or tool("claude")
    run([exe, "mcp", "remove", MCP_NAME, "--scope", "user"], TIMEOUT)
    code, out = run(
        [exe, "mcp", "add", MCP_NAME, "--scope", "user", "--", *engine_command()], TIMEOUT
    )
    return code == 0, out.strip()


def mcp_preview(claude: str | None = None) -> str:
    """등록 전에 보여 줄 명령."""
    exe = claude or "claude"
    return " ".join([exe, "mcp", "add", MCP_NAME, "--scope", "user", "--", *engine_command()])
