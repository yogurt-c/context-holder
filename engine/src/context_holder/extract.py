"""URL 정규화, HTML 본문 추출(Markdown), 본문 해시."""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import trafilatura

# 추적용이라 같은 글을 다른 URL로 보이게 만드는 파라미터
TRACKING_PARAMS = frozenset({"source", "ref", "fbclid", "gclid", "mc_cid", "mc_eid"})


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def html_to_markdown(html: str, url: str) -> str:
    """전체 페이지나 feed의 HTML 조각에서 본문을 Markdown으로 뽑는다."""
    if not html.strip():
        return ""
    if "<html" not in html[:1000].lower():
        # trafilatura는 전체 페이지용이라 feed 조각은 페이지 구조로 감싸야 본문을 찾는다
        html = f"<html><body><article>{html}</article></body></html>"
    text = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_images=False,
        include_tables=True,
        include_comments=False,
        favor_recall=True,
    )
    return (text or "").strip()


def content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
