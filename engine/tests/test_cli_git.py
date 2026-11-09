"""git 레포인 위키에서 ctx connect · pull · sync · init · feedback."""

import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from context_holder import cli
from context_holder.cli import app
from context_holder.github import Account, GitHubError, WikiRemote
from context_holder.gitsync import GitSyncError, WikiRepo
from context_holder.init import InitResult, Progress
from context_holder.runlock import INIT_LOCK, RunLock, daily_lock
from context_holder.sync import SyncReport

runner = CliRunner()


@pytest.fixture(autouse=True)
def device_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_HOLDER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "device_name", lambda home: "air")


@pytest.fixture
def github(monkeypatch, remote):
    """GitHub 대신 로컬 bare 레포를 위키 원격으로 돌려준다."""
    created = {"created": True}
    monkeypatch.setattr(cli, "_token", lambda: "token")
    monkeypatch.setattr(
        cli, "account", lambda client, token: Account("me", "Me", "1+me@users.noreply.github.com")
    )
    monkeypatch.setattr(
        cli, "find_or_create_wiki", lambda client, token, login: WikiRemote(remote, **created)
    )
    return created


def report(**kw) -> SyncReport:
    values = dict(
        sources=(),
        saved=0,
        inboxed=0,
        dropped=0,
        pending_classify=0,
        pending_enrich=0,
        completed=0,
        still_pending=0,
        cost_usd=0.0,
        etags={},
        stopped_reason=None,
    )
    return SyncReport(**{**values, **kw})


def connected(tmp_path, github) -> WikiRepo:
    result = runner.invoke(app, ["connect", "--wiki", str(tmp_path / "wiki")])
    assert result.exit_code == 0, result.output
    return WikiRepo.open(tmp_path / "wiki")


# ---------- connect ----------


def test_connect_creates_and_starts_the_wiki(tmp_path, github, remote, make_device):
    result = runner.invoke(app, ["connect", "--wiki", str(tmp_path / "wiki")])

    assert result.exit_code == 0, result.output
    assert "me/context-wiki를 만들었다 (private)" in result.output
    other = make_device("other")
    other.pull()
    assert (other.root / "config/sources.yaml").exists()  # 기본 설정이 원격에 올라갔다
    author = other.repo[other.repo.head.target].author
    assert author.email == "1+me@users.noreply.github.com"  # 전역 git 설정이 없어 noreply


def test_connect_existing_wiki_on_a_new_device(tmp_path, github, make_device):
    first = make_device("first")
    (first.root / "wiki").mkdir()
    (first.root / "wiki/a.md").write_text("a")
    first.commit_all("a")
    first.push()
    github["created"] = False

    result = runner.invoke(app, ["connect", "--wiki", str(tmp_path / "new")])

    assert result.exit_code == 0, result.output
    assert "찾았다" in result.output
    assert (tmp_path / "new/wiki/a.md").exists()
    assert not (tmp_path / "new/config").exists()  # 이미 있는 위키에는 설정을 넣지 않는다


def test_connect_again_just_pulls(tmp_path, github):
    connected(tmp_path, github)
    result = runner.invoke(app, ["connect", "--wiki", str(tmp_path / "wiki")])
    assert result.exit_code == 0, result.output


def test_connect_refuses_folder_with_other_files(tmp_path, github):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki/notes.txt").write_text("x")
    result = runner.invoke(app, ["connect", "--wiki", str(tmp_path / "wiki")])
    assert result.exit_code == 2
    assert "다른 파일" in result.output


def test_connect_refuses_repo_with_other_remote(tmp_path, github):
    WikiRepo.create(tmp_path / "wiki", str(tmp_path / "elsewhere.git"))
    result = runner.invoke(app, ["connect", "--wiki", str(tmp_path / "wiki")])
    assert result.exit_code == 2
    assert "다른 레포" in result.output


def test_connect_github_error(tmp_path, github, monkeypatch):
    def fail(client, token):
        raise GitHubError("토큰이 만료됐다")

    monkeypatch.setattr(cli, "account", fail)
    result = runner.invoke(app, ["connect", "--wiki", str(tmp_path / "wiki")])
    assert result.exit_code == 1
    assert "만료" in result.output


def test_connect_git_error(tmp_path, github, monkeypatch):
    def fail(self):
        raise GitSyncError("offline")

    monkeypatch.setattr(WikiRepo, "pull", fail)
    result = runner.invoke(app, ["connect", "--wiki", str(tmp_path / "wiki")])
    assert result.exit_code == 1
    assert "받지 못했다" in result.output


