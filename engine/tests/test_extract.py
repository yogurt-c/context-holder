import pytest

from context_holder.extract import canonical_url, content_hash, html_to_markdown
from tests.conftest import LONG_BODY, article_page


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Toss.Tech/article/abc/", "https://toss.tech/article/abc"),
        (
            "https://medium.com/daangn/post-1?source=rss----abc&utm_medium=x#top",
            "https://medium.com/daangn/post-1",
        ),
        (
            "https://d2.naver.com/helloworld/123?b=2&a=1",
            "https://d2.naver.com/helloworld/123?a=1&b=2",
        ),
        ("https://example.com/", "https://example.com/"),
    ],
)
def test_canonical_url_strips_tracking_and_normalizes(raw: str, expected: str):
    assert canonical_url(raw) == expected


def test_html_to_markdown_keeps_article_and_drops_chrome():
    md = html_to_markdown(article_page("캐시 전략", LONG_BODY), url="https://a.example/p")

    assert "배경" in md
    assert "캐시 무효화 전략" in md
    assert "채용" not in md


def test_html_to_markdown_returns_empty_for_empty_html():
    assert html_to_markdown("", url="https://a.example/p") == ""


def test_content_hash_ignores_whitespace_differences():
    assert content_hash("a  b\n\nc") == content_hash("a b c")
    assert content_hash("a b c") != content_hash("a b d")


def test_html_to_markdown_handles_feed_fragments():
    fragment = f"<div><h2>배경</h2><p>{LONG_BODY}</p></div>"

    md = html_to_markdown(fragment, url="https://a.example/p")

    assert "배경" in md
    assert "캐시 무효화 전략" in md
