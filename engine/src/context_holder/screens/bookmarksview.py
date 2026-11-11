"""Bookmarks: 북마크한 글을 최근에 북마크한 순서로 (글 상세 상단의 책갈피 아이콘으로 모은다).

PAGE_SIZE건씩 값 커서("더 보기")로 붙인다. 커서는 그 쪽 마지막 글의 (북마크 시각, 글 id)이고,
다음 쪽은 그 값보다 작은 북마크다. 커서 글을 찾지 않으니 화면에서 그 글의 북마크를 끄거나
다시 켜도, 지워져도 빠지거나 겹치지 않는다. 시각이 같으면 글 id로 순서가 정해진다.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime

from context_holder.feedback import Bookmark, bookmarks, load_feedback
from context_holder.index import Index, IndexedArticle
from context_holder.screens.views import Page
from context_holder.wiki import Wiki

PAGE_SIZE = 20


@dataclass(frozen=True)
class BookmarkRow:
    article: IndexedArticle
    company: str
    cursor: str  # 이 글 뒤를 부를 때 넘길 값


def build_bookmarks(wiki: Wiki, index: Index, names: dict[str, str]) -> Page:
    return _page(_rows(index, names, bookmarks(load_feedback(wiki))))


def bookmarks_more(wiki: Wiki, index: Index, names: dict[str, str], after: str) -> Page | None:
    """after 다음 쪽. 커서 형식이 틀리면 None."""
    key = decode_cursor(after)
    if key is None:
        return None
    older = [b for b in bookmarks(load_feedback(wiki)) if b.key < key]
    return _page(_rows(index, names, older))


def encode_cursor(b: Bookmark) -> str:
    raw = f"{b.at.isoformat()}|{b.article_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, str] | None:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        at, aid = raw.split("|", 1)
        when = datetime.fromisoformat(at)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if when.tzinfo is None or not aid:
        return None
    return (when, aid)


def _page(rows: list[BookmarkRow]) -> Page:
    shown = rows[:PAGE_SIZE]
    return Page(shown, shown[-1].cursor if len(rows) > PAGE_SIZE else None)


def _rows(index: Index, names: dict[str, str], marks: list[Bookmark]) -> list[BookmarkRow]:
    """인덱스에 없는 글 (다른 기기의 pull로 지워진 글 등)은 건너뛴다."""
    rows = []
    for b in marks:
        article = index.get(b.article_id)
        if article is not None:
            company = names.get(article.source_id, article.source_id)
            rows.append(BookmarkRow(article, company, encode_cursor(b)))
    return rows