def test_https_wiki_needs_a_token(tmp_path, monkeypatch):
    WikiRepo.create(tmp_path / "wiki", "https://github.com/me/context-wiki.git")
    monkeypatch.setattr(cli, "github_token", lambda: "secret")
    assert cli._open_repo(tmp_path / "wiki").token == "secret"

    def missing():
        raise cli.TokenMissing("로그인하지 않았다")

    monkeypatch.setattr(cli, "github_token", missing)
    result = runner.invoke(app, ["pull", "--wiki", str(tmp_path / "wiki")])
    assert result.exit_code == 2
    assert "로그인하지 않았다" in result.output


# ---------- pull ----------


def test_pull_needs_a_connected_wiki(tmp_path):
    result = runner.invoke(app, ["pull", "--wiki", str(tmp_path / "plain")])
    assert result.exit_code == 2
    assert "ctx connect" in result.output


def test_pull_indexes_what_other_devices_wrote(tmp_path, github, make_device, monkeypatch):
    repo = connected(tmp_path, github)
    other = make_device("other")
    other.pull()
    (other.root / "wiki").mkdir()
    (other.root / "wiki/a.md").write_text("a")
    other.commit_all("a")
    other.push()
    (repo.root / "feedback").mkdir()
    (repo.root / "feedback/x.jsonl").write_text("{}")  # 지난번에 못 올린 변경
    synced = []
    monkeypatch.setattr(cli, "_index", lambda: FakeIndex(synced, (3, 1)))

    result = runner.invoke(app, ["pull", "--wiki", str(repo.root)])

    assert result.exit_code == 0, result.output
    assert (repo.root / "wiki/a.md").exists()
    assert "인덱스 갱신 3건, 삭제 1건" in result.output
    assert synced == [repo.root]
    other.pull()
    assert (other.root / "feedback/x.jsonl").exists()  # 남은 변경도 올라갔다


def test_pull_reports_conflicts_and_offline(tmp_path, github, monkeypatch):
    from context_holder.gitsync import PullResult

    repo = connected(tmp_path, github)
    monkeypatch.setattr(cli, "_index", lambda: FakeIndex([], (0, 0)))
    monkeypatch.setattr(WikiRepo, "pull", lambda self: PullResult(changed=True, dropped=2))
    result = runner.invoke(app, ["pull", "--wiki", str(repo.root)])
    assert "원격 내용을 따랐다" in result.output

    def offline(self):
        raise GitSyncError("연결 안 됨")

    monkeypatch.setattr(WikiRepo, "pull", offline)
    result = runner.invoke(app, ["pull", "--wiki", str(repo.root)])
    assert result.exit_code == 1
    assert "동기화하지 못했다" in result.output


class FakeIndex:
    def __init__(self, log, counts):
        self.log, self.counts = log, counts

    def sync(self, wiki, embedder=None):
        self.log.append(wiki.root)
        return self.counts


# ---------- sync ----------


def test_sync_takes_the_lock_and_pushes_results(tmp_path, github, make_device, monkeypatch):
    repo = connected(tmp_path, github)
    monkeypatch.setattr(cli, "_index", lambda: FakeIndex([], (0, 0)))

    def fake_run_sync(wiki, client, llm, now, days, etags=None, embedder=None, checkpoint=None):
        for i in range(3):
            (wiki.root / "wiki").mkdir(exist_ok=True)
            (wiki.root / f"wiki/{i}.md").write_text("x")
            checkpoint()
        return report(saved=3, pending_enrich=1)

    monkeypatch.setattr(cli, "run_sync", fake_run_sync)
    result = runner.invoke(app, ["sync", "--wiki", str(repo.root)])
    assert result.exit_code == 0, result.output

    other = make_device("other")
    other.pull()
    lock = json.loads((other.root / f"state/runs/{daily_lock(datetime.now(UTC))}.json").read_text())
    assert lock["device"] == "air"
    assert lock["status"] == "done"
    assert lock["result"] == {
        "saved": 3,
        "inboxed": 0,
        "dropped": 0,
        "pending": 1,
        "sources": 0,
        "failed_sources": 0,
        "cost_usd": 0.0,
    }
    assert len(list((other.root / "wiki").iterdir())) == 3

    again = runner.invoke(app, ["sync", "--wiki", str(repo.root)])
    assert again.exit_code == 0
    assert "오늘 수집은 이미 끝났다 (air)" in again.output


