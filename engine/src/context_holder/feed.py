"""RSS/Atom feed를 가져와 글 목록으로 바꾼다."""

import calendar
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from context_holder import __version__
from context_holder.sources import Source

USER_AGENT = f"context-holder/{__version__} (+https://github.com/yogurt-c/context-holder)"
TIMEOUT = httpx.Timeout(20.0)
ATTEMPTS = 3  # 5xx나 네트워크 오류는 일시적인 경우가 많다 (Medium 504 등)
RETRY_DELAY_SEC = 2.0


class FetchError(RuntimeError):
    """feed나 페이지를 가져오지 못했다."""


@dataclass(frozen=True)
class FeedEntry:
    source_id: str
    url: str
    title: str
    published: datetime | None
    html: str  # content가 있으면 content, 없으면 summary


@dataclass(frozen=True)
class FeedFetch:
    entries: tuple[FeedEntry, ...]
    etag: str | None
    not_modified: bool


def get(client: httpx.Client, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
    """GET 요청. 5xx와 네트워크 오류는 잠깐 쉬고 다시 시도한다."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = client.get(
                url,
                headers={"User-Agent": USER_AGENT, **(headers or {})},
                timeout=TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError as e:
            if attempt == ATTEMPTS:
                raise FetchError(f"{url}: {e}") from e
        else:
            if response.status_code < 500 or attempt == ATTEMPTS:
                return response
        time.sleep(RETRY_DELAY_SEC * attempt)
    raise AssertionError("unreachable")  # pragma: no cover


def fetch_feed(client: httpx.Client, source: Source, etag: str | None) -> FeedFetch:
    headers = {"If-None-Match": etag} if etag else {}
    response = get(client, source.feed_url, headers)
    if response.status_code == 304:
        return FeedFetch(entries=(), etag=etag, not_modified=True)
    if response.status_code != 200:
        raise FetchError(f"{source.feed_url}: HTTP {response.status_code}")
    parsed = feedparser.parse(response.content)
    if not parsed.version and not parsed.entries:
        reason = parsed.get("bozo_exception", "RSS/Atom 아님")
        raise FetchError(f"{source.feed_url}: feed 형식이 아니다 ({reason})")
    entries = tuple(
        entry for raw in parsed.entries if (entry := _to_entry(source.id, raw)) is not None
    )
    return FeedFetch(entries=entries, etag=response.headers.get("etag"), not_modified=False)


def _to_entry(source_id: str, raw: Any) -> FeedEntry | None:
    link = raw.get("link")
    if not link:
        return None
    contents = raw.get("content") or []
    html = contents[0].get("value", "") if contents else raw.get("summary", "")
    stamp = raw.get("published_parsed") or raw.get("updated_parsed")
    published = datetime.fromtimestamp(calendar.timegm(stamp), tz=UTC) if stamp else None
    return FeedEntry(
        source_id=source_id,
        url=link,
        title=(raw.get("title") or "").strip(),
        published=published,
        html=html,
    )
