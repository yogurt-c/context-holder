"""화면에서 sources.yaml을 고친다: 소스 추가 · 수집 제외.

파일을 다시 쓰지 않고 필요한 줄만 넣고 바꾼다. 주석과 다른 항목은 그대로 둔다.
고친 내용은 다음 수집이 커밋 · push한다 (profile.yaml 직군과 같다).
"""

import json
import re
from collections.abc import Collection, Iterable
from urllib.parse import urlsplit

import yaml

from context_holder.sources import Region, Source, SourceConfigError, parse_sources

SOURCES_KEY = re.compile(r"^sources:[ \t]*(?:#.*)?$")
ITEM = re.compile(r"^([ \t]*)- ")
EXCLUDED_LINE = re.compile(r"^excluded:[^\n]*\n?", re.M)
DEFAULT_INDENT = "  "
NAME_MAX = 60

# 한 사이트에 여러 블로그가 있는 곳: 블로그를 가리키는 경로 조각을 id에 넣는다
PLATFORM_IDS = (
    (re.compile(r"^medium\.com$"), re.compile(r"^/feed/@?([^/]+)"), "medium-{}"),
    (re.compile(r"^v2\.velog\.io$"), re.compile(r"^/rss/@?([^/]+)"), "velog-{}"),
    (re.compile(r"^rss\.blog\.naver\.com$"), re.compile(r"^/([^/.]+)"), "naver-blog-{}"),
)


def same_feed(a: str, b: str) -> bool:
    """http · https, www, 끝의 / 차이는 같은 feed로 본다."""
    return _feed_key(a) == _feed_key(b)


def find_by_feed(sources: Iterable[Source], feed_url: str) -> Source | None:
    return next((s for s in sources if same_feed(s.feed_url, feed_url)), None)


def new_id(feed_url: str, taken: Collection[str]) -> str:
    """feed 주소로 소스 id를 만든다. 이미 있는 id면 뒤에 -2, -3을 붙인다."""
    parts = urlsplit(feed_url)
    host = (parts.hostname or "blog").removeprefix("www.")
    base = host
    for host_pattern, path_pattern, template in PLATFORM_IDS:
        match = path_pattern.match(parts.path) if host_pattern.match(host) else None
        if match:
            base = template.format(match.group(1))
            break
    base = re.sub(r"[^a-z0-9.-]+", "-", base.lower()).strip(".-") or "blog"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def with_source(text: str, source_id: str, name: str, region: Region, feed_url: str) -> str:
    """sources 목록 끝에 소스 하나를 넣는다."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if SOURCES_KEY.match(line)), None)
    if start is None:
        raise SourceConfigError("sources.yaml에서 sources 목록을 찾지 못했다")
    end = _block_end(lines, start)
    indent = next(
        (m.group(1) for line in lines[start + 1 : end] if (m := ITEM.match(line))), DEFAULT_INDENT
    )
    if end > 0 and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    entry = (
        f"{indent}- id: {source_id}\n"
        f"{indent}  name: {_quoted(name)}\n"
        f"{indent}  region: {region}\n"
        f"{indent}  feed: {_quoted(feed_url)}\n"
    )
    return "".join([*lines[:end], entry, *lines[end:]])


def with_excluded(text: str, ids: Iterable[str]) -> str:
    """수집 대상에서 뺀 소스 목록(excluded)을 적는다. 비어 있으면 그 줄을 지운다."""
    chosen = sorted(set(ids))
    line = f"excluded: [{', '.join(_quoted(i) for i in chosen)}]\n" if chosen else ""
    if EXCLUDED_LINE.search(text):
        return EXCLUDED_LINE.sub(lambda _: line, text, count=1)
    if not line:
        return text
    body = text if not text or text.endswith("\n") else text + "\n"
    return f"{body}{line}"


def checked(text: str, origin: str) -> tuple[Source, ...]:
    """고친 내용이 올바른 설정인지 확인한다. 잘못됐으면 SourceConfigError."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SourceConfigError(f"{origin}: 고친 내용을 읽을 수 없다 ({e})") from e
    return parse_sources(data, origin)


def clean_name(value: str) -> str:
    name = " ".join(value.split())
    if not name:
        raise ValueError("이름을 입력해 주세요.")
    if len(name) > NAME_MAX:
        raise ValueError(f"이름은 {NAME_MAX}자까지 쓸 수 있어요.")
    return name


# ---------- 내부 ----------


def _feed_key(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{parts.path.rstrip('/')}{query}"


def _block_end(lines: list[str], start: int) -> int:
    """sources 목록이 끝나는 줄. 다음 최상위 키 앞의 빈 줄 · 주석은 그 키의 것으로 본다."""
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].strip() and not lines[i][0].isspace() and not lines[i].startswith("#")
        ),
        len(lines),
    )
    if end == len(lines):
        return end
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
        end -= 1
    return end


def _quoted(value: str) -> str:
    """YAML 큰따옴표 문자열 (JSON 문자열은 그대로 YAML이다)."""
    return json.dumps(value, ensure_ascii=False)
