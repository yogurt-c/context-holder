from datetime import UTC, datetime

import httpx
import pytest

from context_holder.feed import FetchError, fetch_feed
from context_holder.sources import Mode, Source
from tests.conftest import rss

SOURCE = Source(
    id="toss", name="Toss", region="domestic", feed_url="https://toss.tech/rss.xml", mode=Mode.FEED
)


def test_parses_entries_and_returns_etag(make_client):
    body = rss(
        [{"title": "글 하나", "link": "https://toss.tech/article/1", "content": "<p>본문</p>"}]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "context-holder" in request.headers["user-agent"]
        return httpx.Response(200, text=body, headers={"etag": '"v1"'})

    result = fetch_feed(make_client(handler), SOURCE, etag=None)

    assert result.etag == '"v1"'
    assert not result.not_modified
    [entry] = result.entries
    assert entry.title == "글 하나"
    assert entry.url == "https://toss.tech/article/1"
    assert entry.published == datetime(2026, 11, 5, 1, 0, tzinfo=UTC)
    assert "본문" in entry.html


def test_sends_etag_and_handles_not_modified(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    result = fetch_feed(make_client(handler), SOURCE, etag='"v1"')

    assert result.not_modified
    assert result.entries == ()
    assert result.etag == '"v1"'


def test_falls_back_to_summary_when_no_content(make_client):
    body = rss([{"title": "t", "link": "https://toss.tech/a", "summary": "<p>요약만</p>"}])
    client = make_client(lambda _: httpx.Response(200, text=body))

    [entry] = fetch_feed(client, SOURCE, etag=None).entries

    assert "요약만" in entry.html


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(200, text="<html>not a feed</html>")],
)
def test_raises_fetch_error_on_bad_response(make_client, response: httpx.Response):
    with pytest.raises(FetchError):
        fetch_feed(make_client(lambda _: response), SOURCE, etag=None)


def test_raises_fetch_error_on_network_failure(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    with pytest.raises(FetchError, match="timeout"):
        fetch_feed(make_client(handler), SOURCE, etag=None)


def test_skips_entries_without_link(make_client):
    body = rss(
        [{"title": "링크 없음", "link": ""}, {"title": "정상", "link": "https://toss.tech/ok"}]
    )

    result = fetch_feed(make_client(lambda _: httpx.Response(200, text=body)), SOURCE, etag=None)

    assert [e.title for e in result.entries] == ["정상"]


def test_retries_transient_server_errors(make_client, monkeypatch):
    monkeypatch.setattr("context_holder.feed.time.sleep", lambda _: None)
    calls = []
    body = rss([{"title": "t", "link": "https://toss.tech/a"}])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(504) if len(calls) < 3 else httpx.Response(200, text=body)

    result = fetch_feed(make_client(handler), SOURCE, etag=None)

    assert len(calls) == 3
    assert len(result.entries) == 1


def test_gives_up_after_retries(make_client, monkeypatch):
    monkeypatch.setattr("context_holder.feed.time.sleep", lambda _: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(FetchError, match="refused"):
        fetch_feed(make_client(handler), SOURCE, etag=None)
    assert len(calls) == 3


def test_does_not_retry_client_errors(make_client, monkeypatch):
    monkeypatch.setattr("context_holder.feed.time.sleep", lambda _: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(FetchError, match="404"):
        fetch_feed(make_client(handler), SOURCE, etag=None)
    assert len(calls) == 1


def test_robots_txt_disallow_blocks_request(make_client, monkeypatch):
    import context_holder.feed as feed_mod

    monkeypatch.setattr(feed_mod, "RESPECT_ROBOTS", True)
    monkeypatch.setattr(feed_mod, "_robots", {})
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text="ok")

    client = make_client(handler)

    with pytest.raises(FetchError, match=r"robots\.txt"):
        feed_mod.get(client, "https://blog.example.com/private/x")
    assert feed_mod.get(client, "https://blog.example.com/public").status_code == 200
    assert calls.count("/robots.txt") == 1  # 사이트마다 한 번만 읽는다


def test_missing_robots_txt_allows_everything(make_client, monkeypatch):
    import context_holder.feed as feed_mod

    monkeypatch.setattr(feed_mod, "RESPECT_ROBOTS", True)
    monkeypatch.setattr(feed_mod, "_robots", {})
    client = make_client(lambda r: httpx.Response(404 if r.url.path == "/robots.txt" else 200))

    assert feed_mod.get(client, "https://blog.example.com/a").status_code == 200


def test_requests_to_same_host_are_spaced(make_client, monkeypatch):
    import context_holder.feed as feed_mod

    waits = []
    monkeypatch.setattr(feed_mod.time, "sleep", waits.append)
    monkeypatch.setattr(feed_mod, "_last_request", {})
    clock = iter([100.0, 100.0, 100.2, 100.2, 200.0, 200.0])
    monkeypatch.setattr(feed_mod.time, "monotonic", lambda: next(clock))
    client = make_client(lambda r: httpx.Response(200))

    feed_mod.get(client, "https://a.example/1")
    feed_mod.get(client, "https://a.example/2")

    assert waits and waits[0] == pytest.approx(0.8)


def test_unreachable_robots_txt_allows(make_client, monkeypatch):
    import context_holder.feed as feed_mod

    monkeypatch.setattr(feed_mod, "RESPECT_ROBOTS", True)
    monkeypatch.setattr(feed_mod, "_robots", {})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200)

    assert feed_mod.get(make_client(handler), "https://blog.example.com/a").status_code == 200
