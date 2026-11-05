from datetime import UTC, datetime

import httpx
import pytest

from context_holder.archive import Candidate, list_candidates, parse_date
from context_holder.sources import Archive, Mode, Source
from tests.conftest import rss


def source(archive: Archive | None, feed: str = "https://blog.example.com/feed") -> Source:
    return Source("ex", "Ex", "domestic", feed, Mode.FEED, archive=archive)


def router(table: dict[str, httpx.Response]):
    def handler(request: httpx.Request) -> httpx.Response:
        return table.get(str(request.url), httpx.Response(404))

    return handler


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1793854254000, datetime(2026, 11, 5, 4, 50, 54, tzinfo=UTC)),
        ("2026-10-20T15:02:08.000+0900", datetime(2026, 10, 20, 6, 2, 8, tzinfo=UTC)),
        ("2026.11.03", datetime(2026, 11, 3, tzinfo=UTC)),
        ("2026년 10월 27일", datetime(2026, 10, 27, tzinfo=UTC)),
        ("2026-07-01", datetime(2026, 7, 1, tzinfo=UTC)),
        ("언젠가", None),
        (None, None),
    ],
)
def test_parse_date_handles_blog_formats(value, expected):
    assert parse_date(value) == expected


def test_feed_only_source_lists_feed_entries(make_client):
    body = rss(
        [
            {"title": "새 글", "link": "https://blog.example.com/new"},
            {"title": "옛 글", "link": "https://blog.example.com/old"},
        ]
    )
    client = make_client(router({"https://blog.example.com/feed": httpx.Response(200, text=body)}))

    found = list(list_candidates(client, source(None)))

    assert [c.url for c in found] == [
        "https://blog.example.com/new",
        "https://blog.example.com/old",
    ]
    assert found[0].title == "새 글" and found[0].published is not None


def test_wordpress_feed_pages_until_empty(make_client):
    feed = "https://blog.example.com/feed/"
    client = make_client(
        router(
            {
                feed: httpx.Response(200, text=rss([{"title": "1", "link": "https://b/1"}])),
                f"{feed}?paged=2": httpx.Response(
                    200, text=rss([{"title": "2", "link": "https://b/2"}])
                ),
            }
        )
    )

    found = list(list_candidates(client, source(Archive(kind="wp_paged"), feed=feed)))

    assert [c.url for c in found] == ["https://b/1", "https://b/2"]


def test_json_list_reads_nested_fields_and_url_templates(make_client):
    api = "https://api.example.com/posts?page={page}"
    client = make_client(
        router(
            {
                "https://api.example.com/posts?page=1": httpx.Response(
                    200,
                    json={
                        "posts": [
                            {"postId": 9, "meta": {"title": "아홉"}, "at": "2026.10.01"},
                            {"postId": 8, "meta": {"title": "여덟"}, "at": "2026.09.01"},
                        ]
                    },
                ),
                "https://api.example.com/posts?page=2": httpx.Response(200, json={"posts": []}),
            }
        )
    )
    archive = Archive(
        kind="json",
        url=api,
        first_page=1,
        items="posts",
        link="https://blog.example.com/posts/{postId}",
        title="meta.title",
        date="at",
    )

    found = list(list_candidates(client, source(archive)))

    assert found == [
        Candidate("https://blog.example.com/posts/9", "아홉", datetime(2026, 10, 1, tzinfo=UTC)),
        Candidate("https://blog.example.com/posts/8", "여덟", datetime(2026, 9, 1, tzinfo=UTC)),
    ]


def test_json_list_joins_relative_links(make_client):
    client = make_client(
        router(
            {
                "https://d2.example.com/api?page=0": httpx.Response(
                    200,
                    json={
                        "content": [{"url": "/helloworld/1", "postTitle": "t", "ts": 1793854254000}]
                    },
                ),
                "https://d2.example.com/api?page=1": httpx.Response(200, json={"content": []}),
            }
        )
    )
    archive = Archive(
        kind="json",
        url="https://d2.example.com/api?page={page}",
        first_page=0,
        items="content",
        link="url",
        link_base="https://d2.example.com",
        title="postTitle",
        date="ts",
    )

    [found] = list(list_candidates(client, source(archive)))

    assert found.url == "https://d2.example.com/helloworld/1"


