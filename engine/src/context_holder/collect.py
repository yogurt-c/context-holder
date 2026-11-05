"""소스들에서 새 글을 모은다: feed → 본문 추출 → 중복 제거."""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

import httpx

from context_holder.extract import canonical_url, content_hash, html_to_markdown
from context_holder.feed import FeedEntry, FetchError, fetch_feed, get
from context_holder.sources import Mode, Source

log = logging.getLogger(__name__)

# feed 본문이 이보다 짧으면 요약뿐인 글로 보고 원문 페이지를 가져온다
MIN_FEED_BODY_CHARS = 400
# 최종 본문이 이보다 짧으면 메뉴 글자만 뽑힌 실패로 본다 (SPA 페이지 등)
MIN_BODY_CHARS = 200


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
) -> CollectResult:
    previous = dict(etags or {})
    articles: list[Article] = []
    reports: list[SourceReport] = []
    new_etags: dict[str, str] = {}
    hashes: set[str] = set()
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
            markdown = _body(client, source, entry, url)
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
    client: httpx.Client, source: Source, url: str, title: str, published: datetime | None
) -> Article:
    """feed 없이 URL 하나로 글을 다시 가져온다 (요약 대기 글을 다시 처리할 때)."""
    canonical = canonical_url(url)
    entry = FeedEntry(source.id, canonical, title, published, html="")
    markdown = _body(client, source, entry, canonical)
    return Article(source.id, canonical, title, published, markdown, content_hash(markdown))


def _body(client: httpx.Client, source: Source, entry: FeedEntry, url: str) -> str:
    feed_markdown = html_to_markdown(entry.html, url=url) if source.mode is Mode.FEED else ""
    if len(feed_markdown) >= MIN_FEED_BODY_CHARS:
        return feed_markdown
    page_markdown = html_to_markdown(_page_html(client, source, entry.url), url=url)
    markdown = max(feed_markdown, page_markdown, key=len)
    if len(markdown) < MIN_BODY_CHARS:
        raise FetchError(f"{entry.url}: 본문이 {len(markdown)}자뿐이다")
    return markdown


def _page_html(client: httpx.Client, source: Source, article_url: str) -> str:
    api_url = source.body_api.url_for(article_url) if source.body_api else None
    response = get(client, api_url or article_url)
    if response.status_code != 200:
        raise FetchError(f"{response.request.url}: HTTP {response.status_code}")
    if not api_url or source.body_api is None:
        return response.text
    try:
        html = response.json()[source.body_api.field]
    except (ValueError, KeyError, TypeError) as e:
        raise FetchError(f"{api_url}: 응답에 {source.body_api.field} 필드가 없다") from e
    return html if isinstance(html, str) else ""
