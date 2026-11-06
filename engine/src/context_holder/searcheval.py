"""검색 평가셋(engine/eval/search-queries.yaml)으로 recall@5를 잰다."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from context_holder.embed import Embedder
from context_holder.index import Index
from context_holder.search import search

TOP_K = 5


@dataclass(frozen=True)
class EvalQuery:
    query: str
    kind: str
    expected: tuple[str, ...]  # 비어 있으면 위키에 답이 없는 질의


@dataclass(frozen=True)
class SearchEvalResult:
    hits: int
    total: int  # 답이 있는 질의 수
    by_kind: dict[str, tuple[int, int]]
    misses: tuple[str, ...]
    answered_top: tuple[float, ...]  # 답 있는 질의의 1위 유사도
    unanswered_top: tuple[float, ...]  # 답 없는 질의의 1위 유사도 (결과 없음 기준을 정할 때 쓴다)


def load_queries(path: Path) -> list[EvalQuery]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        EvalQuery(q["query"], q["kind"], tuple(q.get("expected") or ())) for q in data["queries"]
    ]


def evaluate_search(
    index: Index, queries: Sequence[EvalQuery], embedder: Embedder | None
) -> SearchEvalResult:
    by_kind: dict[str, tuple[int, int]] = {}
    misses: list[str] = []
    answered: list[float] = []
    unanswered: list[float] = []
    for q in queries:
        hits = search(index, q.query, embedder, limit=TOP_K)
        top = hits[0].similarity if hits and hits[0].similarity is not None else None
        if not q.expected:
            if top is not None:
                unanswered.append(top)
            continue
        if top is not None:
            answered.append(top)
        found = any(h.article.url in q.expected for h in hits)
        got, total = by_kind.get(q.kind, (0, 0))
        by_kind[q.kind] = (got + found, total + 1)
        if not found:
            misses.append(q.query)
    return SearchEvalResult(
        hits=sum(g for g, _ in by_kind.values()),
        total=sum(t for _, t in by_kind.values()),
        by_kind=by_kind,
        misses=tuple(misses),
        answered_top=tuple(answered),
        unanswered_top=tuple(unanswered),
    )
