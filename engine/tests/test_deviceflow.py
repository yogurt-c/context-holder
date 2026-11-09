"""GitHub Device Flow: 코드 받기, 기다리기 · 간격 늘리기 · 만료 · 거부, 토큰."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from context_holder import deviceflow
from context_holder.auth import CLIENT_ID

NOW = datetime(2026, 11, 9, 3, tzinfo=UTC)


def server(responses: list, seen: list) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), dict(httpx.QueryParams(request.content.decode()))))
        body = responses.pop(0)
        if isinstance(body, Exception):
            raise body
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


CODE = {
    "device_code": "dev",
    "user_code": "WDJB-MJHT",
    "verification_uri": "https://github.com/login/device",
    "expires_in": 900,
    "interval": 5,
}


def test_start_asks_for_repo_scope():
    seen = []
    code = deviceflow.start(server([CODE], seen), NOW)
    assert code == deviceflow.DeviceCode(
        "dev", "WDJB-MJHT", "https://github.com/login/device", 5, NOW + timedelta(minutes=15)
    )
    assert seen == [(deviceflow.CODE_URL, {"client_id": CLIENT_ID, "scope": "repo"})]


@pytest.mark.parametrize(
    "body", [{"error": "unauthorized_client"}, "<html>", httpx.ConnectError("x")]
)
def test_start_failures(body):
    with pytest.raises(deviceflow.DeviceFlowError):
        deviceflow.start(server([body], []), NOW)


def test_poll_states_until_token():
    code = deviceflow.DeviceCode(
        "dev", "U", "https://github.com/login/device", 5, NOW + timedelta(minutes=15)
    )
    seen = []
    responses = [
        {"error": "authorization_pending"},
        {"error": "slow_down", "interval": 10},
        {"error": "slow_down"},
        {
            "access_token": "gho_new",
            "expires_in": 28800,
            "refresh_token": "r",
            "refresh_token_expires_in": 100,
        },
    ]
    client = server(responses, seen)
    assert deviceflow.poll(client, code, NOW).state == "pending"
    assert deviceflow.poll(client, code, NOW) == deviceflow.Poll("slow_down", interval=10)
    assert deviceflow.poll(client, code, NOW).interval == 10  # 간격이 없으면 5초 늘린다
    done = deviceflow.poll(client, code, NOW)
    assert done.state == "done" and done.token.access_token == "gho_new"
    assert done.token.expires_at == NOW + timedelta(hours=8)
    assert seen[0][1] == {
        "client_id": CLIENT_ID,
        "device_code": "dev",
        "grant_type": deviceflow.GRANT,
    }


def test_poll_end_states():
    code = deviceflow.DeviceCode("dev", "U", "u", 5, NOW + timedelta(minutes=1))
    assert deviceflow.poll(server([], []), code, NOW + timedelta(minutes=2)).state == "expired"
    assert deviceflow.poll(server([{"error": "expired_token"}], []), code, NOW).state == "expired"
    assert deviceflow.poll(server([{"error": "access_denied"}], []), code, NOW).state == "denied"
    with pytest.raises(deviceflow.DeviceFlowError):
        deviceflow.poll(server([{"error": "incorrect_client_credentials"}], []), code, NOW)
