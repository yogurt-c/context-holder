"""검색 모델 바꾸기 · 단락 벡터 채우기 (`ctx reembed`, plan 6장).

글마다 원문을 다시 가져와 단락 벡터를 `vectors/{모델}/`에 쓴다.
이미 있는 벡터는 건너뛰어 이어서 할 수 있다.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from context_holder.collect import Article, article_from_entry, fetch_article
from context_holder.embed import Embedder
from context_holder.extract import canonical_url
from context_holder.feed import FeedEntry, FetchError, fetch_feed
from context_holder.sources import Source
from context_holder.vectorize import write_paragraph_vectors
from context_holder.wiki import StoredArticle, Wiki


@dataclass(frozen=True)
class Failure:
    article_id: str
    title: str
    reason: str  # 원문을 다시 가져오지 못한 이유. 이 글은 위키 필드로만 검색된다


@dataclass(frozen=True)
class ReembedResult:
    written: int
    failed: tuple[Failure, ...]


Progress = Callable[[tuple[int, int]], None]  # (처리한 글, 전체 글)


def run_reembed(
    wiki: Wiki,
    client: httpx.Client,
    embedders: Sequence[Embedder],
    on_progress: Progress | None = None,
    switch: bool = True,
) -> ReembedResult:
    sources = {s.id: s for s in wiki.sources()}
    articles = list(wiki.articles())
    feeds: dict[str, dict[str, FeedEntry]] = {}
    written, failed = 0, []
    for n, stored in enumerate(articles, 1):
        todo = [e for e in embedders if wiki.read_vectors(e.model_id, stored.id) is None]
        if todo:
            try:
                article = _original(client, sources, feeds, stored)
            except FetchError as e:
                failed.append(Failure(stored.id, stored.title, str(e)))
            else:
                for embedder in todo:
                    write_paragraph_vectors(wiki, embedder, article)
                written += 1
        if on_progress:
            on_progress((n, len(articles)))
    if switch and len(embedders) == 1:
        model = embedders[0].model_id
        folder = wiki.root / "vectors"
        for other in (
            [p.name for p in folder.iterdir() if p.name != model] if folder.exists() else []
        ):
            wiki.remove_vectors(other)
        wiki.set_search_model(model)
    return ReembedResult(written, tuple(failed))


def _original(client, sources: dict[str, Source], feeds, stored: StoredArticle) -> Article:
    source = sources.get(stored.source_id)
    if source is None:
        raise FetchError("소스 설정에서 빠졌다")
    if source.archive is None:  # feed만 쓰는 소스는 원문 페이지를 막는 곳이 있어 feed 본문을 쓴다
        if source.id not in feeds:
            entries = fetch_feed(client, source, etag=None).entries
            feeds[source.id] = {canonical_url(e.url): e for e in entries}
        entry = feeds[source.id].get(stored.url)
        if entry is not None:
            return article_from_entry(client, source, entry)
    return fetch_article(client, source, stored.url, stored.title, stored.published)
