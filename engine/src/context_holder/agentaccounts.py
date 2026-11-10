"""에이전트 로그인 계정. Orca처럼 시스템 기본값과 이 앱에서 추가한 계정들 중 하나를 쓴다.

- system: 터미널과 같은 기본 설정 폴더(~/.claude · ~/.codex)의 로그인
- 추가한 계정: `~/.context-holder/accounts/{claude|codex}/{id}`를 설정 폴더로 쓴다
  (CLAUDE_CONFIG_DIR · CODEX_HOME). 터미널에서 계정을 바꿔도 그대로다.
  Claude Code는 설정 폴더마다 키체인 항목을 따로 둔다.

계정 폴더의 account.json에 마지막으로 확인한 이메일 · 요금제 · 추가한 때를 남긴다.
목록을 그릴 때 계정마다 명령을 부르지 않으려고다 (claude auth status는 1초쯤 걸린다).
위키 검색(MCP) 등록은 터미널에서 쓰는 에이전트에 붙어야 해서 늘 system 쪽에 한다.
"""

import json
import re
import secrets
import shutil
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from context_holder.paths import home

SYSTEM = "system"
CONFIG_VARS = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
META = "account.json"
_ID = re.compile(r"[0-9a-f]{8}")


@dataclass(frozen=True)
class Account:
    id: str
    email: str = ""
    plan: str = ""
    org: str = ""
    added: str = ""  # ISO 시각


def valid(account: str) -> bool:
    return account == SYSTEM or bool(_ID.fullmatch(account))


def account_dir(agent: str, account: str, root: Path | None = None) -> Path:
    return (root or home() / "accounts") / agent / account


def command_prefix(agent: str, account: str, root: Path | None = None) -> list[str]:
    """에이전트 명령 앞에 붙일 것. 추가한 계정이면 그 설정 폴더를 환경 변수로 넘긴다.

    지워진 계정이면 시스템 기본값으로 돌린다.
    """
    if account == SYSTEM:
        return []
    folder = account_dir(agent, account, root)
    if not folder.is_dir():
        return []
    return ["/usr/bin/env", f"{CONFIG_VARS[agent]}={folder}"]


def create(agent: str, now: datetime, root: Path | None = None) -> str:
    account = secrets.token_hex(4)
    folder = account_dir(agent, account, root)
    folder.mkdir(parents=True)
    _write(folder, Account(account, added=now.isoformat()))
    return account


def list_accounts(agent: str, root: Path | None = None) -> list[Account]:
    folder = (root or home() / "accounts") / agent
    if not folder.is_dir():
        return []
    found = [_read(path) for path in folder.iterdir() if path.is_dir() and valid(path.name)]
    return sorted(found, key=lambda a: a.added)


def remember(agent: str, account: str, email: str, plan: str, org: str, root: Path | None = None):
    folder = account_dir(agent, account, root)
    if folder.is_dir():
        _write(folder, replace(_read(folder), email=email, plan=plan, org=org))


def remove(agent: str, account: str, root: Path | None = None) -> None:
    if valid(account) and account != SYSTEM:
        shutil.rmtree(account_dir(agent, account, root), ignore_errors=True)


def added_label(account: Account) -> str:
    """ "10월 20일 오후 6:05" 꼴."""
    try:
        at = datetime.fromisoformat(account.added).astimezone()
    except ValueError:
        return ""
    half = "오전" if at.hour < 12 else "오후"
    return f"{at.month}월 {at.day}일 {half} {at.hour % 12 or 12}:{at.minute:02d}"


def _read(folder: Path) -> Account:
    try:
        data = json.loads((folder / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    return Account(
        folder.name,
        str(data.get("email") or ""),
        str(data.get("plan") or ""),
        str(data.get("org") or ""),
        str(data.get("added") or ""),
    )


def _write(folder: Path, account: Account) -> None:
    data = {k: v for k, v in asdict(account).items() if k != "id"}
    (folder / META).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
