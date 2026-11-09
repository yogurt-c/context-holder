"""Inbox 화면 (plan 7장). 관련도 0.3~0.6 보류 글을 사용자가 저장이나 관련없음으로 고른다.

- 저장: 글 파일을 inbox/에서 wiki/로 옮기고 피드백 `save`를 남긴다.
- 관련없음: 피드백 `not_relevant`를 남기고 글 파일을 지운다. 본 글 기록이 남아 다시 모으지 않는다.
- 첫 실행(origin: init)에서 온 보류 글은 "초기 수집 보류"로 따로 묶어 한꺼번에 정리할 수 있다.
인덱스는 해당 행만 고친다. 위키 변경은 다음 수집이 커밋하고 push한다.
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from context_holder.classify import Bucket
from context_holder.feedback import Kind, record_feedback
from context_holder.index import Index, IndexedArticle
from context_holder.screens.views import local_day
from context_holder.wiki import StoredArticle, Wiki

INIT = "init"


@dataclass(frozen=True)
class InboxItem:
    article: IndexedArticle
    company: str
    day: date | None


@dataclass(frozen=True)
class InboxView:
    recent: list[InboxItem]  # 매일 수집에서 보류한 글
    init: list[InboxItem]  # 첫 실행에서 보류한 글
    selected: StoredArticle | None
    company: str
    next_id: str | None  # "나중에"로 넘어갈 글

    @property
    def total(self) -> int:
        return len(self.recent) + len(self.init)

    @property
    def selected_in_init(self) -> bool:
        return self.selected is not None and self.selected.origin == INIT


def inbox_items(index: Index, names: dict[str, str]) -> tuple[list[InboxItem], list[InboxItem]]:
    rows = index.select("WHERE bucket = ?", (Bucket.INBOX.value,))
    items = [
        InboxItem(a, names.get(a.source_id, a.source_id), local_day(a.collected)) for a in rows
    ]
    recent = sorted(
        (i for i in items if i.article.origin != INIT),
        key=lambda i: (i.article.collected or "", i.article.relevance or 0.0),
        reverse=True,
    )
    init = sorted(
        (i for i in items if i.article.origin == INIT),
        key=lambda i: (-(i.article.relevance or 0.0), i.article.title),
    )
    return recent, init


def build_inbox(wiki: Wiki, index: Index, names: dict[str, str], sel: str | None) -> InboxView:
    recent, init = inbox_items(index, names)
    order = [i.article for i in [*recent, *init]]
    chosen = next((a for a in order if a.id == sel), order[0] if order else None)
    stored = wiki.read(Path(chosen.path)) if chosen else None
    return InboxView(
        recent,
        init,
        stored,
        names.get(stored.source_id, stored.source_id) if stored else "",
        neighbor(order, chosen.id) if chosen else None,
    )


def neighbor(order: list[IndexedArticle], current: str) -> str | None:
    """다음 글. 마지막이면 앞 글."""
    ids = [a.id for a in order]
    if current not in ids:
        return ids[0] if ids else None
    n = ids.index(current)
    rest = ids[n + 1 :] or ids[:n][-1:]
    return rest[0] if rest else None


def decide(
    wiki: Wiki, index: Index, device: str, article: IndexedArticle, keep: bool, when: datetime
) -> None:
    path = Path(article.path)
    if keep:
        moved = wiki.save_from_inbox(path)
        stored = wiki.read(moved)
        assert stored is not None
        index.move(article.id, stored)
        record_feedback(wiki, device, Kind.SAVE, article.url, when)
    else:
        record_feedback(wiki, device, Kind.NOT_RELEVANT, article.url, when)
        wiki.remove_article(path, article.id)
        index.remove(article.id)


def dismiss_init(wiki: Wiki, index: Index, device: str, when: datetime) -> int:
    """초기 수집 보류 글을 모두 관련없음으로 고른다. 고른 수를 돌려준다."""
    _, init = inbox_items(index, {})
    for item in init:
        decide(wiki, index, device, item.article, keep=False, when=when)
    return len(init)


def pending_inbox(index: Index, article_id: str) -> IndexedArticle | None:
    """고를 수 있는 보류 글. 이미 골랐거나 파일이 사라졌으면 None."""
    article = index.get(article_id)
    if article is None or article.bucket is not Bucket.INBOX or not Path(article.path).exists():
        return None
    return article
