"""키워드(FTS5)와 의미(임베딩) 검색을 RRF로 합친다 (plan 6장)."""

from dataclasses import dataclass

from context_holder.classify import Bucket
from context_holder.embed import Embedder
from context_holder.index import Index, IndexedArticle

RRF_K = 60  # 순위 합산 상수. 한쪽에서만 상위인 글도 살아남게 한다
CANDIDATES = 50


@dataclass(frozen=True)
class SearchHit:
    article: IndexedArticle
    score: float
    matched: str  # 어디가 맞았나: "키워드", "핵심 내용 3번째 줄", "원문 5번째 단락" 등
    keyword_rank: int | None
    vector_rank: int | None
    similarity: float | None  # 의미 검색 유사도 (결과 없음 판단에 쓴다)


def search(
    index: Index,
    query: str,
    embedder: Embedder | None,
    limit: int = 10,
    buckets: tuple[Bucket, ...] = (Bucket.SAVE, Bucket.INBOX),
) -> list[SearchHit]:
    keyword = index.keyword(query, CANDIDATES, buckets)
    vector = index.vector(embedder.query(query), CANDIDATES, buckets) if embedder else []
    k_rank = {h.article_id: n for n, h in enumerate(keyword, 1)}
    v_hits = {h.article_id: (n, h) for n, h in enumerate(vector, 1)}
    scores: dict[str, float] = {}
    for aid, rank in k_rank.items():
        scores[aid] = scores.get(aid, 0.0) + 1 / (RRF_K + rank)
    for aid, (rank, _) in v_hits.items():
        scores[aid] = scores.get(aid, 0.0) + 1 / (RRF_K + rank)
    hits = []
    for aid, score in sorted(scores.items(), key=lambda kv: -kv[1])[:limit]:
        article = index.get(aid)
        if article is None:  # pragma: no cover - 같은 연결에서 지워지지 않는 한 일어나지 않는다
            continue
        v = v_hits.get(aid)
        hits.append(
            SearchHit(
                article=article,
                score=score,
                matched=v[1].label if v else "키워드",
                keyword_rank=k_rank.get(aid),
                vector_rank=v[0] if v else None,
                similarity=v[1].score if v else None,
            )
        )
    return hits
