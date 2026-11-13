"""블로그 주소에서 RSS/Atom feed를 찾고, 실제로 글을 모을 수 있는지 확인한다.

Sources 화면의 "소스 추가"가 쓴다. 요청은 모두 feed.get을 거친다 (robots.txt · 요청 간격).
찾는 순서: 입력한 주소 자체 → 플랫폼 규칙(Medium · velog · 네이버 블로그)
→ 페이지의 <link rel="alternate"> → 흔한 feed 경로.
찾은 뒤에는 최근 글 몇 개를 실제 수집과 같은 코드로 끝까지 가져와 본문이 나오는지 본다.
"""

import html as html_lib
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import httpx

from context_holder.collect import article_from_entry
from context_holder.feed import FeedEntry, FetchError, ParsedFeed, RobotsBlocked, get, parse_feed
from context_holder.sources import Region, Source

MAX_CANDIDATES = 10  # 한 사이트에 너무 많이 요청하지 않게 (요청마다 1초 간격)
SAMPLE = 2  # 본문을 확인해 볼 최근 글 수
PREVIEW = 3  # 화면에 보일 최근 글 수
STALE_DAYS = 180
COMMON_PATHS = ("feed", "rss", "rss.xml", "atom.xml", "feed.xml", "index.xml")
FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json")
FORBIDDEN = (401, 403, 429)
HANGUL = re.compile(r"[가-힣]")
LINK_TAG = re.compile(r"<link\b[^>]*>", re.I)
ATTR = re.compile(r"""([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")

# (호스트, 경로) → 그 블로그의 feed 주소
PLATFORMS: tuple[tuple[re.Pattern[str], re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"^medium\.com$"), re.compile(r"^/(@?[^/]+)"), ("https://medium.com/feed/{}",)),
    (
        re.compile(r"^velog\.io$"),
        re.compile(r"^/@([^/]+)"),
        ("https://v2.velog.io/rss/{}", "https://v2.velog.io/rss/@{}"),
    ),
    (
        re.compile(r"^(?:m\.)?blog\.naver\.com$"),
        re.compile(r"^/([A-Za-z0-9_-]+)"),
        ("https://rss.blog.naver.com/{}.xml",),
    ),
)


class NotCollectable(Exception):
    """이 블로그는 모을 수 없다. 메시지는 화면에 그대로 보인다."""


@dataclass(frozen=True)
class Probe:
    feed_url: str
    title: str  # 블로그 이름 (feed 제목, 없으면 호스트)
    region: Region  # 추정값. 화면에서 고친다
    recent: tuple[FeedEntry, ...]  # 최근 글, 최신순
    warnings: tuple[str, ...]


def probe(
    client: httpx.Client,
    url: str,
    now: datetime,
    on_step: Callable[[str], None] = lambda message: None,
) -> Probe:
    on_step("피드를 찾는 중…")
    feed_url, parsed = find_feed(client, url)
    entries = _newest_first(parsed.entries)
    if not entries:
        raise NotCollectable("피드는 찾았지만 글이 하나도 없어요.")
    title = parsed.title or urlsplit(parsed.link or url).hostname or feed_url
    on_step("글 본문을 가져올 수 있는지 확인하는 중…")
    _check_body(client, Source("probe", title, "overseas", feed_url), entries[:SAMPLE])
    return Probe(
        feed_url=feed_url,
        title=title,
        region=_guess_region(url, title, entries),
        recent=entries[:PREVIEW],
        warnings=_warnings(entries, now),
    )


def find_feed(client: httpx.Client, url: str) -> tuple[str, ParsedFeed]:
    tried = _Attempts()
    page = tried.fetch(client, url)
    if isinstance(page, ParsedFeed):
        return url, page
    for candidate in _candidates(url, page):
        found = tried.fetch(client, candidate)
        if isinstance(found, ParsedFeed):
            return candidate, found
    raise NotCollectable(tried.reason())


# ---------- 내부 ----------


@dataclass
class _Attempts:
    """시도한 주소와 실패 이유. 아무 데서도 못 찾으면 가장 그럴듯한 이유를 보인다."""

    count: int = 0
    seen: set[str] = field(default_factory=set)
    robots: bool = False
    forbidden: int | None = None
    unreachable: bool = False  # 입력한 주소 자체에 연결하지 못했다

    def fetch(self, client: httpx.Client, url: str) -> ParsedFeed | str | None:
        """feed면 ParsedFeed, HTML 등 다른 페이지면 그 본문, 실패하면 None."""
        if url in self.seen or self.count >= MAX_CANDIDATES:
            return None
        first = self.count == 0
        self.seen.add(url)
        self.count += 1
        try:
            response = get(client, url)
        except RobotsBlocked:
            self.robots = True
            return None
        except FetchError:
            self.unreachable = self.unreachable or first
            return None
        if response.status_code in FORBIDDEN:
            self.forbidden = self.forbidden or response.status_code
        if response.status_code != 200:
            return None
        return parse_feed(response.content, "probe") or response.text

    def reason(self) -> str:
        if self.unreachable:
            return "주소에 연결하지 못했어요. 주소를 확인해 주세요."
        if self.robots:
            return "사이트의 robots.txt가 자동 수집을 막고 있어요."
        if self.forbidden:
            return f"사이트가 자동 접근을 막고 있어요 (HTTP {self.forbidden})."
        return "RSS/Atom 피드를 찾지 못했어요. 피드 주소를 직접 넣어 보세요."


def _candidates(url: str, page: str | None) -> Iterator[str]:
    yield from _platform_feeds(url)
    if page:
        yield from _alternates(page, url)
    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}/"
    folder = urljoin(url, "./") if parts.path.strip("/") else root
    if not folder.endswith("/"):
        folder += "/"
    for base in dict.fromkeys((folder, root)):
        for path in COMMON_PATHS:
            yield urljoin(base, path)


def _platform_feeds(url: str) -> Iterator[str]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    for host_pattern, path_pattern, templates in PLATFORMS:
        match = path_pattern.match(parts.path) if host_pattern.match(host) else None
        if match:
            for template in templates:
                yield template.format(match.group(1))


def _alternates(page: str, base: str) -> Iterator[str]:
    """페이지 <head>의 <link rel="alternate" type="application/rss+xml" href="…">."""
    for tag in LINK_TAG.findall(page):
        attrs = {m[0].lower(): html_lib.unescape(m[1] or m[2] or m[3]) for m in ATTR.findall(tag)}
        rel = attrs.get("rel", "").lower().split()
        kind = attrs.get("type", "").lower()
        if "alternate" in rel and kind in FEED_TYPES and attrs.get("href"):
            yield urljoin(base, attrs["href"])


def _newest_first(entries: tuple[FeedEntry, ...]) -> tuple[FeedEntry, ...]:
    """게시일이 있는 글은 최신순으로, 없는 글은 feed 순서대로 뒤에 둔다."""
    dated = sorted((e for e in entries if e.published), key=lambda e: e.published, reverse=True)
    return (*dated, *(e for e in entries if not e.published))


def _check_body(client: httpx.Client, source: Source, sample: tuple[FeedEntry, ...]) -> None:
    """최근 글 중 하나라도 본문을 끝까지 가져오면 모을 수 있는 블로그다."""
    blocked = False
    for entry in sample:
        try:
            article_from_entry(client, source, entry)
            return
        except RobotsBlocked:
            blocked = True
        except FetchError:
            continue
    if blocked:
        raise NotCollectable("피드는 찾았지만 글 페이지를 robots.txt가 막고 있어요.")
    raise NotCollectable(
        "피드는 찾았지만 글 본문을 가져오지 못했어요. 본문을 화면에서 그리는 블로그일 수 있어요."
    )


def _guess_region(url: str, title: str, entries: tuple[FeedEntry, ...]) -> Region:
    host = urlsplit(url).hostname or ""
    texts = [title, *(e.title for e in entries[:PREVIEW])]
    korean = host.endswith(".kr") or any(HANGUL.search(t) for t in texts)
    return "domestic" if korean else "overseas"


def _warnings(entries: tuple[FeedEntry, ...], now: datetime) -> tuple[str, ...]:
    found: list[str] = []
    latest = entries[0].published
    if latest is None:
        found.append(
            f"글에 게시일이 없어요. 첫 수집 때 피드의 글 {len(entries)}개를 모두 가져와요."
        )
    elif (now - latest).days >= STALE_DAYS:
        found.append(f"마지막 글이 {(now - latest).days}일 전이에요. 새 글이 드물 수 있어요.")
    return tuple(found)
