"""MCP tool의 동작 (plan 8장). Claude가 기술 결정 전에 위키를 직접 검색한다.

- 질의 확장은 호출자(Claude)가 한다. 여러 질의를 받아 RRF로 합친다.
- 단계적으로 공개한다. 검색은 짧은 목록만, 상세는 id로 다시 요청한다.
- 결과가 답인지는 호출자가 요약을 보고 판단한다. 유사도 · 키워드 포함률로는 답 없는 질의를
  가를 수 없었다 (engine/eval). 결과가 0건이면 있는 결정 주제를 함께 준다.
- 모든 사례에 원문 URL을 넣는다. 응답은 tool마다 글자 수 예산 안으로 자른다.
"""

import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from context_holder import topics as topicmod
from context_holder.classify import Bucket
from context_holder.embed import Embedder
from context_holder.feedback import Kind, record_feedback
from context_holder.index import Index, normalize_topic
from context_holder.search import RRF_K, SearchHit, search
from context_holder.sources import SourceConfigError
from context_holder.wiki import StoredArticle, Wiki

# 응답 글자 수 예산. plan의 토큰 예산(한국어는 대략 글자 1~2개가 토큰 1개)에 맞춘다
BUDGET = {
    "list_topics": 1500,
    "search_wiki": 4000,
    "find_decision_cases": 5000,
    "get_decision": 3000,
    "get_article": 7000,
}
MAX_QUERIES = 5
CANDIDATES = 30
SECTIONS = ("요약", "핵심 내용", "결정 기록")
JUDGE_NOTE = (
    "관련도 순 목록일 뿐 답이라는 보장은 없다. 요약을 보고 질문과 맞지 않으면 "
    "위키에 없다고 답하고, 웹에서 찾은 좋은 글은 suggest_source로 남긴다."
)
NO_MODEL_NOTE = "검색 모델이 아직 없어 키워드로만 찾았다. 앱이 모델을 내려받으면 의미 검색도 한다."

Clock = Callable[[], datetime]


