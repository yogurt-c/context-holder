from collections.abc import Callable

import httpx
import pytest

LONG_BODY = " ".join(["캐시 무효화 전략을 이벤트 기반으로 바꾼 과정을 정리한다."] * 20)


def rss(items: list[dict[str, str]]) -> str:
    rows = "".join(
        f"""<item>
  <title>{i["title"]}</title>
  <link>{i["link"]}</link>
  <pubDate>{i.get("date", "Wed, 05 Nov 2026 01:00:00 GMT")}</pubDate>
  <description><![CDATA[{i.get("summary", "요약")}]]></description>
  {f"<content:encoded><![CDATA[{i['content']}]]></content:encoded>" if "content" in i else ""}
</item>"""
        for i in items
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>blog</title><link>https://blog.example.com</link>{rows}</channel></rss>"""


def article_page(title: str, body: str) -> str:
    return f"""<html><head><title>{title}</title></head><body>
<nav>홈 블로그 채용</nav>
<article><h1>{title}</h1><h2>배경</h2><p>{body}</p><p>{body}</p></article>
<footer>© example</footer></body></html>"""


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def make_client() -> Callable[[Handler], httpx.Client]:
    def _make(handler: Handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return _make


@pytest.fixture(autouse=True)
def no_network_politeness(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트에서는 재시도 · 요청 간격 대기와 robots.txt 확인을 끈다.

    요청 수를 세는 테스트가 있어서다.
    """
    monkeypatch.setattr("context_holder.feed.time.sleep", lambda _: None)
    monkeypatch.setattr("context_holder.feed.RESPECT_ROBOTS", False)


class FakeEmbedder:
    """단어(토큰)가 겹칠수록 가까워지는 결정적 임베더. 실제 모델 없이 검색 흐름을 시험한다."""

    def __init__(
        self, model_id: str = "fake", dim: int = 64, synonyms: dict[str, str] | None = None
    ):
        self.model_id = model_id
        self.dim = dim
        self.synonyms = synonyms or {}  # 다른 말을 같은 뜻으로 묶는다 (실제 모델의 의미 이해 흉내)
        self.calls = 0

    def passages(self, texts):
        import numpy as np

        self.calls += 1
        return np.array([self._vec(t) for t in texts], dtype=np.float32).reshape(
            len(texts), self.dim
        )

    def query(self, text):
        return self._vec(text)

    def _vec(self, text):
        import hashlib

        import numpy as np

        from context_holder.textproc import search_tokens

        v = np.zeros(self.dim, dtype=np.float32)
        for token in search_tokens(text):
            token = self.synonyms.get(token, token)
            v[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = np.linalg.norm(v)
        return v / norm if norm else v


@pytest.fixture(autouse=True)
def fake_search_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI가 실제 검색 모델을 내려받지 않게 한다."""
    monkeypatch.setattr("context_holder.cli.make_embedder", lambda model_id: FakeEmbedder(model_id))
