"""Search 화면 (plan 7장). 검색은 MCP와 같은 경로(키워드 + 의미, RRF)를 쓴다.

필터(종류 · 기간 · 회사 · 태그)는 화면이 거른다.
결과를 누르면 글 상세 모달을 연다 (articleview). 회사 · 태그 목록은 종류와 기간만 거른 결과에서
개수와 함께 만든다. 질의 확장("함께 찾은 말")은 하지 않는다: 질의 확장은 Claude가 한다 (plan 8장).
"""

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from context_holder.index import Index
from context_holder.mcptools import NO_MODEL_NOTE, WikiTools
from context_holder.search import SearchHit
from context_holder.wiki import StoredArticle, Wiki

PERIODS = {"30d": ("최근 30일", 30), "1y": ("최근 1년", 365), "all": ("전체", None)}
KINDS = {"all": "전체", "decision": "결정 사례"}
NO_MODEL_SCREEN = (
    "의미 검색 모델이 아직 없어 단어가 맞는 글만 찾았어요. 설정에서 내려받을 수 있어요."
)
TAG_FACETS = 8
TOPIC_LINKS = 8
MIN_TERM = 2

Segments = list[tuple[str, bool]]  # (글, 강조 여부). 템플릿이 이스케이프해 그린다


@dataclass(frozen=True)
class Query:
    q: str = ""
    kind: str = "all"
    period: str = "all"
    companies: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def url(self, **change) -> str:
        target = replace(self, **change)
        pairs = [("q", target.q)] if target.q else []
        if target.kind != "all":
            pairs.append(("kind", target.kind))
        if target.period != "all":
            pairs.append(("period", target.period))
        pairs += [("company", c) for c in target.companies]
        pairs += [("tag", t) for t in target.tags]
        return "/search" + (f"?{urlencode(pairs)}" if pairs else "")

    def toggle(self, name: str, value: str) -> str:
        """회사 · 태그 필터 하나를 켜거나 끈 주소."""
        current: tuple[str, ...] = getattr(self, name)
        values = tuple(v for v in current if v != value)
        if value not in current:
            values = (*current, value)
        return self.url(**{name: values})


@dataclass(frozen=True)
class Facet:
    value: str
    label: str
    count: int
    on: bool


@dataclass(frozen=True)
class Result:
    hit: SearchHit
    stored: StoredArticle
    company: str
    title: Segments
    snippet: Segments
    where: str  # 어디가 맞았나


@dataclass(frozen=True)
class SearchView:
    query: Query
    results: list[Result] = field(default_factory=list)
    companies: list[Facet] = field(default_factory=list)
    tags: list[Facet] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    article_total: int = 0
    decision_total: int = 0
    topics: list[str] = field(default_factory=list)  # 검색어가 없을 때 보여 줄 결정 주제


def parse_query(params: Mapping[str, str], lists: Mapping[str, list[str]]) -> Query:
    kind = params.get("kind", "all")
    period = params.get("period", "all")
    return Query(
        q=(params.get("q") or "").strip()[:200],
        kind=kind if kind in KINDS else "all",
        period=period if period in PERIODS else "all",
        companies=tuple(dict.fromkeys(lists.get("company", []))),
        tags=tuple(dict.fromkeys(lists.get("tag", []))),
    )


def build_search(
    tools: WikiTools, wiki: Wiki, index: Index, query: Query, names: dict[str, str], now: datetime
) -> SearchView:
    totals = {
        "article_total": len(index.select()),
        "decision_total": sum(n for _, n in index.topics()),
    }
    if not query.q:
        topics = [name for name, _ in index.topics()[:TOPIC_LINKS]]
        return SearchView(query, topics=topics, **totals)
    started = time.perf_counter()
    hits, notes = tools.all_hits([query.q])
    notes = [NO_MODEL_SCREEN if n == NO_MODEL_NOTE else n for n in notes]
    terms = _terms(query.q)
    pool = [r for r in _results(wiki, hits, names, terms) if _keeps(r, query, now)]
    results = [
        r
        for r in pool
        if (not query.companies or r.stored.source_id in query.companies)
        and (not query.tags or set(query.tags) & set(r.stored.tags))
    ]
    return SearchView(
        query,
        results,
        _company_facets(pool, query),
        _tag_facets(pool, query),
        notes,
        time.perf_counter() - started,
        **totals,
    )


def highlight(text: str, terms: list[str]) -> Segments:
    if not terms or not text:
        return [(text, False)]
    pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    out: Segments = []
    end = 0
    for m in pattern.finditer(text):
        if m.start() > end:
            out.append((text[end : m.start()], False))
        out.append((m.group(0), True))
        end = m.end()
    if end < len(text):
        out.append((text[end:], False))
    return out


# ---------- 내부 ----------


def _terms(q: str) -> list[str]:
    words = {w.lower() for w in q.split() if len(w) >= MIN_TERM}
    return sorted(words, key=len, reverse=True)  # 긴 말을 먼저 맞춘다


def _results(wiki: Wiki, hits: list[SearchHit], names: dict[str, str], terms: list[str]):
    for hit in hits:
        stored = wiki.read(Path(hit.article.path))
        if stored is None:  # 다른 기기의 pull로 사라졌다
            continue
        text, where = _snippet(stored, hit.matched, terms)
        yield Result(
            hit,
            stored,
            names.get(stored.source_id, stored.source_id),
            highlight(stored.title, terms),
            highlight(text, terms),
            where,
        )


def _snippet(a: StoredArticle, matched: str, terms: list[str]) -> tuple[str, str]:
    first = a.summary[0] if a.summary else ""
    d = a.decision or {}
    line = re.match(r"(요약|핵심 내용) (\d+)번째 줄", matched)
    if line:
        lines = a.summary if line.group(1) == "요약" else a.key_points
        n = int(line.group(2)) - 1
        return (lines[n] if n < len(lines) else first), matched
    if matched == "결정 기록":
        return str(d.get("problem") or first), matched
    if matched.startswith("원문"):  # 원문 텍스트는 없다. 요약 첫 줄로 대신한다
        return first, f"{matched}에서 맞음"
    if matched == "키워드":
        candidates = [*a.summary, *a.key_points, str(d.get("problem") or "")]
        found = next((c for c in candidates if any(t in c.lower() for t in terms)), first)
        return found, "키워드"
    return first, matched


def _keeps(r: Result, query: Query, now: datetime) -> bool:
    if query.kind == "decision" and not r.stored.decision:
        return False
    days = PERIODS[query.period][1]
    if days is None:
        return True
    moment = r.stored.published or r.stored.collected
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=now.tzinfo)
    return moment >= now - timedelta(days=days)


def _company_facets(pool: list[Result], query: Query) -> list[Facet]:
    counts: dict[str, tuple[str, int]] = {}
    for r in pool:
        label, n = counts.get(r.stored.source_id, (r.company, 0))
        counts[r.stored.source_id] = (label, n + 1)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1][1], kv[1][0]))
    ordered += [(c, (c, 0)) for c in query.companies if c not in counts]  # 고른 회사는 남긴다
    return [Facet(sid, label, n, sid in query.companies) for sid, (label, n) in ordered]


def _tag_facets(pool: list[Result], query: Query) -> list[Facet]:
    counts: dict[str, int] = {}
    for r in pool:
        for tag in r.stored.tags:
            counts[tag] = counts.get(tag, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TAG_FACETS]
    shown = {t for t, _ in top}
    top += [(t, counts.get(t, 0)) for t in query.tags if t not in shown]  # 고른 태그는 남긴다
    return [Facet(t, t, n, t in query.tags) for t, n in top]
