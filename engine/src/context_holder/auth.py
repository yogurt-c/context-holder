"""GitHub 토큰을 가져온다. 토큰은 파일 · 환경 변수 · 위키 어디에도 쓰지 않는다 (plan 3.2).

1. macOS 키체인 (앱이 Device Flow 로그인 후 저장한다)
2. `gh auth token` (이미 GitHub CLI에 로그인한 사용자)
"""

import subprocess

KEYCHAIN_SERVICE = "context-holder"
KEYCHAIN_ACCOUNT = "github"


class TokenMissing(Exception):
    pass


def github_token() -> str:
    for command in (
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        ["gh", "auth", "token"],
    ):
        token = _run(command)
        if token:
            return token
    raise TokenMissing(
        "GitHub에 로그인하지 않았다. 앱에서 GitHub 로그인을 하거나 gh auth login을 실행한다"
    )


def _run(command: list[str]) -> str | None:
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None
