"""Claude Code 설치 · 로그인 · MCP 등록 (plan 3.2). API 키 없이 구독 로그인을 쓴다.

- 상태: `claude auth status --json`
- 설치: 공식 설치 프로그램(claude.ai/install.sh). 사용자가 버튼을 눌러야 실행한다
- 로그인: `claude auth login --claudeai`가 브라우저 승인을 띄운다
- MCP: `~/.claude` 파일을 직접 고치지 않고 `claude mcp add --scope user`로 등록한다
  (Claude Code가 자기 설정을 병합한다). 등록하는 명령은 지금 돌고 있는 엔진이다.
"""

import json
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
    detail: str = ""  # 구독 종류 등 (이메일은 보여 주지 않는다)


def run_command(args: Sequence[str], timeout: float) -> tuple[int, str]:  # pragma: no cover
    try:
        done = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return done.returncode, done.stdout + done.stderr


def status(
    run: Run = run_command, claude: Callable[[], str] = lambda: tool("claude")
) -> ClaudeStatus:
    code, out = run([claude(), "auth", "status", "--json"], TIMEOUT)
    if code == 127:
        return ClaudeStatus(False, False)
    try:
        data = json.loads(out)
    except ValueError:
        return ClaudeStatus(True, False)
    kind = data.get("subscriptionType") or data.get("authMethod") or ""
    return ClaudeStatus(True, bool(data.get("loggedIn")), str(kind))


def install_command() -> list[str]:
    return ["/bin/bash", "-c", f"curl -fsSL {INSTALL_SCRIPT} | bash"]


def login_command(claude: str) -> list[str]:
    return [claude, "auth", "login", "--claudeai"]


def engine_command() -> list[str]:
    """Claude Code가 실행할 MCP 서버 명령: 지금 돌고 있는 엔진."""
    if getattr(sys, "frozen", False):  # 앱에 묶인 엔진
        return [sys.executable, "mcp"]
    script = Path(sys.executable).with_name("ctx")  # 개발: .venv/bin/ctx
    if script.exists():
        return [str(script), "mcp"]
    return [sys.executable, "-m", "context_holder", "mcp"]


def registered_command(run: Run = run_command, claude: str | None = None) -> list[str] | None:
    """Claude Code에 등록된 context-holder MCP 명령. 없으면 None."""
    code, out = run([claude or tool("claude"), "mcp", "get", MCP_NAME], TIMEOUT)
    if code != 0:
        return None
    command, args = None, []
    for line in out.splitlines():
        key, _, value = line.strip().partition(":")
        if key == "Command":
            command = value.strip()
        elif key == "Args":
            args = value.split()
    return [command, *args] if command else None


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
