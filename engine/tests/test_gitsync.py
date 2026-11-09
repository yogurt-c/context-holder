import pygit2
import pytest

from context_holder.gitsync import GitSyncError, PushRejected, WikiRepo


def write(repo: WikiRepo, name: str, text: str) -> None:
    path = repo.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def files(repo: WikiRepo) -> set[str]:
    return {str(p.relative_to(repo.root)) for p in repo.root.rglob("*") if p.is_file()} - {
        str(p.relative_to(repo.root)) for p in (repo.root / ".git").rglob("*")
    }


def history(repo: WikiRepo) -> list[str]:
    return [c.message for c in repo.repo.walk(repo.repo.head.target)]


def test_open_returns_none_for_plain_folder(tmp_path):
    assert WikiRepo.open(tmp_path) is None


def test_open_existing_repo(make_device):
    device = make_device("a")
    opened = WikiRepo.open(device.root, token="t")
    assert opened is not None
    assert opened.root == device.root
    assert opened.token == "t"


def test_commit_all_stages_new_changed_and_deleted_files(make_device):
    a = make_device("a")
    write(a, "wiki/x.md", "1")
    write(a, "wiki/y.md", "1")
    assert a.commit_all("first")
    assert not a.commit_all("nothing")  # 바뀐 게 없으면 커밋하지 않는다

    (a.root / "wiki/y.md").unlink()
    write(a, "wiki/x.md", "2")
    assert a.commit_all("second")
    tree = a.repo[a.repo.head.target].tree
    assert [e.name for e in tree["wiki"].peel(pygit2.Tree)] == ["x.md"]
    assert a.is_clean()


def test_pull_from_empty_remote_changes_nothing(make_device):
    a = make_device("a")
    assert not a.commit_all("empty")  # 파일이 없으면 빈 첫 커밋을 만들지 않는다
    assert not a.pull().changed
    a.push()  # 아직 커밋이 없으면 아무것도 하지 않는다
    assert a.head() is None
    assert a.ahead() == 0


def test_new_device_receives_everything(make_device):
    a = make_device("a")
    write(a, "wiki/x.md", "1")
    a.commit_all("first")
    assert a.ahead() == 1  # 원격에 아직 없다
    a.push()
    assert a.ahead() == 0

    b = make_device("b")
    assert b.pull().changed
    assert files(b) == {"wiki/x.md"}
    assert b.head() == a.head()


def test_fast_forward_brings_deletions(make_device):
    a, b = make_device("a"), make_device("b")
    write(a, "wiki/x.md", "1")
    write(a, "wiki/y.md", "1")
    a.commit_all("first")
    a.push()
    b.pull()

    (a.root / "wiki/y.md").unlink()
    a.commit_all("remove y")
    a.push()
    result = b.pull()

    assert result.changed and result.dropped == 0
    assert files(b) == {"wiki/x.md"}
    assert not b.pull().changed  # 이미 최신


def test_diverged_devices_replay_local_commits_on_top(make_device):
    a, b = make_device("a"), make_device("b")
    write(a, "config/sources.yaml", "x")
    a.commit_all("start")
    a.push()
    b.pull()

    write(a, "wiki/a.md", "a")
    a.commit_all("a writes")
    a.push()
    write(b, "feedback/2026-11-b.jsonl", "b")
    b.commit_all("b writes")

    result = b.pull()
    assert result.changed and result.dropped == 0
    assert files(b) == {"config/sources.yaml", "wiki/a.md", "feedback/2026-11-b.jsonl"}
    assert history(b) == ["b writes", "a writes", "start"]  # 한 줄 히스토리
    assert all(len(c.parents) <= 1 for c in b.repo.walk(b.repo.head.target))
    assert b.repo[b.repo.head.target].author.name == "b"
    assert b.pull().changed is False  # 로컬이 앞서 있다
    b.push()
    a.pull()
    assert files(a) == files(b)