def test_sync_stopped_by_limit_releases_the_lock(tmp_path, github, monkeypatch):
    repo = connected(tmp_path, github)
    monkeypatch.setattr(cli, "_index", lambda: FakeIndex([], (0, 0)))
    monkeypatch.setattr(cli, "run_sync", lambda *a, **k: report(stopped_reason="limit: 한도"))
    runner.invoke(app, ["sync", "--wiki", str(repo.root)])
    lock = json.loads((repo.root / f"state/runs/{daily_lock(datetime.now(UTC))}.json").read_text())
    assert lock["status"] == "stopped"


def test_sync_waits_for_another_device(tmp_path, github, make_device, monkeypatch):
    repo = connected(tmp_path, github)
    other = make_device("studio")
    other.pull()
    RunLock(other, INIT_LOCK, "studio", lambda: datetime.now(UTC)).acquire()
    monkeypatch.setattr(cli, "_index", lambda: FakeIndex([], (0, 0)))
    monkeypatch.setattr(cli, "run_sync", lambda *a, **k: pytest.fail("수집하면 안 된다"))

    result = runner.invoke(app, ["sync", "--wiki", str(repo.root)])

    assert result.exit_code == 0, result.output
    assert "다른 기기(studio)가 수집하는 중이다" in result.output


def test_sync_lock_messages(tmp_path, github, monkeypatch):
    from context_holder.collectrun import LockedRun
    from context_holder.runlock import LockStatus, Outcome

    repo = connected(tmp_path, github)
    monkeypatch.setattr(cli, "_index", lambda: FakeIndex([], (0, 0)))
    taken = LockedRun(LockStatus(Outcome.ACQUIRED, {}, took_over="studio"), None, "offline")
    monkeypatch.setattr(cli, "run_locked", lambda lock, work, blockers: taken)
    result = runner.invoke(app, ["sync", "--wiki", str(repo.root)])
    assert "멈춘 수집(studio)을 이어받았다" in result.output
    assert "push하지 못했다" in result.output

    def fail(lock, work, blockers):
        raise GitSyncError("offline")

    monkeypatch.setattr(cli, "run_locked", fail)
    result = runner.invoke(app, ["sync", "--wiki", str(repo.root)])
    assert result.exit_code == 1
    assert "수집 잠금을 잡지 못했다" in result.output


# ---------- init ----------


def test_init_uses_its_own_lock_and_pushes_every_batch(tmp_path, github, make_device, monkeypatch):
    repo = connected(tmp_path, github)
    monkeypatch.setattr(cli, "_index", lambda: FakeIndex([], (0, 0)))
    beats = []
    real = RunLock.heartbeat
    monkeypatch.setattr(RunLock, "heartbeat", lambda self, m: beats.append(m) or real(self, m))

    def fake_run_init(wiki, client, llm, now, settings, on_progress, embedder=None):
        on_progress(Progress(total=40, remaining=20, counts={"save": 20}))
        on_progress(Progress(total=40, remaining=0, counts={"save": 40}))
        return InitResult(True, 40, 0, 0, None, 1.0)

    monkeypatch.setattr(cli, "run_init", fake_run_init)
    result = runner.invoke(app, ["init", "--wiki", str(repo.root)])

    assert result.exit_code == 0, result.output
    assert "초기 수집을 마쳤다" in result.output
    assert len(beats) == 2
    other = make_device("other")
    other.pull()
    lock = json.loads((other.root / "state/runs/init.json").read_text())
    assert lock["status"] == "done"
    assert lock["result"] == {"saved": 40, "inboxed": 0, "dropped": 0}


# ---------- feedback ----------


def test_feedback_on_plain_folder(tmp_path):
    result = runner.invoke(
        app, ["feedback", "bookmark", "https://a.example/x", "--wiki", str(tmp_path / "w")]
    )
    assert result.exit_code == 0, result.output
    assert len(list((tmp_path / "w/feedback").glob("*-air.jsonl"))) == 1


def test_feedback_is_pushed(tmp_path, github, make_device, monkeypatch):
    repo = connected(tmp_path, github)
    result = runner.invoke(
        app, ["feedback", "not_relevant", "https://a.example/x", "--wiki", str(repo.root)]
    )
    assert result.exit_code == 0, result.output
    other = make_device("other")
    other.pull()
    assert len(list((other.root / "feedback").glob("*-air.jsonl"))) == 1

    def offline(self):
        raise GitSyncError("offline")

    monkeypatch.setattr(WikiRepo, "push", offline)
    result = runner.invoke(
        app, ["feedback", "bookmark", "https://a.example/y", "--wiki", str(repo.root)]
    )
    assert result.exit_code == 0
    assert "push하지 못했다" in result.output
