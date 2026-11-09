"""`claude` · `gh` 같은 외부 명령의 위치를 찾는다.

Finder나 로그인 자동 실행으로 뜬 앱은 PATH가 짧다(/usr/bin:/bin 정도). 그래서 PATH 다음에
설치 프로그램들이 흔히 쓰는 위치를 보고, 마지막으로 로그인 셸에게 묻는다.
"""

import functools
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

COMMON_DIRS = (
    "~/.local/bin",  # Claude Code 공식 설치 프로그램
    "~/.claude/local",
    "/opt/homebrew/bin",
    "/usr/local/bin",
)

Runner = Callable[[list[str]], str | None]


def _login_shell(args: list[str]) -> str | None:  # pragma: no cover - 실제 셸
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def find_tool(
    name: str,
    which: Callable[[str], str | None] = shutil.which,
    dirs: tuple[str, ...] = COMMON_DIRS,
    shell: Runner = _login_shell,
) -> str | None:
    found = which(name)
    if found:
        return found
    for folder in dirs:
        candidate = Path(folder).expanduser() / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    login = os.environ.get("SHELL") or "/bin/zsh"
    answer = shell([login, "-lc", f"command -v {name}"])
    if answer and answer.splitlines()[-1].startswith("/"):
        return answer.splitlines()[-1]
    return None


@functools.cache
def tool(name: str) -> str:
    """찾은 경로. 못 찾으면 이름 그대로 (실행할 때 '설치되지 않음'으로 드러난다)."""
    return find_tool(name) or name
