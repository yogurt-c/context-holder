"""GitHub Device Flow 로그인 (plan 3.2). 토큰 붙여 넣기 없이 브라우저 승인 한 번으로 끝난다.

1. 기기 코드를 받는다: 사용자 코드(WDJB-MJHT)와 승인 주소
2. 사용자가 브라우저에서 코드를 넣고 승인하는 동안 interval마다 묻는다
3. 승인되면 토큰을 키체인에 저장한다 (auth). client secret은 필요 없다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from context_holder.auth import CLIENT_ID, TOKEN_URL, StoredToken

CODE_URL = "https://github.com/login/device/code"
SCOPE = "repo"  # 위키 레포(private) 읽기 · 쓰기
GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SLOW_DOWN_SECONDS = 5


class DeviceFlowError(Exception):
    pass


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int  # 초
    expires_at: datetime


@dataclass(frozen=True)
class Poll:
    state: str  # pending | slow_down | done | expired | denied
    token: StoredToken | None = None
    interval: int | None = None  # slow_down이면 늘어난 간격


def start(client: httpx.Client, now: datetime) -> DeviceCode:
    data = _post(client, CODE_URL, {"client_id": CLIENT_ID, "scope": SCOPE})
    if "device_code" not in data:
        raise DeviceFlowError(f"GitHub 로그인을 시작하지 못했다: {data.get('error', data)}")
    return DeviceCode(
        data["device_code"],
        data["user_code"],
        data["verification_uri"],
        int(data.get("interval", 5)),
        now + timedelta(seconds=int(data.get("expires_in", 900))),
    )


def poll(client: httpx.Client, code: DeviceCode, now: datetime) -> Poll:
    if now >= code.expires_at:
        return Poll("expired")
    data = _post(
        client,
        TOKEN_URL,
        {"client_id": CLIENT_ID, "device_code": code.device_code, "grant_type": GRANT},
    )
    if "access_token" in data:
        return Poll("done", StoredToken.from_response(data, now))
    error = data.get("error")
    if error == "authorization_pending":
        return Poll("pending")
    if error == "slow_down":
        return Poll(
            "slow_down", interval=int(data.get("interval", code.interval + SLOW_DOWN_SECONDS))
        )
    if error == "expired_token":
        return Poll("expired")
    if error == "access_denied":
        return Poll("denied")
    raise DeviceFlowError(f"GitHub 로그인에 실패했다: {error or data}")


def _post(client: httpx.Client, url: str, params: dict[str, str]) -> dict:
    try:
        response = client.post(url, data=params, headers={"Accept": "application/json"})
        return response.json()
    except (httpx.HTTPError, ValueError) as e:
        raise DeviceFlowError(f"GitHub에 연결하지 못했다: {e}") from e
