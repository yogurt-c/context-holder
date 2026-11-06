"""기기 이름, GitHub 토큰 · 계정, 피드백 파일."""

import json
import subprocess
from datetime import UTC, datetime

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
    monkeypatch.setattr(
        device.subprocess, "run", lambda *a, **k: completed("Team's Mac\n")
    )
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


def test_token_from_keychain_first(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command[0])
        return completed("from-keychain\n")

    monkeypatch.setattr(auth.subprocess, "run", run)
    assert auth.github_token() == "from-keychain"
    assert calls == ["security"]


def test_token_falls_back_to_gh(monkeypatch):
    def run(command, **kwargs):
        if command[0] == "security":
            return completed("", code=44)  # 키체인에 없다
        return completed("gho_x\n")

    monkeypatch.setattr(auth.subprocess, "run", run)
    assert auth.github_token() == "gho_x"


def test_token_missing(monkeypatch):
    def run(command, **kwargs):
        if command[0] == "gh":
            raise FileNotFoundError("gh")
        return completed("")

    monkeypatch.setattr(auth.subprocess, "run", run)
    with pytest.raises(auth.TokenMissing):
        auth.github_token()


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
