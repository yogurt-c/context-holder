"""GitHub 계정의 `context-wiki`를 찾아 받는다. 없으면 private으로 만든다 (plan 3.2).

`ctx connect`와 첫 실행 설정 화면이 함께 쓴다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from context_holder.github import GitHubError, account, find_or_create_wiki
from context_holder.gitsync import GitSyncError, WikiRepo
from context_holder.wiki import Wiki


class ConnectError(Exception):
    pass


@dataclass(frozen=True)
class Connected:
    login: str
    created: bool  # 레포를 새로 만들었나
    empty: bool  # 글이 하나도 없는 위키 (첫 수집이 필요하다)


def connect_wiki(
    path: Path, token: Callable[[], str], client: httpx.Client | None = None
) -> Connected:
    owned = client is None
    http = client or httpx.Client(timeout=30)
    try:
        me = account(http, token())
        remote = find_or_create_wiki(http, token(), me.login)
    except GitHubError as e:
        raise ConnectError(str(e)) from e
    finally:
        if owned:
            http.close()

    repo = WikiRepo.open(path, token)
    if repo is None:
        if path.exists() and any(path.iterdir()):
            raise ConnectError(f"{path}에 이미 다른 파일이 있다. 빈 폴더를 지정한다.")
        repo = WikiRepo.create(path, remote.url, token)
    elif repo.remote_url != remote.url:
        raise ConnectError(f"{path}는 다른 레포({repo.remote_url})에 연결돼 있다.")
    if not repo.has_identity():
        repo.set_identity(me.name, me.noreply_email)
    try:
        repo.pull()
        if repo.head() is None:  # 빈 레포: 기본 설정을 넣어 시작한다
            Wiki(path).ensure_config()
            repo.commit_all("위키 시작")
        repo.push()
    except GitSyncError as e:
        raise ConnectError(f"위키를 받지 못했다: {e}") from e
    wiki = Wiki(path)
    empty = not any(wiki.articles())
    return Connected(me.login, remote.created, empty)
