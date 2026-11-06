"""위키 레포를 pygit2로 pull · commit · push한다 (plan 3.1).

시스템 git 없이 앱에 넣은 libgit2로 동작한다. HTTPS와 GitHub 토큰으로 통신한다.
pull은 `git pull --rebase`처럼 동작한다: 로컬 커밋을 원격 최신 커밋 위에 하나씩 다시 얹는다.
같은 파일이 충돌하면 원격 내용을 우선한다. 로컬 변경은 다음 실행에서 다시 만든다.
"""

from dataclasses import dataclass
from pathlib import Path

import pygit2
from pygit2.enums import CheckoutStrategy, MergeFavor

BRANCH = "main"
REMOTE = "origin"
PUSH_ATTEMPTS = 3


class GitSyncError(Exception):
    """원격과 통신하지 못했다 (오프라인, 인증 실패 등)."""


class PushRejected(GitSyncError):
    """다시 pull해도 다른 기기의 push에 계속 밀렸다."""


@dataclass(frozen=True)
class PullResult:
    changed: bool  # 원격에서 새 커밋을 받아 작업 폴더가 바뀌었다
    dropped: int  # 원격 내용과 같아져 버린 로컬 커밋 수 (충돌에서 원격 우선)


class WikiRepo:
    def __init__(self, repo: pygit2.Repository, token: str | None = None):
        self.repo = repo
        self.token = token

    # ---------- 열기 ----------

    @classmethod
    def open(cls, path: Path, token: str | None = None) -> "WikiRepo | None":
        """위키 폴더가 git 레포가 아니면 None. 로컬 폴더만 쓰는 개발 모드다."""
        if not (path / ".git").exists():
            return None
        return cls(pygit2.Repository(str(path)), token)

    @classmethod
    def create(cls, path: Path, url: str, token: str | None = None) -> "WikiRepo":
        """빈 레포를 만들고 원격을 연결한다. 내용은 pull로 받는다 (빈 원격도 같은 흐름)."""
        repo = pygit2.init_repository(str(path), initial_head=BRANCH)
        repo.remotes.create(REMOTE, url)
        return cls(repo, token)

    @property
    def root(self) -> Path:
        return Path(self.repo.workdir)

    @property
    def remote_url(self) -> str:
        return self.repo.remotes[REMOTE].url

    def set_identity(self, name: str, email: str) -> None:
        """전역 git 설정에 저자가 없을 때만 이 레포의 저자를 정한다."""
        self.repo.config["user.name"] = name
        self.repo.config["user.email"] = email

    def has_identity(self) -> bool:
        try:
            self.repo.default_signature  # noqa: B018 - 설정이 없으면 예외
        except (KeyError, pygit2.GitError):
            return False
        return True

    # ---------- 커밋 ----------

    def commit_all(self, message: str) -> bool:
        """작업 폴더의 모든 변경(삭제 포함)을 커밋한다. 바뀐 게 없으면 False."""
        index = self.repo.index
        index.add_all()
        index.write()
        tree = index.write_tree()
        parents = [] if self.repo.head_is_unborn else [self.repo.head.target]
        if parents and self.repo[parents[0]].tree_id == tree:
            return False
        if not parents and len(index) == 0:  # 첫 커밋인데 파일이 없다
            return False
        sig = self.repo.default_signature
        self.repo.create_commit("HEAD", sig, sig, message, tree, parents)
        return True

    def head(self) -> str | None:
        return None if self.repo.head_is_unborn else str(self.repo.head.target)

    def is_clean(self) -> bool:
        return not self.repo.status()

    # ---------- pull ----------

    def pull(self) -> PullResult:
        if not self.is_clean():
            raise GitSyncError("커밋하지 않은 변경이 있어 pull하지 않는다")
        self._fetch()
        ref = self.repo.references.get(f"refs/remotes/{REMOTE}/{BRANCH}")
        if ref is None:  # 아직 아무것도 push되지 않은 빈 원격
            return PullResult(changed=False, dropped=0)
        theirs = ref.target
        if self.repo.head_is_unborn:
            self._move_to(theirs)
            return PullResult(changed=True, dropped=0)
        ours = self.repo.head.target
        base = self.repo.merge_base(ours, theirs)
        if ours == theirs or base == theirs:  # 같거나 로컬이 앞서 있다
            return PullResult(changed=False, dropped=0)
        if base == ours:  # 로컬 커밋이 없다: 빨리 감기
            self._move_to(theirs)
            return PullResult(changed=True, dropped=0)
        tip, dropped = self._replay(base, ours, theirs)
        self._move_to(tip)
        return PullResult(changed=True, dropped=dropped)

    def _replay(self, base, ours, theirs) -> tuple[pygit2.Oid, int]:
        local = []
        for commit in self.repo.walk(ours):
            if commit.id == base:
                break
            local.append(commit)
        tip, dropped = theirs, 0
        for commit in reversed(local):
            parent_tree = commit.parents[0].tree if commit.parents else None
            merged = self.repo.merge_trees(
                parent_tree or self._empty_tree(),
                self.repo[tip].tree,
                commit.tree,
                favor=MergeFavor.OURS,  # 여기서 ours는 원격: 충돌하면 원격 내용이 남는다
            )
            tree = merged.write_tree(self.repo)
            if tree == self.repo[tip].tree_id:
                dropped += 1
                continue
            tip = self.repo.create_commit(
                None, commit.author, self.repo.default_signature, commit.message, tree, [tip]
            )
        return tip, dropped

    def _empty_tree(self) -> pygit2.Tree:
        return self.repo[self.repo.TreeBuilder().write()]

    def _move_to(self, target: pygit2.Oid) -> None:
        self.repo.checkout_tree(self.repo[target], strategy=CheckoutStrategy.FORCE)
        self.repo.references.create(f"refs/heads/{BRANCH}", target, force=True)
        self.repo.set_head(f"refs/heads/{BRANCH}")

    # ---------- push ----------

    def push(self) -> None:
        """push한다. 다른 기기가 먼저 push했으면 pull(rebase) 후 다시 시도한다."""
        if self.repo.head_is_unborn:
            return
        for _ in range(PUSH_ATTEMPTS):
            if self._try_push():
                return
            self.pull()
        raise PushRejected(f"{PUSH_ATTEMPTS}번 시도했지만 다른 기기의 push에 밀렸다")

    def _try_push(self) -> bool:
        try:
            self.repo.remotes[REMOTE].push([f"refs/heads/{BRANCH}"], callbacks=self._callbacks())
        except pygit2.GitError as e:
            if self._behind_remote():
                return False
            raise GitSyncError(f"push 실패: {e}") from e
        return True

    def _behind_remote(self) -> bool:
        """원격에 로컬에 없는 커밋이 있는지. push가 거절된 이유를 가린다."""
        self._fetch()
        ref = self.repo.references.get(f"refs/remotes/{REMOTE}/{BRANCH}")
        if ref is None:
            return False
        ours = self.repo.head.target
        return self.repo.merge_base(ours, ref.target) != ref.target

    def ahead(self) -> int:
        """아직 push하지 않은 로컬 커밋 수 (오프라인일 때 쌓인다)."""
        if self.repo.head_is_unborn:
            return 0
        ref = self.repo.references.get(f"refs/remotes/{REMOTE}/{BRANCH}")
        if ref is None:
            return sum(1 for _ in self.repo.walk(self.repo.head.target))
        ahead, _ = self.repo.ahead_behind(self.repo.head.target, ref.target)
        return ahead

    def _fetch(self) -> None:
        try:
            self.repo.remotes[REMOTE].fetch(callbacks=self._callbacks())
        except pygit2.GitError as e:
            raise GitSyncError(f"원격에 연결하지 못했다: {e}") from e

    def _callbacks(self) -> pygit2.RemoteCallbacks | None:
        if not self.token:
            return None
        return pygit2.RemoteCallbacks(credentials=pygit2.UserPass("x-access-token", self.token))
