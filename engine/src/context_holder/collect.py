"""소스들에서 새 글을 모은다: feed → 본문 추출 → 중복 제거."""

import html as html_lib
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

import httpx

from context_holder.dates import parse_date
from context_holder.extract import canonical_url, content_hash, html_to_markdown
from context_holder.feed import FeedEntry, FetchError, fetch_feed, get
from context_holder.sources import Mode, Source

log = logging.getLogger(__name__)

# feed 본문이 이보다 짧으면 요약뿐인 글로 보고 원문 페이지를 가져온다
MIN_FEED_BODY_CHARS = 400
# 최종 본문이 이보다 짧으면 메뉴 글자만 뽑힌 실패로 본다 (SPA 페이지 등)
MIN_BODY_CHARS = 200

OG_TITLE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.I)
TITLE_TAG = re.compile(r"<title[^>]*>([^<]*)</title>", re.I)
PUBLISHED_META = re.compile(
    r'<meta[^>]+property="article:published_time"[^>]+content="([^"]+)"', re.I
)


@dataclass(frozen=True)
class Article:
    source_id: str
    url: str
    title: str
    published: datetime | None
    markdown: str
    content_hash: str


@dataclass(frozen=True)
class SourceReport:
    source_id: str
    found: int = 0  # feed에 있던 글
    new: int = 0  # 새로 모은 글
    skipped: int = 0  # 이미 봤거나, 기간 밖이거나, 제외 제목이거나, 중복 본문
    failed: int = 0  # 원문 페이지를 가져오지 못한 글
    not_modified: bool = False
    error: str | None = None  # feed 자체를 가져오지 못한 이유


@dataclass(frozen=True)
class CollectResult:
    articles: tuple[Article, ...]
    reports: tuple[SourceReport, ...]
    etags: dict[str, str]


def collect(
    client: httpx.Client,
    sources: Iterable[Source],
    since: datetime,
    seen_urls: frozenset[str] = frozenset(),
    etags: Mapping[str, str] | None = None,
    seen_hashes: frozenset[str] = frozenset(),
) -> CollectResult:
    previous = dict(etags or {})
    articles: list[Article] = []
    reports: list[SourceReport] = []
    new_etags: dict[str, str] = {}
    hashes: set[str] = set(seen_hashes)  # 이전 실행에서 본 본문도 거른다
    for source in sources:
        report, found, etag = _collect_source(
            client, source, since, seen_urls, previous.get(source.id), hashes
        )
        reports.append(report)
        articles.extend(found)
        if etag:
            new_etags[source.id] = etag
    return CollectResult(articles=tuple(articles), reports=tuple(reports), etags=new_etags)


def _collect_source(
    client: httpx.Client,
    source: Source,
    since: datetime,
    seen_urls: frozenset[str],
    etag: str | None,
    hashes: set[str],
) -> tuple[SourceReport, list[Article], str | None]:
    try:
        fetched = fetch_feed(client, source, etag)
    except FetchError as e:
        log.warning("feed 실패 %s: %s", source.id, e)
        return SourceReport(source.id, error=str(e)), [], etag
    if fetched.not_modified:
        return SourceReport(source.id, not_modified=True), [], etag

    articles: list[Article] = []
    skipped = failed = 0
    for entry in fetched.entries:
        url = canonical_url(entry.url)
        if (
            url in seen_urls
            or source.excludes(entry.title)
            or (entry.published is not None and entry.published < since)
        ):
            skipped += 1
            continue
        try:
            markdown, _ = _body(client, source, entry, url)
        except FetchError as e:
            log.warning("본문 실패 %s: %s", url, e)
            failed += 1
            continue
        digest = content_hash(markdown)
        if digest in hashes:
            skipped += 1
            continue
        hashes.add(digest)
        articles.append(Article(source.id, url, entry.title, entry.published, markdown, digest))
    report = SourceReport(
        source.id, found=len(fetched.entries), new=len(articles), skipped=skipped, failed=failed
    )
    return report, articles, fetched.etag


def fetch_article(
    client: httpx.Client,
    source: Source,
    url: str,
    title: str | None = None,
    published: datetime | None = None,
) -> Article:
    """feed 없이 URL 하나로 글을 가져온다. 모르는 제목 · 게시일은 원문에서 읽는다."""
    canonical = canonical_url(url)
    entry = FeedEntry(source.id, canonical, title or "", published, html="")
    markdown, page = _body(client, source, entry, canonical)
    title = title or (page.title if page else None) or canonical
    published = published or (page.published if page else None)
    return Article(source.id, canonical, title, published, markdown, content_hash(markdown))


def article_from_entry(client: httpx.Client, source: Source, entry: FeedEntry) -> Article:
    """feed 항목 하나를 글로 만든다. feed 본문이 짧을 때만 원문을 가져온다."""
    url = canonical_url(entry.url)
    markdown, _ = _body(client, source, entry, url)
    return Article(source.id, url, entry.title, entry.published, markdown, content_hash(markdown))


@dataclass(frozen=True)
class _Page:
    html: str
    title: str | None
    published: datetime | None


def _body(
    client: httpx.Client, source: Source, entry: FeedEntry, url: str
) -> tuple[str, _Page | None]:
    feed_markdown = html_to_markdown(entry.html, url=url) if source.mode is Mode.FEED else ""
    if len(feed_markdown) >= MIN_FEED_BODY_CHARS:
        return feed_markdown, None
    page = _page(client, source, entry.url)
    markdown = max(feed_markdown, html_to_markdown(page.html, url=url), key=len)
    if len(markdown) < MIN_BODY_CHARS:
        raise FetchError(f"{entry.url}: 본문이 {len(markdown)}자뿐이다")
    return markdown, page


def _page(client: httpx.Client, source: Source, article_url: str) -> _Page:
    api = source.body_api
    api_url = api.url_for(article_url) if api else None
    response = get(client, api_url or article_url)
    if response.status_code != 200:
        raise FetchError(f"{response.request.url}: HTTP {response.status_code}")
    if not api_url or api is None:
        text = response.text
        return _Page(text, _html_title(text), _html_date(text, source.date_pattern))
    try:
        data = response.json()
        html = data[api.field]
    except (ValueError, KeyError, TypeError) as e:
        raise FetchError(f"{api_url}: 응답에 {api.field} 필드가 없다") from e
    title = data.get(api.title_field) if api.title_field else None
    return _Page(
        html if isinstance(html, str) else "",
        str(title).strip() if title else None,
        parse_date(data.get(api.date_field)) if api.date_field else None,
    )


def _html_title(text: str) -> str | None:
    match = OG_TITLE.search(text) or TITLE_TAG.search(text)
    return html_lib.unescape(match.group(1)).strip() or None if match else None


def _html_date(text: str, date_pattern: str) -> datetime | None:
    # trafilatura의 날짜 추정은 일부 블로그에서 수집일을 돌려줘서 쓰지 않는다
    if date_pattern:
        match = re.search(date_pattern, text)
        if match:
            return parse_date(".".join(match.groups()) if match.groups() else match.group(0))
    match = PUBLISHED_META.search(text)
    return parse_date(match.group(1)) if match else None