def test_conflict_keeps_remote_version(make_device):
    a, b = make_device("a"), make_device("b")
    write(a, "state/runs/today.json", "start")
    a.commit_all("start")
    a.push()
    b.pull()

    write(a, "state/runs/today.json", "a")
    a.commit_all("a")
    a.push()
    write(b, "state/runs/today.json", "b")
    b.commit_all("b")

    result = b.pull()
    assert result.dropped == 1
    assert (b.root / "state/runs/today.json").read_text() == "a"
    assert history(b) == ["a", "start"]


def test_unrelated_histories_are_joined(make_device):
    a, b = make_device("a"), make_device("b")
    write(a, "wiki/a.md", "a")
    a.commit_all("a")
    a.push()
    write(b, "wiki/b.md", "b")
    b.commit_all("b")  # 원격을 받기 전에 따로 시작했다

    b.pull()
    assert files(b) == {"wiki/a.md", "wiki/b.md"}
    assert history(b) == ["b", "a"]


def test_pull_refuses_uncommitted_changes(make_device):
    a = make_device("a")
    write(a, "wiki/x.md", "1")
    with pytest.raises(GitSyncError, match="커밋하지 않은"):
        a.pull()


def test_push_retries_after_another_device_pushed(make_device, monkeypatch):
    a, b = make_device("a"), make_device("b")
    write(a, "wiki/a.md", "a")
    a.commit_all("a")
    a.push()
    b.pull()
    write(a, "wiki/a2.md", "a")
    a.commit_all("a2")
    a.push()
    write(b, "wiki/b.md", "b")
    b.commit_all("b")

    b.push()  # 첫 시도는 거절되고, pull 후 다시 올린다
    a.pull()
    assert files(a) == {"wiki/a.md", "wiki/a2.md", "wiki/b.md"}


def test_push_gives_up_after_attempts(make_device, monkeypatch):
    a = make_device("a")
    write(a, "wiki/a.md", "a")
    a.commit_all("a")
    monkeypatch.setattr(a, "_try_push", lambda: False)
    monkeypatch.setattr(a, "pull", lambda: None)
    with pytest.raises(PushRejected):
        a.push()


def test_push_failure_that_is_not_a_rejection(make_device, monkeypatch):
    a = make_device("a")
    write(a, "wiki/a.md", "a")
    a.commit_all("a")
    a.push()
    monkeypatch.setattr(a, "_behind_remote", lambda: False)
    a.repo.remotes.set_push_url("origin", str(a.root / "missing.git"))
    write(a, "wiki/b.md", "b")
    a.commit_all("b")
    with pytest.raises(GitSyncError, match="push 실패"):
        a.push()


def test_behind_remote_is_false_before_first_push(make_device):
    a = make_device("a")
    write(a, "wiki/a.md", "a")
    a.commit_all("a")
    assert a._behind_remote() is False


def test_unreachable_remote(tmp_path):
    repo = WikiRepo.create(tmp_path / "w", str(tmp_path / "missing.git"))
    with pytest.raises(GitSyncError, match="연결하지 못했다"):
        repo.pull()


def test_token_is_sent_as_credentials(make_device):
    a = make_device("a")
    assert a._callbacks() is None
    a.token = "secret"
    assert isinstance(a._callbacks(), pygit2.RemoteCallbacks)


def test_token_failure_is_a_sync_error(make_device):
    """토큰을 못 받아도 수집이 죽지 않게, 네트워크 실패처럼 다음 실행으로 미룬다."""
    a = make_device("a")
    write(a, "wiki/a.md", "a")
    a.commit_all("a")

    def missing() -> str:
        raise RuntimeError("로그인하지 않았다")

    a.token = missing
    with pytest.raises(GitSyncError, match="GitHub 토큰을 받지 못했다: 로그인하지 않았다"):
        a.push()
    with pytest.raises(GitSyncError, match="GitHub 토큰을 받지 못했다"):
        a.pull()


def test_identity_comes_from_repo_config(tmp_path, remote):
    repo = WikiRepo.create(tmp_path / "w", remote)
    assert not repo.has_identity()  # 전역 git 설정이 없는 Mac
    repo.set_identity("hyungi", "1+hyungi@users.noreply.github.com")
    assert repo.has_identity()
    assert repo.remote_url == remote
