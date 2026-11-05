from datetime import UTC, datetime

import httpx

from context_holder.collect import collect
from context_holder.sources import Mode, Source
from tests.conftest import LONG_BODY, article_page, rss

SINCE = datetime(2026, 11, 1, tzinfo=UTC)
FULL = Source("toss", "Toss", "domestic", "https://toss.tech/rss.xml", Mode.FEED)
FETCH = Source(
    "kakao", "Kakao", "domestic", "https://tech.kakao.com/feed/", Mode.FETCH, ("(?i)채용",)
)


def routes(table: dict[str, httpx.Response]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url not in table:
            return httpx.Response(404)
        return table[url]

    return handler


def test_collects_full_feed_and_fetches_pages_for_fetch_mode(make_client):
    client = make_client(
        routes(
            {
                FULL.feed_url: httpx.Response(
                    200,
                    text=rss(
                        [
                            {
                                "title": "캐시 전략",
                                "link": "https://toss.tech/article/cache?utm_source=rss",
                                "content": article_page("캐시 전략", LONG_BODY),
                            }
                        ]
                    ),
                    headers={"etag": '"t1"'},
                ),
                FETCH.feed_url: httpx.Response(
                    200,
                    text=rss(
                        [
                            {"title": "MSA 전환기", "link": "https://tech.kakao.com/posts/1"},
                            {"title": "2026 채용", "link": "https://tech.kakao.com/posts/2"},
                        ]
                    ),
                ),
                "https://tech.kakao.com/posts/1": httpx.Response(
                    200, text=article_page("MSA 전환기", LONG_BODY + " 카카오")
                ),
            }
        )
    )

    result = collect(client, [FULL, FETCH], since=SINCE)

    assert [a.url for a in result.articles] == [
        "https://toss.tech/article/cache",
        "https://tech.kakao.com/posts/1",
    ]
    assert all("캐시 무효화 전략" in a.markdown for a in result.articles)
    assert result.etags == {"toss": '"t1"'}
    kakao = next(r for r in result.reports if r.source_id == "kakao")
    assert (kakao.found, kakao.new, kakao.skipped) == (2, 1, 1)


def test_short_feed_body_falls_back_to_page(make_client):
    client = make_client(
        routes(
            {
                FULL.feed_url: httpx.Response(
                    200,
                    text=rss(
                        [
                            {
                                "title": "영상 글",
                                "link": "https://toss.tech/v",
                                "content": "<p>짧음</p>",
                            }
                        ]
                    ),
                ),
                "https://toss.tech/v": httpx.Response(200, text=article_page("영상 글", LONG_BODY)),
            }
        )
    )

    [article] = collect(client, [FULL], since=SINCE).articles

    assert "캐시 무효화 전략" in article.markdown


def test_skips_seen_old_and_duplicate_entries(make_client):
    page = article_page("같은 글", LONG_BODY)
    body = rss(
        [
            {"title": "본 글", "link": "https://toss.tech/seen", "content": page},
            {
                "title": "옛 글",
                "link": "https://toss.tech/old",
                "content": page + " old",
                "date": "Mon, 01 Sep 2026 00:00:00 GMT",
            },
            {"title": "새 글", "link": "https://toss.tech/new", "content": page},
            {"title": "같은 본문", "link": "https://toss.tech/copy", "content": page},
        ]
    )
    client = make_client(routes({FULL.feed_url: httpx.Response(200, text=body)}))

    result = collect(client, [FULL], since=SINCE, seen_urls=frozenset({"https://toss.tech/seen"}))

    assert [a.url for a in result.articles] == ["https://toss.tech/new"]
    [report] = result.reports
    assert (report.found, report.new, report.skipped) == (4, 1, 3)


def test_one_source_failing_does_not_stop_others(make_client):
    client = make_client(
        routes(
            {
                FULL.feed_url: httpx.Response(500),
                FETCH.feed_url: httpx.Response(200, text=rss([])),
            }
        )
    )

    result = collect(client, [FULL, FETCH], since=SINCE)

    failed = next(r for r in result.reports if r.source_id == "toss")
    assert failed.error and "500" in failed.error
    assert next(r for r in result.reports if r.source_id == "kakao").error is None


def test_page_fetch_failure_is_counted_not_raised(make_client):
    client = make_client(
        routes(
            {
                FETCH.feed_url: httpx.Response(
                    200, text=rss([{"title": "글", "link": "https://tech.kakao.com/posts/9"}])
                ),
                "https://tech.kakao.com/posts/9": httpx.Response(403),
            }
        )
    )

    result = collect(client, [FETCH], since=SINCE)

    assert result.articles == ()
    [report] = result.reports
    assert report.failed == 1
    assert report.error is None


def test_not_modified_keeps_previous_etag(make_client):
    client = make_client(routes({FULL.feed_url: httpx.Response(304)}))

    result = collect(client, [FULL], since=SINCE, etags={"toss": '"t0"'})

    assert result.etags == {"toss": '"t0"'}
    assert result.reports[0].not_modified


def test_fetch_mode_uses_body_api_when_configured(make_client):
    from context_holder.sources import BodyApi

    source = Source(
        "kakao",
        "Kakao",
        "domestic",
        "https://tech.kakao.com/feed/",
        Mode.FETCH,
        body_api=BodyApi("https://tech.kakao.com/api/v1/posts/{id}", r"/posts/(\d+)", "content"),
    )
    client = make_client(
        routes(
            {
                source.feed_url: httpx.Response(
                    200,
                    text=rss([{"title": "CDC 전환", "link": "https://tech.kakao.com/posts/845"}]),
                ),
                "https://tech.kakao.com/api/v1/posts/845": httpx.Response(
                    200, json={"content": f"<h2>요약</h2><p>{LONG_BODY}</p>"}
                ),
            }
        )
    )

    [article] = collect(client, [source], since=SINCE).articles

    assert article.url == "https://tech.kakao.com/posts/845"
    assert "캐시 무효화 전략" in article.markdown


def test_body_api_bad_response_counts_as_failure(make_client):
    from context_holder.sources import BodyApi

    source = Source(
        "kakao",
        "Kakao",
        "domestic",
        "https://tech.kakao.com/feed/",
        Mode.FETCH,
        body_api=BodyApi("https://tech.kakao.com/api/v1/posts/{id}", r"/posts/(\d+)", "content"),
    )
    client = make_client(
        routes(
            {
                source.feed_url: httpx.Response(
                    200,
                    text=rss(
                        [
                            {"title": "a", "link": "https://tech.kakao.com/posts/1"},
                            {"title": "b", "link": "https://tech.kakao.com/posts/2"},
                        ]
                    ),
                ),
                "https://tech.kakao.com/api/v1/posts/1": httpx.Response(200, text="not json"),
                "https://tech.kakao.com/api/v1/posts/2": httpx.Response(200, json={"other": 1}),
            }
        )
    )

    result = collect(client, [source], since=SINCE)

    assert result.articles == ()
    assert result.reports[0].failed == 2


def test_navigation_only_page_is_a_failure_not_an_article(make_client):
    nav_only = "<html><body><nav>메뉴 검색 블로그</nav><main><p>짧은 글</p></main></body></html>"
    client = make_client(
        routes(
            {
                FETCH.feed_url: httpx.Response(
                    200, text=rss([{"title": "SPA 글", "link": "https://tech.kakao.com/posts/3"}])
                ),
                "https://tech.kakao.com/posts/3": httpx.Response(200, text=nav_only),
            }
        )
    )

    result = collect(client, [FETCH], since=SINCE)

    assert result.articles == ()
    assert result.reports[0].failed == 1


def test_fetch_article_gets_one_article_by_url(make_client):
    from context_holder.collect import fetch_article

    client = make_client(
        routes({"https://toss.tech/x": httpx.Response(200, text=article_page("글", LONG_BODY))})
    )

    a = fetch_article(client, FULL, "https://toss.tech/x?utm_source=rss", "글", None)

    assert a.url == "https://toss.tech/x"
    assert "캐시 무효화 전략" in a.markdown
    assert a.content_hash


def test_fetch_article_reads_title_and_date_from_page(make_client):
    from context_holder.collect import fetch_article

    page = (
        '<html><head><meta property="og:title" content="SNAP: LLM 비용 &amp; 지연"></head>'
        f"<body><article><p>2026년 10월 27일</p><p>{LONG_BODY}</p></article></body></html>"
    )
    toss = Source(
        "toss",
        "Toss",
        "domestic",
        "https://toss.tech/rss.xml",
        Mode.FEED,
        date_pattern=r"(\d{4})년 (\d{1,2})월 (\d{1,2})일",
    )
    client = make_client(routes({"https://toss.tech/article/snap": httpx.Response(200, text=page)}))

    a = fetch_article(client, toss, "https://toss.tech/article/snap")

    assert a.title == "SNAP: LLM 비용 & 지연"
    assert a.published == datetime(2026, 10, 27, tzinfo=UTC)


def test_fetch_article_falls_back_to_published_time_meta_and_title_tag(make_client):
    from context_holder.collect import fetch_article

    page = (
        "<html><head><title>DNS builds</title>"
        '<meta property="article:published_time" content="2026-11-02T14:00:00.000Z"></head>'
        f"<body><article><p>{LONG_BODY}</p></article></body></html>"
    )
    client = make_client(routes({"https://toss.tech/x": httpx.Response(200, text=page)}))

    a = fetch_article(client, FULL, "https://toss.tech/x")

    assert a.title == "DNS builds"
    assert a.published == datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


def test_fetch_article_reads_title_and_date_from_body_api(make_client):
    from context_holder.collect import fetch_article
    from context_holder.sources import BodyApi

    kakao = Source(
        "kakao",
        "Kakao",
        "domestic",
        "https://tech.kakao.com/feed/",
        Mode.FETCH,
        body_api=BodyApi(
            "https://tech.kakao.com/api/v1/posts/{id}",
            r"/posts/(\d+)",
            "content",
            title_field="title",
            date_field="releaseDate",
        ),
    )
    client = make_client(
        routes(
            {
                "https://tech.kakao.com/api/v1/posts/700": httpx.Response(
                    200,
                    json={
                        "content": f"<p>{LONG_BODY}</p>",
                        "title": "옛 글",
                        "releaseDate": "2025.04.24",
                    },
                )
            }
        )
    )

    a = fetch_article(client, kakao, "https://tech.kakao.com/posts/700")

    assert (a.title, a.published) == ("옛 글", datetime(2025, 4, 24, tzinfo=UTC))


def test_known_title_and_date_win_over_page(make_client):
    from context_holder.collect import fetch_article

    client = make_client(
        routes(
            {
                "https://toss.tech/x": httpx.Response(
                    200, text=article_page("페이지 제목", LONG_BODY)
                )
            }
        )
    )
    when = datetime(2026, 1, 1, tzinfo=UTC)

    a = fetch_article(client, FULL, "https://toss.tech/x", title="목록 제목", published=when)

    assert (a.title, a.published) == ("목록 제목", when)