def test_html_list_collects_links_page_by_page_without_duplicates(make_client):
    def page(*slugs):
        return "".join(f'<a href="/article/{s}">x</a>' for s in slugs)

    client = make_client(
        router(
            {
                "https://toss.example.com/?page=1": httpx.Response(200, text=page("a", "b", "a")),
                "https://toss.example.com/?page=2": httpx.Response(200, text=page("b", "c")),
                "https://toss.example.com/?page=3": httpx.Response(200, text=page("c")),
            }
        )
    )
    archive = Archive(
        kind="html",
        url="https://toss.example.com/?page={page}",
        first_page=1,
        link="/article/[a-z0-9-]+",
        link_base="https://toss.example.com",
    )

    found = list(list_candidates(client, source(archive)))

    assert [c.url for c in found] == [
        "https://toss.example.com/article/a",
        "https://toss.example.com/article/b",
        "https://toss.example.com/article/c",
    ]
    assert found[0].title is None and found[0].published is None


def test_sitemap_orders_by_numeric_id_descending(make_client):
    xml = (
        "".join(f"<url><loc>https://tech.example.com/posts/{n}</loc></url>" for n in (3, 845, 12))
        + "<url><loc>https://tech.example.com/about</loc></url>"
    )
    client = make_client(
        router({"https://tech.example.com/sitemap.xml": httpx.Response(200, text=xml)})
    )
    archive = Archive(
        kind="sitemap",
        url="https://tech.example.com/sitemap.xml",
        link=r"https://tech\.example\.com/posts/(\d+)",
    )

    found = list(list_candidates(client, source(archive)))

    assert [c.url for c in found] == [
        "https://tech.example.com/posts/845",
        "https://tech.example.com/posts/12",
        "https://tech.example.com/posts/3",
    ]


def test_listing_stops_quietly_on_http_errors_after_first_page(make_client):
    client = make_client(
        router(
            {
                "https://api.example.com/p1": httpx.Response(
                    200, json={"items": [{"u": "https://b/1", "t": "t", "d": "2026.10.01"}]}
                ),
                "https://api.example.com/p2": httpx.Response(500),
            }
        )
    )
    archive = Archive(
        kind="json",
        url="https://api.example.com/p{page}",
        first_page=1,
        items="items",
        link="u",
        title="t",
        date="d",
    )

    found = list(list_candidates(client, source(archive)))

    assert [c.url for c in found] == ["https://b/1"]


def _json_archive() -> Archive:
    return Archive(
        kind="json", url="https://api.example.com/p{page}", first_page=1, items="items", link="u"
    )


def test_wordpress_stops_when_a_page_is_empty(make_client):
    feed = "https://blog.example.com/feed/"
    client = make_client(
        router(
            {
                feed: httpx.Response(200, text=rss([{"title": "1", "link": "https://b/1"}])),
                f"{feed}?paged=2": httpx.Response(200, text=rss([])),
            }
        )
    )

    found = list(list_candidates(client, source(Archive(kind="wp_paged"), feed=feed)))

    assert [c.url for c in found] == ["https://b/1"]


@pytest.mark.parametrize(
    "first",
    [httpx.Response(503), "network"],
)
def test_first_page_failure_is_an_error(make_client, first):
    from context_holder.feed import FetchError

    def handler(request: httpx.Request) -> httpx.Response:
        if first == "network":
            raise httpx.ConnectError("down", request=request)
        return first

    with pytest.raises(FetchError):
        list(list_candidates(make_client(handler), source(_json_archive())))


def test_later_network_failure_and_bad_json_end_the_listing(make_client):
    pages = {
        "https://api.example.com/p1": httpx.Response(200, json={"items": [{"u": "https://b/1"}]}),
        "https://api.example.com/p2": httpx.Response(200, text="<html>not json</html>"),
    }
    client = make_client(router(pages))
    assert [c.url for c in list_candidates(client, source(_json_archive()))] == ["https://b/1"]

    def flaky(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("p2"):
            raise httpx.ConnectError("down", request=request)
        return pages["https://api.example.com/p1"]

    assert [c.url for c in list_candidates(make_client(flaky), source(_json_archive()))] == [
        "https://b/1"
    ]


def test_missing_sitemap_is_an_error(make_client):
    from context_holder.feed import FetchError

    archive = Archive(kind="sitemap", url="https://tech.example.com/sitemap.xml", link=r"x/(\d+)")

    with pytest.raises(FetchError):
        list(list_candidates(make_client(router({})), source(archive)))


def test_nested_field_through_non_object_is_none(make_client):
    client = make_client(
        router(
            {
                "https://api.example.com/p1": httpx.Response(
                    200, json={"items": [{"u": "https://b/1", "meta": "flat", "d": None}]}
                ),
                "https://api.example.com/p2": httpx.Response(200, json={"items": []}),
            }
        )
    )
    archive = Archive(
        kind="json",
        url="https://api.example.com/p{page}",
        items="items",
        link="u",
        title="meta.title",
        date="d",
    )

    [found] = list(list_candidates(client, source(archive)))

    assert found.title is None and found.published is None
