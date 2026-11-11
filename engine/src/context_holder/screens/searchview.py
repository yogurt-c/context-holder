"""Search 화면 (plan 7장). 검색은 MCP와 같은 경로(키워드 + 의미, RRF)를 쓴다.

필터(종류 · 기간 · 회사 · 태그)는 화면이 인덱스 값만으로 거른다. 마크다운은 보이는 쪽만 읽는다.
결과는 PAGE_SIZE건씩 커서("더 보기")로 붙인다. 커서는 그 쪽 마지막 글 id다.
결과를 누르면 글 상세 모달을 연다 (articleview). 회사 · 태그 목록은 종류와 기간만 거른 결과에서
만든다. 질의 확장("함께 찾은 말")은 하지 않는다: 질의 확장은 Claude가 한다 (plan 8장).
"""

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from context_holder.index import Index, IndexedArticle
from context_holder.mcptools import NO_MODEL_NOTE, WikiTools
from context_holder.screens.decisionsview import DecisionsView, browse_topics
from context_holder.screens.views import Page, after_cursor
from context_holder.search import SearchHit
from context_holder.wiki import StoredArticle, Wiki

PERIODS = {"30d": ("최근 30일", 30), "1y": ("최근 1년", 365), "all": ("전체", None)}
KINDS = {"all": "전체", "decision": "결정 사례"}
NO_MODEL_SCREEN = (
    "의미 검색 모델이 아직 없어 단어가 맞는 글만 찾았어요. 설정에서 내려받을 수 있어요."
)
TAG_FACETS = 8
PAGE_SIZE = 20
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

    def more_url(self, after: str) -> str:
        """다음 쪽 결과 주소. 검색어 · 필터는 그대로 넘긴다."""
        params = self.url().partition("?")[2]
        return f"/search/more?{params}&{urlencode({'after': after})}"

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
    results: list[Result] = field(default_factory=list)  # 첫 쪽
    after: str | None = None  # 다음 쪽 커서
    total: int = 0  # 필터까지 건 결과 수
    companies: list[Facet] = field(default_factory=list)
    tags: list[Facet] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    article_total: int = 0
    # 검색어가 없을 때 보여 줄 주제: (분야, [(이름, 주소)]). 정리한 주제는 Decisions 비교표로 간다
    browse: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    # 검색어가 없을 때 보여 줄 Claude Code 질문 예시: (설명, 문장)
    ask: list[tuple[str, str]] = field(default_factory=list)
    connected: bool | None = None  # Claude Code가 이 위키를 쓰나. None이면 모른다 (개발 모드)


# 위키에 주제가 아직 없을 때 예시에 쓰는 주제
EXAMPLE_TOPICS = ("메시지 큐 선택", "캐시 전략")


def claude_prompts(browse: list[tuple[str, list[tuple[str, str]]]]) -> list[tuple[str, str]]:
    """Claude Code에 그대로 붙여 넣을 질문. 내 위키에 있는 주제로 채워 바로 결과가 나오게 한다."""
    names = [name for _, links in browse for name, _ in links] or list(EXAMPLE_TOPICS)
    first, second = names[0], names[1] if len(names) > 1 else names[0]
    return [
        (
            "그냥 물어보기",
            f"{first} 관련 결정을 앞두고 있어. "
            "다른 회사들은 어떤 선택지를 두고 뭘 따졌는지 정리해 줘",
        ),
        (
            "위키 지정",
            f"Context Holder 위키에서 {second} 사례를 찾아서 "
            "선택지와 트레이드오프를 표로 비교해 줘",
        ),
        ("더 깊이", "찾은 사례 중 하나의 결정 기록과 원문 근거 문장을 보여 줘"),
    ]


