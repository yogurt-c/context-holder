"""기기 이름, GitHub 토큰 · 계정, 피드백 파일."""

import json
import subprocess
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from context_holder import auth, device
from context_holder.feedback import Kind, load_feedback, record_feedback
from context_holder.github import GitHubError, account, find_or_create_wiki
from context_holder.wiki import Wiki, article_id


def completed(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


# ---------- 기기 이름 ----------


def test_device_name_is_made_once_from_mac_name(tmp_path, monkeypatch):
    monkeypatch.setattr(device.subprocess, "run", lambda *a, **k: completed("Team's Mac\n"))
    assert device.device_name(tmp_path) == "team-s-mac"

    monkeypatch.setattr(device.subprocess, "run", lambda *a, **k: completed("Renamed"))
    assert device.device_name(tmp_path) == "team-s-mac"  # 저장한 이름을 계속 쓴다


def test_device_name_falls_back_to_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(device.socket, "gethostname", lambda: "studio.local")
    monkeypatch.setattr(device.subprocess, "run", lambda *a, **k: completed("", code=1))
    assert device.device_name(tmp_path / "a") == "studio"

    def missing(*a, **k):
        raise FileNotFoundError("scutil")

    monkeypatch.setattr(device.subprocess, "run", missing)
    assert device.device_name(tmp_path / "b") == "studio"


def test_device_name_is_never_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(device.subprocess, "run", lambda *a, **k: completed("맥북"))
    assert device.device_name(tmp_path) == "device"


# ---------- 토큰 ----------


def test_token_from_keychain_first(no_real_keychain):
    no_real_keychain.secret = "from-keychain"  # 예전 방식: 토큰 문자열만
    assert auth.github_token() == "from-keychain"


def test_token_falls_back_to_gh(monkeypatch):
    monkeypatch.setattr(auth, "gh_lookup", lambda: "gho_x")
    assert auth.github_token() == "gho_x"
    assert auth.token_source() == "gh"


def test_token_missing():
    with pytest.raises(auth.TokenMissing):
        auth.github_token()
    assert auth.token_source() is None


def test_gh_token_runs_found_gh(monkeypatch):
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        return completed("gho_y\n")

    monkeypatch.setattr(auth.subprocess, "run", run)
    monkeypatch.setattr(auth, "tool", lambda name: f"/opt/homebrew/bin/{name}")
    assert auth._gh_token() == "gho_y" and seen == [["/opt/homebrew/bin/gh", "auth", "token"]]
    monkeypatch.setattr(auth.subprocess, "run", lambda *a, **k: completed("", code=1))
    assert auth._gh_token() is None

    def missing(*a, **k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(auth.subprocess, "run", missing)
    assert auth._gh_token() is None


NOW = datetime(2026, 11, 9, 3, tzinfo=UTC)


def expiring(minutes: int, refresh="r1", refresh_days=180) -> auth.StoredToken:
    return auth.StoredToken(
        "old", NOW + timedelta(minutes=minutes), refresh, NOW + timedelta(days=refresh_days)
    )


def token_server(responses: list[dict], seen: list) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json=responses.pop(0))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fresh_token_is_used_without_refresh(no_real_keychain, tmp_path):
    no_real_keychain.secret = expiring(60).to_json()
    seen = []
    client = token_server([], seen)
    assert auth.github_token(clock=lambda: NOW, client=client, lock_path=tmp_path / "l") == "old"
    assert seen == []


def test_expiring_token_is_refreshed_and_rotated(no_real_keychain, tmp_path):
    no_real_keychain.secret = expiring(3).to_json()
    seen = []
    response = {
        "access_token": "new",
        "expires_in": 28800,
        "refresh_token": "r2",
        "refresh_token_expires_in": 15897600,
    }
    client = token_server([response], seen)

    token = auth.github_token(clock=lambda: NOW, client=client, lock_path=tmp_path / "l")

    assert token == "new"
    assert seen == [
        {"client_id": auth.CLIENT_ID, "grant_type": "refresh_token", "refresh_token": "r1"}
    ]
    stored = auth.StoredToken.from_secret(no_real_keychain.secret)
    assert stored.refresh_token == "r2" and stored.expires_at == NOW + timedelta(hours=8)


def test_refresh_skipped_when_another_process_refreshed(no_real_keychain, tmp_path):
    stale = expiring(3)
    no_real_keychain.secret = expiring(120).to_json()  # 잠금을 기다리는 사이 바뀌었다
    seen = []
    token = auth._fresh(stale, auth.Keychain(), lambda: NOW, token_server([], seen), tmp_path / "l")
    assert token == "old" and seen == []


@pytest.mark.parametrize(
    ("stored", "response"),
    [
        (expiring(-1, refresh=None), None),
        (expiring(-1, refresh_days=-1), None),
        (expiring(-1), {"error": "bad_refresh_token"}),
    ],
)
def test_refresh_failure_asks_to_log_in_again(no_real_keychain, tmp_path, stored, response):
    no_real_keychain.secret = stored.to_json()
    client = token_server([response] if response else [], [])
    with pytest.raises(auth.TokenExpired):
        auth.github_token(clock=lambda: NOW, client=client, lock_path=tmp_path / "l")


def test_refresh_network_error(no_real_keychain, tmp_path):
    no_real_keychain.secret = expiring(-1).to_json()

    def broken(request):
        raise httpx.ConnectError("offline")

    client = httpx.Client(transport=httpx.MockTransport(broken))
    with pytest.raises(auth.TokenMissing) as e:
        auth.github_token(clock=lambda: NOW, client=client, lock_path=tmp_path / "l")
    assert not isinstance(e.value, auth.TokenExpired)


def test_refresh_opens_its_own_client(no_real_keychain, tmp_path, monkeypatch):
    no_real_keychain.secret = expiring(-1).to_json()
    seen = []
    made = token_server([{"access_token": "new"}], seen)
    monkeypatch.setattr(auth.httpx, "Client", lambda **kw: made)
    assert auth.github_token(clock=lambda: NOW, lock_path=tmp_path / "l") == "new"
    assert auth.StoredToken.from_secret(no_real_keychain.secret).expires_at is None


def test_stored_token_round_trip_and_odd_secrets():
    token = expiring(10)
    assert auth.StoredToken.from_secret(token.to_json()) == token
    assert auth.StoredToken.from_secret(" plain ").access_token == "plain"
    assert auth.StoredToken.from_secret("[1]").access_token == "[1]"
    assert auth.StoredToken.from_response({"access_token": "x"}, NOW).expires_at is None


def test_save_token_and_source(no_real_keychain):
    auth.save_token(expiring(10))
    assert auth.token_source() == "app"


def test_keychain_delete_and_locked(no_real_keychain, monkeypatch):
    import keyring.errors

    chain = auth.Keychain()
    chain.write("v")
    assert chain.read() == "v"
    chain.delete()
    chain.delete()  # 없어도 괜찮다
    assert chain.read() is None

    def locked(s, a):
        raise keyring.errors.KeyringLocked("잠김")

    monkeypatch.setattr(auth.keyring, "get_password", locked)
    assert chain.read() is None


# ---------- GitHub ----------


def github(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_account_and_noreply_email():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, json={"login": "yogurt-c", "id": 7, "name": None})

    me = account(github(handler), "t")
    assert me.login == "yogurt-c"
    assert me.name == "yogurt-c"
    assert me.noreply_email == "7+yogurt-c@users.noreply.github.com"


def test_expired_token():
    with pytest.raises(GitHubError, match="만료"):
        account(github(lambda r: httpx.Response(401)), "t")


def test_other_api_failure():
    with pytest.raises(GitHubError, match="500"):
        account(github(lambda r: httpx.Response(500)), "t")


def test_finds_existing_private_wiki():
    def handler(request):
        assert request.url.path == "/repos/me/context-wiki"
        return httpx.Response(200, json={"private": True, "clone_url": "https://x/me/w.git"})

    remote = find_or_create_wiki(github(handler), "t", "me")
    assert remote.url == "https://x/me/w.git"
    assert not remote.created


def test_refuses_public_wiki():
    handler = lambda r: httpx.Response(200, json={"private": False, "clone_url": "u"})  # noqa: E731
    with pytest.raises(GitHubError, match="private"):
        find_or_create_wiki(github(handler), "t", "me")


def test_creates_private_wiki_when_missing():
    sent = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(404)
        sent.update(json.loads(request.content))
        return httpx.Response(201, json={"clone_url": "https://x/me/context-wiki.git"})

    remote = find_or_create_wiki(github(handler), "t", "me")
    assert remote.created
    assert sent["name"] == "context-wiki"
    assert sent["private"] is True


def test_create_failure():
    def handler(request):
        return httpx.Response(404 if request.method == "GET" else 422, text="exists")

    with pytest.raises(GitHubError, match="422"):
        find_or_create_wiki(github(handler), "t", "me")


# ---------- 피드백 ----------


def test_feedback_goes_to_this_device_file_and_merges_on_read(tmp_path):
    wiki = Wiki(tmp_path)
    url = "https://blog.example.com/a"
    record_feedback(wiki, "air", Kind.BOOKMARK, url, datetime(2026, 11, 6, 2, tzinfo=UTC))
    record_feedback(wiki, "studio", Kind.NOT_RELEVANT, url, datetime(2026, 11, 6, 1, tzinfo=UTC))
    record_feedback(wiki, "air", Kind.NOT_RELEVANT, url, datetime(2026, 12, 1, tzinfo=UTC))

    names = sorted(p.name for p in (tmp_path / "feedback").iterdir())
    assert names == ["2026-11-air.jsonl", "2026-11-studio.jsonl", "2026-12-air.jsonl"]
    entries = load_feedback(wiki)
    assert [(e.device, e.kind) for e in entries] == [
        ("studio", "not_relevant"),
        ("air", "bookmark"),
        ("air", "not_relevant"),
    ]
    assert entries[0].article_id == article_id(url)


def test_no_feedback_yet(tmp_path):
    assert load_feedback(Wiki(tmp_path)) == []
