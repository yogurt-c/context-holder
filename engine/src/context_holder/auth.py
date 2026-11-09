"""GitHub 토큰을 가져온다. 토큰은 파일 · 환경 변수 · 위키 어디에도 쓰지 않는다 (plan 3.2).

1. macOS 키체인 (앱이 Device Flow로 받아 저장한다)
2. `gh auth token` (이미 GitHub CLI에 로그인한 사용자)

Device Flow 토큰은 8시간 뒤 만료되고, 리프레시 토큰(6개월, 쓰면 바뀐다)으로 새로 받는다.
화면 서버 · 수집 · ctx mcp가 동시에 갱신하면 리프레시 토큰이 바뀌어 한쪽이 무효가 되므로
기기 로컬 잠금 파일로 한 번에 하나만 갱신한다.
"""

import fcntl
import json
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import keyring
import keyring.errors

from context_holder.paths import home
from context_holder.tools import tool

KEYCHAIN_SERVICE = "context-holder"
KEYCHAIN_ACCOUNT = "github"
CLIENT_ID = "Ov23liLDrGVpOxrV5EwI"  # GitHub OAuth App (Device Flow). 비밀이 아니다
TOKEN_URL = "https://github.com/login/oauth/access_token"
REFRESH_EARLY = timedelta(minutes=5)

Clock = Callable[[], datetime]


class TokenMissing(Exception):
    pass


class TokenExpired(TokenMissing):
    """리프레시 토큰까지 만료되거나 취소됐다. 다시 로그인해야 한다."""


@dataclass(frozen=True)
class StoredToken:
    access_token: str
    expires_at: datetime | None = None  # None: 만료 없음
    refresh_token: str | None = None
    refresh_expires_at: datetime | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "expires_at": _iso(self.expires_at),
                "refresh_token": self.refresh_token,
                "refresh_expires_at": _iso(self.refresh_expires_at),
            }
        )

    @classmethod
    def from_secret(cls, secret: str) -> "StoredToken":
        """키체인 값. 예전 방식(토큰 문자열만)도 읽는다."""
        try:
            data = json.loads(secret)
        except ValueError:
            return cls(secret.strip())
        if not isinstance(data, dict):
            return cls(secret.strip())
        return cls(
            data["access_token"],
            _parse(data.get("expires_at")),
            data.get("refresh_token"),
            _parse(data.get("refresh_expires_at")),
        )

    @classmethod
    def from_response(cls, data: dict, now: datetime) -> "StoredToken":
        """GitHub 토큰 응답. 만료 설정을 끈 OAuth App이면 expires_in이 없다."""
        return cls(
            data["access_token"],
            now + timedelta(seconds=int(data["expires_in"])) if data.get("expires_in") else None,
            data.get("refresh_token"),
            now + timedelta(seconds=int(data["refresh_token_expires_in"]))
            if data.get("refresh_token_expires_in")
            else None,
        )


class Keychain:
    def read(self) -> str | None:
        try:
            return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        except keyring.errors.KeyringError:
            return None

    def write(self, secret: str) -> None:
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, secret)

    def delete(self) -> None:
        with suppress(keyring.errors.KeyringError):
            keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


def save_token(token: StoredToken, keychain: Keychain | None = None) -> None:
    (keychain or Keychain()).write(token.to_json())


def github_token(
    keychain: Keychain | None = None,
    clock: Clock = lambda: datetime.now(UTC),
    client: httpx.Client | None = None,
    lock_path: Path | None = None,
    gh: Callable[[], str | None] | None = None,
) -> str:
    keychain = keychain or Keychain()
    secret = keychain.read()
    if secret:
        return _fresh(StoredToken.from_secret(secret), keychain, clock, client, lock_path)
    token = (gh or gh_lookup)()
    if token:
        return token
    raise TokenMissing(
        "GitHub에 로그인하지 않았다. 앱에서 GitHub 로그인을 하거나 gh auth login을 실행한다"
    )


def token_source(keychain: Keychain | None = None) -> str | None:
    """ "app"(Device Flow) · "gh" · None. 설정 화면에 보여 준다."""
    if (keychain or Keychain()).read():
        return "app"
    return "gh" if gh_lookup() else None


# ---------- 내부 ----------


def _fresh(
    token: StoredToken,
    keychain: Keychain,
    clock: Clock,
    client: httpx.Client | None,
    lock_path: Path | None,
) -> str:
    if token.expires_at is None or clock() < token.expires_at - REFRESH_EARLY:
        return token.access_token
    with _locked(lock_path or home() / "github-token.lock"):
        current = StoredToken.from_secret(keychain.read() or token.to_json())
        if current.expires_at and clock() < current.expires_at - REFRESH_EARLY:
            return current.access_token  # 다른 프로세스가 먼저 갱신했다
        refreshed = _refresh(current, clock, client)
        keychain.write(refreshed.to_json())
        return refreshed.access_token


def _refresh(token: StoredToken, clock: Clock, client: httpx.Client | None) -> StoredToken:
    now = clock()
    if not token.refresh_token or (token.refresh_expires_at and now >= token.refresh_expires_at):
        raise TokenExpired("GitHub 로그인이 만료됐다. 앱에서 다시 로그인한다")
    params = {
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": token.refresh_token,
    }
    owned = client is None
    http = client or httpx.Client(timeout=30)
    try:
        response = http.post(TOKEN_URL, data=params, headers={"Accept": "application/json"})
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        raise TokenMissing(f"GitHub 토큰을 새로 받지 못했다: {e}") from e
    finally:
        if owned:
            http.close()
    if "access_token" not in data:  # bad_refresh_token 등
        raise TokenExpired(f"GitHub 로그인이 만료됐다 ({data.get('error')}). 다시 로그인한다")
    return StoredToken.from_response(data, now)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _gh_token() -> str | None:
    try:
        out = subprocess.run(
            [tool("gh"), "auth", "token"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


gh_lookup: Callable[[], str | None] = _gh_token  # 테스트에서 바꾼다


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None