def _browse(wiki: Wiki, index: Index, query: Query) -> list[tuple[str, list[tuple[str, str]]]]:
    groups = browse_topics(wiki, index)
    if groups:
        return [(area, [(n, DecisionsView().url(sel=n)) for n in names]) for area, names in groups]
    # 주제를 아직 정리하지 않았으면 사례가 많은 결정 주제를 검색어로 건다
    names = [name for name, _ in index.topics()[:TOPIC_LINKS]]
    return [("", [(n, query.url(q=n, kind="decision")) for n in names])] if names else []


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
    tools: WikiTools,
    wiki: Wiki,
    index: Index,
    query: Query,
    names: dict[str, str],
    now: datetime,
    connected: Callable[[], bool | None] = lambda: None,
) -> SearchView:
    totals = {"article_total": index.count()}
    if not query.q:
        browse = _browse(wiki, index, query)
        return SearchView(
            query, browse=browse, ask=claude_prompts(browse), connected=connected(), **totals
        )
    started = time.perf_counter()
    pool, results, notes = _hits(tools, query, now)
    shown = results[:PAGE_SIZE]
    return SearchView(
        query,
        list(_results(wiki, shown, names, _terms(query.q))),
        shown[-1].article.id if len(results) > PAGE_SIZE else None,
        len(results),
        _company_facets(pool, query, names),
        _tag_facets(pool, query),
        notes,
        time.perf_counter() - started,
        **totals,
    )


def search_more(
    tools: WikiTools,
    wiki: Wiki,
    query: Query,
    names: dict[str, str],
    now: datetime,
    after: str,
) -> Page:
    """Search "더 보기": after 글 다음 PAGE_SIZE건. 검색을 다시 돌려 같은 순서에서 자른다."""
    _, results, _ = _hits(tools, query, now)
    page = after_cursor(results, after, PAGE_SIZE, lambda h: h.article.id)
    return Page(list(_results(wiki, page.items, names, _terms(query.q))), page.after)


def _hits(
    tools: WikiTools, query: Query, now: datetime
) -> tuple[list[SearchHit], list[SearchHit], list[str]]:
    """(종류 · 기간만 거른 후보, 회사 · 태그까지 거른 결과, 알림)."""
    hits, notes = tools.all_hits([query.q])
    notes = [NO_MODEL_SCREEN if n == NO_MODEL_NOTE else n for n in notes]
    pool = [h for h in hits if _keeps(h.article, query, now)]
    results = [
        h
        for h in pool
        if (not query.companies or h.article.source_id in query.companies)
        and (not query.tags or set(query.tags) & set(h.article.tags))
    ]
    return pool, results, notes


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
    """보이는 쪽만 마크다운을 읽는다."""
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


def _keeps(a: IndexedArticle, query: Query, now: datetime) -> bool:
    if query.kind == "decision" and not a.has_decision:
        return False
    days = PERIODS[query.period][1]
    if days is None:
        return True
    when = a.published or a.collected
    if when is None:
        return False
    moment = datetime.fromisoformat(when)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=now.tzinfo)
    return moment >= now - timedelta(days=days)


def _company_facets(pool: list[SearchHit], query: Query, names: dict[str, str]) -> list[Facet]:
    """후보가 많은 회사부터. 고른 회사는 후보에 없어도 남긴다."""
    counts: dict[str, int] = {}
    for h in pool:
        counts[h.article.source_id] = counts.get(h.article.source_id, 0) + 1
    ordered = sorted(counts, key=lambda sid: (-counts[sid], names.get(sid, sid)))
    ordered += [c for c in query.companies if c not in counts]
    return [Facet(sid, names.get(sid, sid), sid in query.companies) for sid in ordered]


def _tag_facets(pool: list[SearchHit], query: Query) -> list[Facet]:
    """후보에 많이 붙은 태그 몇 개. 고른 태그는 후보에 없어도 남긴다."""
    counts: dict[str, int] = {}
    for h in pool:
        for tag in h.article.tags:
            counts[tag] = counts.get(tag, 0) + 1
    top = sorted(counts, key=lambda t: (-counts[t], t))[:TAG_FACETS]
    top += [t for t in query.tags if t not in top]
    return [Facet(t, t, t in query.tags) for t in top]