@dataclass
class WikiTools:
    index: Index
    wiki: Wiki
    embedder: Callable[[], Embedder | None]  # 첫 검색 때 불러온다 (서버는 바로 뜬다)
    device: str
    log_path: Path
    clock: Clock
    _embedder: Embedder | None = field(default=None, init=False)
    _embedder_ready: bool = field(default=False, init=False)
    _names: dict[str, str] | None = field(default=None, init=False)
    _started: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def call(self, tool: str, **kwargs: Any) -> str:
        """서버가 tool을 부르는 입구. 한 번에 하나씩 처리하고 걸린 시간을 질의 로그에 남긴다."""
        with self._lock:  # 인덱스 연결 · 검색 모델 · 로그 파일을 스레드끼리 나눠 쓰지 않게
            self._started = time.perf_counter()
            return getattr(self, tool)(**kwargs)

    # ---------- tool ----------

    def list_topics(self, prefix: str | None = None) -> str:
        """넓은 주제(Decisions 화면과 같은 묶음)와 아직 분류하지 않은 주제."""
        grouped, loose = _grouped_topics(self.index.topics(), topicmod.load(self.wiki))
        if prefix:
            key = normalize_topic(prefix)
            grouped = [
                g for g in grouped if any(normalize_topic(t).startswith(key) for t in (g[0], *g[2]))
            ]
            loose = [t for t in loose if normalize_topic(t[0]).startswith(key)]
        if not grouped and not loose:
            return self._log("list_topics", {"prefix": prefix}, [], "결정 주제가 없다.")
        lines = []
        if grouped:
            lines.append(f"결정 주제 {len(grouped)}개 (사례 수)")
            lines += [f"- {name} ({n})" for name, n, _ in grouped]
        if loose:  # 한 번도 정리하지 않았으면 원래 주제가 곧 결정 주제다
            head = "아직 묶지 않은 주제" if grouped else "결정 주제"
            lines.append(f"{head} {len(loose)}개 (사례 수)")
            lines += [f"- {name} ({n})" for name, n in loose]
        if grouped:
            lines.append("find_decision_cases(topic)에 주제 이름을 주면 그 주제 사례를 앞에 둔다.")
        return self._log("list_topics", {"prefix": prefix}, [], _fit(lines, "list_topics"))

    def search_wiki(
        self,
        queries: Sequence[str],
        company: str | None = None,
        since: str | None = None,
        limit: int = 8,
    ) -> str:
        args = {"queries": list(queries), "company": company, "since": since, "limit": limit}
        hits, notes = self.search_hits(queries, company, since, limit)
        if not hits:
            return self._log("search_wiki", args, [], self._empty(notes))
        lines = [*notes]
        for hit in hits:
            a = hit.article
            stored = self.wiki.read(Path(a.path))
            summary = stored.summary[0] if stored and stored.summary else ""
            inbox = " (보류)" if a.bucket is Bucket.INBOX else ""
            day = (a.published or "")[:10] or "날짜 모름"
            lines.append(
                f"- [{a.id}] {a.title}{inbox}\n"
                f"  {self._company(a.source_id)} · {day} · 맞은 곳: {hit.matched}"
                + (f"\n  결정 주제: {a.decision_topic}" if a.decision_topic else "")
                + (f"\n  {summary}" if summary else "")
                + f"\n  {a.url}"
            )
        lines += ["상세는 get_article(id), 결정 기록은 get_decision(id)로 본다.", JUDGE_NOTE]
        ids = [h.article.id for h in hits]
        return self._log("search_wiki", args, ids, _fit(lines, "search_wiki"))

    def find_decision_cases(
        self, topic: str | None = None, problem: str | None = None, limit: int = 5
    ) -> str:
        args = {"topic": topic, "problem": problem, "limit": limit}
        if not any(q and q.strip() for q in (topic, problem)):
            return self._log("find_decision_cases", args, [], "topic이나 problem을 준다.")
        cases, notes = self.decision_hits(topic, problem, limit)
        if not cases:
            return self._log("find_decision_cases", args, [], self._empty(notes))
        lines = [*notes]
        for hit in cases:
            stored = self.wiki.read(Path(hit.article.path))
            if stored and stored.decision:
                lines.append(_case(hit.article.id, stored, self._company(stored.source_id)))
        lines += ["근거 문장까지 보려면 get_decision(id)를 부른다.", JUDGE_NOTE]
        ids = [h.article.id for h in cases]
        return self._log("find_decision_cases", args, ids, _fit(lines, "find_decision_cases"))

    def get_decision(self, id: str) -> str:
        stored = self._stored(id)
        if stored is None or not stored.decision:
            return self._log("get_decision", {"id": id}, [], f"{id}에는 결정 기록이 없다.")
        lines = [
            f"# {stored.title}",
            f"{self._company(stored.source_id)} · {_date(stored)} · {stored.url}",
            *_decision_lines(stored.decision),
        ]
        return self._log("get_decision", {"id": id}, [id], _fit(lines, "get_decision"))

    def get_article(self, id: str, section: str | None = None) -> str:
        args = {"id": id, "section": section}
        stored = self._stored(id)
        if stored is None:
            return self._log("get_article", args, [], f"{id} 글이 없다.")
        if section and section not in SECTIONS:
            return self._log("get_article", args, [], f"section은 {', '.join(SECTIONS)} 중 하나다.")
        lines = [
            f"# {stored.title}",
            f"{self._company(stored.source_id)} · {_date(stored)} · {stored.url}",
        ]
        if stored.tags:
            lines.append(f"태그: {', '.join(stored.tags)}")
        if section in (None, "요약"):
            lines += ["## 요약", *(f"- {s}" for s in stored.summary)]
        if section in (None, "핵심 내용"):
            lines += ["## 핵심 내용", *(f"- {k}" for k in stored.key_points)]
        if section == "결정 기록":
            lines += _decision_lines(stored.decision) if stored.decision else ["결정 기록이 없다."]
        elif stored.decision:
            lines.append("결정 기록이 있다: get_decision(id)")
        if stored.status == "pending":
            lines.append("아직 요약하지 못한 글이다. 원문 링크를 본다.")
        return self._log("get_article", args, [id], _fit(lines, "get_article"))

    def suggest_source(self, url: str, reason: str) -> str:
        args = {"url": url, "reason": reason}
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return self._log("suggest_source", args, [], "http(s) 주소를 준다.")
        record_feedback(self.wiki, self.device, Kind.SUGGEST_SOURCE, url, self.clock(), reason)
        return self._log(
            "suggest_source", args, [], "남겼다. 다음 수집 때 소스 추가 후보로 보여 준다."
        )

    # ---------- 순위 (평가도 같은 경로를 쓴다) ----------

    def search_hits(
        self,
        queries: Sequence[str],
        company: str | None = None,
        since: str | None = None,
        limit: int = 8,
    ) -> tuple[list[SearchHit], list[str]]:
        hits, notes = self._search(queries, company, since)
        return hits[: max(1, min(limit, 20))], notes

    def all_hits(self, queries: Sequence[str]) -> tuple[list[SearchHit], list[str]]:
        """후보 전체 (화면 검색: 회사 · 태그 · 기간은 화면이 거른다)."""
        return self._search(queries, None, None)

    def decision_hits(
        self, topic: str | None = None, problem: str | None = None, limit: int = 5
    ) -> tuple[list[SearchHit], list[str]]:
        # 짧은 topic을 따로 검색해 합치면 topic 낱말만 맞는 글이 순위를 흐린다. 한 질의로 묻는다
        query = " ".join(q.strip() for q in (topic, problem) if q and q.strip())
        hits, notes = self._search([query], None, None)
        cases = [h for h in hits if h.article.decision_topic]
        if topic:  # 같은 넓은 주제 · 같은 원래 주제의 사례를 앞에 둔다
            key = normalize_topic(topic)
            group = topicmod.load(self.wiki).group(topic)
            members = {normalize_topic(t) for t in group.topics} if group else set()
            found = {h.article.id for h in cases}
            cases += [  # 검색에 걸리지 않은 같은 주제 사례도 넣는다
                SearchHit(a, 0.0, "같은 결정 주제", None, None, None)
                for a in self.index.select("WHERE decision_topic IS NOT NULL")
                if normalize_topic(a.decision_topic or "") in members and a.id not in found
            ]

            def rank(h: SearchHit) -> tuple[bool, bool]:
                own = normalize_topic(h.article.decision_topic or "")
                return own not in members and own != key, own != key

            cases.sort(key=rank)
        return cases[: max(1, min(limit, 10))], notes

    # ---------- 내부 ----------

    def _search(
        self, queries: Sequence[str], company: str | None, since: str | None
    ) -> tuple[list[SearchHit], list[str]]:
        queries = [q.strip() for q in queries if q and q.strip()][:MAX_QUERIES]
        embedder, notes = self._model()
        scores: dict[str, float] = {}
        best: dict[str, SearchHit] = {}
        for q in queries:
            for rank, hit in enumerate(search(self.index, q, embedder, CANDIDATES), 1):
                aid = hit.article.id
                scores[aid] = scores.get(aid, 0.0) + 1 / (RRF_K + rank)
                if aid not in best or (hit.keyword_rank and not best[aid].keyword_rank):
                    best[aid] = hit
        source = self._source_filter(company)
        hits = [
            best[aid]
            for aid in sorted(scores, key=lambda a: -scores[a])
            if (source is None or best[aid].article.source_id in source)
            and (not since or (best[aid].article.published or "") >= since)
        ]
        return hits, notes

    def _model(self) -> tuple[Embedder | None, list[str]]:
        if not self._embedder_ready:
            try:
                self._embedder = self.embedder()
                if self._embedder:
                    self._embedder.query("warm up")  # 모델이 없으면 여기서 실패한다
            except Exception:  # 모델이 없거나 깨졌으면 키워드 검색으로 답한다
                self._embedder = None
            self._embedder_ready = True
        return self._embedder, [] if self._embedder else [NO_MODEL_NOTE]

    def _source_filter(self, company: str | None) -> set[str] | None:
        if not company:
            return None
        key = company.strip().lower()
        names = self._source_names()
        return {sid for sid, name in names.items() if key in (sid.lower(), name.lower())} or {key}

    def _source_names(self) -> dict[str, str]:
        if self._names is None:
            try:
                self._names = {s.id: s.name for s in self.wiki.sources()}
            except (OSError, SourceConfigError):  # 설정이 아직 없는 위키
                self._names = {}
        return self._names

    def _company(self, source_id: str) -> str:
        return self._source_names().get(source_id, source_id)

    def _empty(self, notes: list[str]) -> str:
        topics = ", ".join(name for name, _ in self.index.topics()[:8])
        return "\n".join(
            [*notes, "위키에서 맞는 글을 찾지 못했다.", f"있는 결정 주제 예: {topics or '없음'}"]
        )

    def _stored(self, id: str) -> StoredArticle | None:
        article = self.index.get(id)
        return self.wiki.read(Path(article.path)) if article else None

    def _log(self, tool: str, args: dict[str, Any], ids: list[str], text: str) -> str:
        entry = {
            "at": self.clock().isoformat(),
            "tool": tool,
            "args": args,
            "ids": ids,
            "chars": len(text),
            "ms": round((time.perf_counter() - self._started) * 1000) if self._started else None,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return text


def _decision_lines(d: dict[str, Any]) -> list[str]:
    lines = [
        f"주제: {d.get('topic')}",
        f"문제: {d.get('problem')}",
        "선택지:",
        *(f"- {o}" for o in d.get("options") or ()),
        f"선택: {d.get('chosen')}",
        "트레이드오프:",
        *(f"- {t}" for t in d.get("tradeoffs") or ()),
        f"결과: {d.get('outcome') or '-'}",
        f"신뢰도: {d.get('confidence')}",
        "근거 (원문 문장):",
        *(f"- {e}" for e in d.get("evidence") or ()),
    ]
    if d.get("unverified"):
        lines += ["원문에서 확인하지 못한 근거:", *(f"- {u}" for u in d["unverified"])]
    return lines


def _case(id: str, a: StoredArticle, company: str) -> str:
    d = a.decision or {}
    tradeoffs = "; ".join((d.get("tradeoffs") or [])[:3])
    return (
        f"- [{id}] {d.get('topic')} — {a.title}\n"
        f"  {company} · {_date(a)} · 신뢰도 {d.get('confidence')}\n"
        f"  문제: {d.get('problem')}\n"
        f"  선택지: {' / '.join(d.get('options') or [])}\n"
        f"  선택: {d.get('chosen')}\n"
        f"  트레이드오프: {tradeoffs}\n"
        f"  {a.url}"
    )


def _date(a: StoredArticle) -> str:
    return a.published.date().isoformat() if a.published else "날짜 모름"


def _fit(lines: list[str], tool: str) -> str:
    """예산을 넘으면 뒤쪽 항목을 덜어 낸다. 잘랐다는 사실을 남긴다."""
    budget = BUDGET[tool]
    out: list[str] = []
    used = 0
    for n, line in enumerate(lines):
        if used + len(line) + 1 > budget:
            out.append(
                f"(길이 제한으로 {len(lines) - n}개 항목을 뺐다. limit을 줄이거나 좁혀 묻는다)"
            )
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _grouped_topics(
    counts: list[tuple[str, int]], topics: topicmod.TopicMap
) -> tuple[list[tuple[str, int, list[str]]], list[tuple[str, int]]]:
    """원래 주제 사례 수를 넓은 주제로 모은다. (넓은 주제, 사례 수, 원래 주제들), 분류 전 주제."""
    grouped: dict[str, tuple[int, list[str]]] = {}
    loose = []
    for name, n in counts:
        group = topics.group_of(name)
        if group is None:
            loose.append((name, n))
            continue
        total, originals = grouped.get(group, (0, []))
        grouped[group] = (total + n, [*originals, name])
    ordered = sorted(grouped.items(), key=lambda kv: (-kv[1][0], kv[0]))
    return [(g, n, originals) for g, (n, originals) in ordered], loose
