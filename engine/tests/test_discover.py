"""블로그 주소로 feed를 찾고, 글을 실제로 모을 수 있는지 확인한다."""

from datetime import UTC, datetime

import httpx
import pytest

from context_holder import discover, feed
from context_holder.discover import NotCollectable, find_feed, probe
from tests.conftest import LONG_BODY, article_page, rss

NOW = datetime(2026, 11, 13, tzinfo=UTC)
FEED = rss(
    [
        {"title": "캐시 전략", "link": "https://blog.example.kr/post/2", "content": LONG_BODY},
        {
            "title": "예전 글",
            "link": "https://blog.example.kr/post/1",
            "content": LONG_BODY,
            "date": "Mon, 03 Nov 2026 01:00:00 GMT",
        },
    ]
)
HOME = """<html><head>
<link rel="stylesheet" href="/a.css">
<link rel="alternate" type="application/rss+xml" title="RSS" href="/posts/index.xml">
</head><body>홈</body></html>"""


def routes(table: dict[str, httpx.Response], log: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if log is not None:
            log.append(url)
        return table.get(url, httpx.Response(404))

    return handler


def test_feed_url_itself(make_client):
    client = make_client(routes({"https://blog.example.kr/rss": httpx.Response(200, text=FEED)}))
    url, parsed = find_feed(client, "https://blog.example.kr/rss")
    assert url == "https://blog.example.kr/rss"
    assert parsed.title == "blog" and len(parsed.entries) == 2


def test_finds_link_alternate_in_home_page(make_client):
    log: list[str] = []
    client = make_client(
        routes(
            {
                "https://blog.example.kr/": httpx.Response(200, text=HOME),
                "https://blog.example.kr/posts/index.xml": httpx.Response(200, text=FEED),
            },
            log,
        )
    )
    url, _ = find_feed(client, "https://blog.example.kr/")
    assert url == "https://blog.example.kr/posts/index.xml"
    assert log == ["https://blog.example.kr/", "https://blog.example.kr/posts/index.xml"]


def test_tries_common_paths_under_blog_folder_first(make_client):
    log: list[str] = []
    client = make_client(
        routes({"https://co.example/blog/rss.xml": httpx.Response(200, text=FEED)}, log)
    )
    url, _ = find_feed(client, "https://co.example/blog/")
    assert url == "https://co.example/blog/rss.xml"
    assert log[1:3] == ["https://co.example/blog/feed", "https://co.example/blog/rss"]


def test_platform_rules(make_client):
    client = make_client(routes({"https://medium.com/feed/daangn": httpx.Response(200, text=FEED)}))
    url, _ = find_feed(client, "https://medium.com/daangn")
    assert url == "https://medium.com/feed/daangn"
    assert list(discover._platform_feeds("https://blog.naver.com/abc")) == [
        "https://rss.blog.naver.com/abc.xml"
    ]
    assert "https://v2.velog.io/rss/kim" in list(discover._platform_feeds("https://velog.io/@kim"))


def test_stops_after_max_candidates(make_client):
    log: list[str] = []
    client = make_client(routes({}, log))
    with pytest.raises(NotCollectable, match="찾지 못했어요"):
        find_feed(client, "https://co.example/blog/")
    assert len(log) == discover.MAX_CANDIDATES


def test_reports_forbidden_and_robots(make_client, monkeypatch):
    client = make_client(lambda request: httpx.Response(403))
    with pytest.raises(NotCollectable, match="HTTP 403"):
        find_feed(client, "https://co.example/")

    monkeypatch.setattr(feed, "RESPECT_ROBOTS", True)
    robots = "User-agent: *\nDisallow: /\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(200, text=FEED)

    feed._robots.clear()
    with pytest.raises(NotCollectable, match=r"robots\.txt"):
        find_feed(make_client(handler), "https://blocked.example/")
    feed._robots.clear()


def test_unreachable_address(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(NotCollectable, match="연결하지 못했어요"):
        find_feed(make_client(handler), "https://nowhere.example/")


def test_probe_checks_body_and_guesses_region(make_client):
    client = make_client(routes({"https://blog.example.kr/rss": httpx.Response(200, text=FEED)}))
    result = probe(client, "https://blog.example.kr/rss", NOW)
    assert result.feed_url == "https://blog.example.kr/rss"
    assert result.region == "domestic"
    assert [e.title for e in result.recent] == ["캐시 전략", "예전 글"]
    assert result.warnings == ()


def test_probe_fetches_page_when_feed_has_summary_only(make_client):
    short = rss([{"title": "Cache", "link": "https://b.example/p/1", "summary": "short"}])
    client = make_client(
        routes(
            {
                "https://b.example/feed": httpx.Response(200, text=short),
                "https://b.example/p/1": httpx.Response(200, text=article_page("Cache", LONG_BODY)),
            }
        )
    )
    result = probe(client, "https://b.example/feed", NOW)
    assert result.region == "overseas" and result.title == "blog"


def test_probe_fails_when_body_is_empty(make_client):
    short = rss([{"title": "SPA", "link": "https://spa.example/p/1", "summary": "short"}])
    client = make_client(
        routes(
            {
                "https://spa.example/feed": httpx.Response(200, text=short),
                "https://spa.example/p/1": httpx.Response(200, text="<div id=root></div>"),
            }
        )
    )
    with pytest.raises(NotCollectable, match="본문"):
        probe(client, "https://spa.example/feed", NOW)


def test_probe_fails_on_empty_feed(make_client):
    client = make_client(routes({"https://e.example/feed": httpx.Response(200, text=rss([]))}))
    with pytest.raises(NotCollectable, match="글이 하나도"):
        probe(client, "https://e.example/feed", NOW)


def test_probe_warns_on_stale_or_undated_feed(make_client):
    old = rss(
        [
            {
                "title": "t",
                "link": "https://o.example/1",
                "content": LONG_BODY,
                "date": "Mon, 03 Mar 2025 01:00:00 GMT",
            }
        ]
    )
    client = make_client(routes({"https://o.example/feed": httpx.Response(200, text=old)}))
    [warning] = probe(client, "https://o.example/feed", NOW).warnings
    assert "일 전" in warning

    undated = old.replace("<pubDate>Mon, 03 Mar 2025 01:00:00 GMT</pubDate>", "")
    client = make_client(routes({"https://o.example/feed": httpx.Response(200, text=undated)}))
    [warning] = probe(client, "https://o.example/feed", NOW).warnings
    assert "게시일이 없어요" in warning
