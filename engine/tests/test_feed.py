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
