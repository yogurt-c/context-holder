"""첫 실행(init) 때 소스의 지난 글 목록을 최신순으로 얻는다.

feed에는 최근 글만 있어서, 소스마다 설정한 방법(`archive`)으로 더 오래된 글 주소를 찾는다.
본문은 여기서 가져오지 않는다. 목록 페이지 하나가 실패하면 그 소스의 목록은 거기서 멈춘다.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from context_holder.dates import parse_date
from context_holder.feed import FetchError, fetch_feed, get
from context_holder.sources import Archive, Source

MAX_PAGES = 60  # 잘못된 설정으로 끝없이 넘기지 않게


@dataclass(frozen=True)
class Candidate:
    url: str
    title: str | None
    published: datetime | None


def list_candidates(client: httpx.Client, source: Source) -> Iterator[Candidate]:
    """최신 글부터 내준다. 첫 목록부터 실패하면 FetchError."""
    archive = source.archive
    if archive is None:
        yield from _feed_entries(client, source, source.feed_url)
    elif archive.kind == "wp_paged":
        yield from _wordpress(client, source)
    elif archive.kind == "json":
        yield from _json_pages(client, archive)
    elif archive.kind == "html":
        yield from _html_pages(client, archive)
    else:
        yield from _sitemap(client, archive)


def _feed_entries(client: httpx.Client, source: Source, url: str) -> Iterator[Candidate]:
    paged = Source(source.id, source.name, source.region, url, source.mode)
    for entry in fetch_feed(client, paged, etag=None).entries:
        yield Candidate(entry.url, entry.title or None, entry.published)


def _wordpress(client: httpx.Client, source: Source) -> Iterator[Candidate]:
    yield from _feed_entries(client, source, source.feed_url)
    joiner = "&" if "?" in source.feed_url else "?"
    for page in range(2, MAX_PAGES + 1):
        try:
            found = list(_feed_entries(client, source, f"{source.feed_url}{joiner}paged={page}"))
        except FetchError:
            return  # 마지막 페이지 다음은 404를 준다
        if not found:
            return
        yield from found


def _pages(client: httpx.Client, archive: Archive) -> Iterator[httpx.Response]:
    for n, page in enumerate(range(archive.first_page, archive.first_page + MAX_PAGES)):
        url = archive.url.replace("{page}", str(page))
        try:
            response = get(client, url)
        except FetchError:
            if n == 0:
                raise
            return
        if response.status_code != 200:
            if n == 0:
                raise FetchError(f"{url}: HTTP {response.status_code}")
            return
        yield response


def _json_pages(client: httpx.Client, archive: Archive) -> Iterator[Candidate]:
    for response in _pages(client, archive):
        try:
            items = _field(response.json(), archive.items)
        except ValueError:
            return
        if not isinstance(items, list) or not items:
            return
        for item in items:
            if isinstance(item, dict):
                yield Candidate(
                    url=_json_link(item, archive),
                    title=_text(_field(item, archive.title)) if archive.title else None,
                    published=parse_date(_field(item, archive.date)) if archive.date else None,
                )


def _json_link(item: dict[str, Any], archive: Archive) -> str:
    if "{" in archive.link:
        return archive.link.format_map({k: v for k, v in item.items() if not isinstance(v, dict)})
    return urljoin(archive.link_base or "", str(_field(item, archive.link)))


def _html_pages(client: httpx.Client, archive: Archive) -> Iterator[Candidate]:
    seen: set[str] = set()
    pattern = re.compile(archive.link)
    for response in _pages(client, archive):
        links = [
            urljoin(archive.link_base or "", m.group(0)) for m in pattern.finditer(response.text)
        ]
        fresh = list(dict.fromkeys(link for link in links if link not in seen))
        if not fresh:
            return  # 마지막 페이지를 넘기면 같은 목록이 반복된다
        seen.update(fresh)
        for link in fresh:
            yield Candidate(link, None, None)


def _sitemap(client: httpx.Client, archive: Archive) -> Iterator[Candidate]:
    response = get(client, archive.url)
    if response.status_code != 200:
        raise FetchError(f"{archive.url}: HTTP {response.status_code}")
    pattern = re.compile(archive.link)
    found: dict[str, int] = {}
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", response.text):
        match = pattern.fullmatch(loc)
        if match:
            found[loc] = int(match.group(1)) if match.groups() and match.group(1).isdigit() else 0
    for url in sorted(found, key=lambda u: -found[u]):
        yield Candidate(url, None, None)


def _field(data: Any, path: str) -> Any:
    for key in path.split(".") if path else ():
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _text(value: Any) -> str | None:
    return str(value).strip() or None if value is not None else None
