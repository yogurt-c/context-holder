"""원문 단락을 검색 모델로 벡터화해 위키에 쓴다. 원문 텍스트는 쓰지 않는다."""

import logging

from context_holder.collect import Article
from context_holder.embed import Embedder
from context_holder.paragraphs import split_paragraphs
from context_holder.wiki import Wiki, article_id

log = logging.getLogger(__name__)


def write_paragraph_vectors(wiki: Wiki, embedder: Embedder, article: Article) -> int:
    paragraphs = split_paragraphs(article.markdown)
    vectors = embedder.passages(paragraphs)
    wiki.write_vectors(embedder.model_id, article_id(article.url), vectors, article.content_hash)
    return len(paragraphs)


def try_write_vectors(wiki: Wiki, embedder: Embedder | None, article: Article) -> None:
    """수집 중에는 벡터화가 실패해도 글 저장은 계속한다. 빠진 벡터는 ctx reembed가 채운다."""
    if embedder is None:
        return
    try:
        write_paragraph_vectors(wiki, embedder, article)
    except Exception as e:  # 모델 다운로드 · 런타임 오류가 수집을 멈추면 안 된다
        log.warning("단락 벡터 실패 %s: %s", article.url, e)
