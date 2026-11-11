"""Bookmarks: 북마크한 글을 최근에 북마크한 순서로 (글 상세 상단의 책갈피 아이콘으로 모은다).

PAGE_SIZE건씩 커서("더 보기")로 붙인다. 커서는 그 쪽 마지막 글 id다.
"""

from dataclasses import dataclass

from context_holder.feedback import bookmarked, load_feedback
from context_holder.index import Index, IndexedArticle
from context_holder.screens.views import Page, after_cursor
from context_holder.wiki import Wiki

PAGE_SIZE = 20


@dataclass(frozen=True)
class BookmarkRow:
    article: IndexedArticle
    company: str


def build_bookmarks(wiki: Wiki, index: Index, names: dict[str, str]) -> Page:
    rows = _rows(wiki, index, names)
    shown = rows[:PAGE_SIZE]
    return Page(shown, shown[-1].article.id if len(rows) > PAGE_SIZE else None)


def bookmarks_more(wiki: Wiki, index: Index, names: dict[str, str], after: str) -> Page:
    return after_cursor(_rows(wiki, index, names), after, PAGE_SIZE, lambda r: r.article.id)


def _rows(wiki: Wiki, index: Index, names: dict[str, str]) -> list[BookmarkRow]:
    """인덱스에 없는 글 (다른 기기의 pull로 지워진 글 등)은 건너뛴다."""
    found = (index.get(aid) for aid in bookmarked(load_feedback(wiki)))
    return [BookmarkRow(a, names.get(a.source_id, a.source_id)) for a in found if a is not None]
