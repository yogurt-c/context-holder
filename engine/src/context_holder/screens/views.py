"""화면에 넣을 값을 만든다 (plan 7장). 템플릿은 여기서 만든 값만 그린다.

Today 순위 = 관련도 * 신선도 * 회사 가중치.
신선도는 게시일(없으면 수집일)에서 7일이 지날 때마다 절반이 된다.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from context_holder.classify import Bucket
from context_holder.index import Index, IndexedArticle
from context_holder.sources import SourceConfigError
from context_holder.wiki import StoredArticle, Wiki

HALF_LIFE_DAYS = 7
BRIEF_SIZE = 5  # 리드 1 + 목록 4. 나머지는 접어 둔다
RECENT_DAYS = 7
RECENT_SIZE = 3
TOPICS_SIZE = 4
INBOX_PREVIEW = 2
MCP_DAYS = 7
MCP_SIZE = 5
RUNS_DIR = "state/runs"

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class RunStatus:
    state: str  # none | running | done | stopped
    label: str  # 사이드바 카드 머리줄
    mine: bool  # 이 기기가 맡았나
    device: str | None = None
    counts: dict[str, int] = field(default_factory=dict)  # 신규 · 저장 · 보류 · 제외
    foot: str = ""


@dataclass(frozen=True)
class Ranked:
    article: IndexedArticle
    score: float
    company: str


@dataclass(frozen=True)
class Lead:
    ranked: Ranked
    stored: StoredArticle
    decision_markdown: str  # "Claude에 문맥으로 보내기"로 복사할 내용


@dataclass(frozen=True)
class McpEntry:
    call: str
    hits: int


@dataclass(frozen=True)
class Rail:
    topics: list[tuple[str, int]]
    topic_total: int
    inbox: list[IndexedArticle]
    inbox_total: int
    mcp: list[McpEntry]


@dataclass(frozen=True)
class TodayView:
    day: date
    is_today: bool
    prev_day: date | None
    next_day: date | None
    lead: Lead | None
    briefs: list[Ranked]  # 2~5위
    more: list[Ranked]  # 6위부터
    recent: list[tuple[date, Ranked]]  # 그날 저장 글이 없을 때 최근 7일 상위
    pending: int
    status: RunStatus
    rail: Rail


WEEKDAYS = "월화수목금토일"


def short_day(day: date) -> str:
    return f"{day.month}월 {day.day}일"


def long_day(day: date) -> str:
    return f"{short_day(day)} {WEEKDAYS[day.weekday()]}요일"


def local_day(value: str | datetime | None) -> date | None:
    if value is None:
        return None
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone().date()


def freshness(article: IndexedArticle, now: datetime) -> float:
    base = article.published or article.collected
    if base is None:
        return 1.0
    moment = datetime.fromisoformat(base)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    age = max((now - moment).total_seconds() / 86400, 0.0)
    return 0.5 ** (age / HALF_LIFE_DAYS)


def rank(
    articles: Iterable[IndexedArticle],
    weights: dict[str, float],
    names: dict[str, str],
    now: datetime,
) -> list[Ranked]:
    ranked = [
        Ranked(
            a,
            (a.relevance or 0.0) * freshness(a, now) * weights.get(a.source_id, 1.0),
            names.get(a.source_id, a.source_id),
        )
        for a in articles
    ]
    return sorted(ranked, key=lambda r: (-r.score, r.article.title))


def run_status(wiki: Wiki, day: date, device: str, is_today: bool) -> RunStatus:
    path = wiki.root / RUNS_DIR / f"{day.isoformat()}.json"
    if not path.exists():
        label = "오늘 아직 수집 전" if is_today else "이날은 수집하지 않았다"
        return RunStatus("none", label, mine=False)
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    holder = record.get("device")
    mine = holder == device
    where = "이 기기에서 수집" if mine else f"{holder}에서 수집"
    result = record.get("result") or {}
    counts = {}
    if result:
        counts = {
            "신규": sum(int(result.get(k, 0)) for k in ("saved", "inboxed", "dropped")),
            "저장": int(result.get("saved", 0)),
            "보류": int(result.get("inboxed", 0)),
            "제외": int(result.get("dropped", 0)),
        }
    status = record.get("status")
    if status == "running":
        started = _clock_time(record.get("started_at"))
        label = "이 기기가 수집 중" if mine else f"{holder}가 수집 중"
        return RunStatus("running", label, mine, holder, counts, f"{started}에 시작")
    if status == "stopped":
        return RunStatus("stopped", "수집이 중간에 멈췄다", mine, holder, counts, where)
    finished = _clock_time(record.get("finished_at"))
    head = f"오늘 {finished} 수집 완료" if is_today else f"{finished} 수집 완료"
    return RunStatus("done", head, mine, holder, counts, where)


def mcp_entries(log_path: Path, now: datetime) -> list[McpEntry]:
    """MCP 질의 로그의 최근 기록. 깨진 줄은 건너뛴다."""
    if not log_path.exists():
        return []
    since = now - timedelta(days=MCP_DAYS)
    entries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            at = datetime.fromisoformat(row["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if row.get("tool") not in ("search_wiki", "find_decision_cases") or at < since:
            continue
        entries.append(McpEntry(call_text(row["tool"], row.get("args") or {}), len(row["ids"])))
    return entries[-MCP_SIZE:][::-1]


def decision_markdown(stored: StoredArticle, company: str) -> str:
    d = stored.decision or {}
    lines = [
        f"## {d.get('topic')} ({company})",
        f"- 글: {stored.title} {stored.url}",
        f"- 문제: {d.get('problem')}",
        f"- 선택지: {', '.join(d.get('options') or [])}",
        f"- 선택: {d.get('chosen')}",
        *(f"- 트레이드오프: {t}" for t in d.get("tradeoffs") or []),
        f"- 결과: {d.get('outcome') or '-'}",
        f"- 신뢰도: {d.get('confidence')}",
    ]
    return "\n".join(lines)


def build_today(
    wiki: Wiki, index: Index, device: str, mcp_log: Path, day: date | None, clock: Clock
) -> TodayView:
    now = clock()
    today = now.astimezone().date()
    day = day or today
    sources = configured_sources(wiki)
    weights = {s.id: s.weight for s in sources}
    names = {s.id: s.name for s in sources}
    saved = [a for a in index.select("WHERE bucket = ?", (Bucket.SAVE.value,)) if a.collected]
    by_day: dict[date, list[IndexedArticle]] = {}
    for a in saved:
        by_day.setdefault(local_day(a.collected), []).append(a)  # type: ignore[arg-type]
    days = sorted(by_day)
    moment = _end_of(day)
    ranked = rank(by_day.get(day, []), weights, names, moment)
    lead = _lead(wiki, ranked[0]) if ranked else None
    recent = [] if ranked else _recent(by_day, day, weights, names, moment)
    return TodayView(
        day=day,
        is_today=day == today,
        prev_day=max((d for d in days if d < day), default=None),
        next_day=min((d for d in [*days, today] if day < d <= today), default=None),
        lead=lead,
        briefs=ranked[1:BRIEF_SIZE],
        more=ranked[BRIEF_SIZE:],
        recent=recent,
        pending=sum(1 for a in index.select("WHERE status = ?", ("pending",))),
        status=run_status(wiki, day, device, day == today),
        rail=_rail(index, mcp_log, now),
    )


def sidebar(
    wiki: Wiki, index: Index, device: str, clock: Clock, scheduler: Any = None
) -> dict[str, Any]:
    """모든 화면의 사이드바: 오늘 수집 현황과 Inbox 건수. 앱이 띄웠으면 스케줄러 상태도."""
    today = clock().astimezone().date()
    inbox = index.select("WHERE bucket = ?", (Bucket.INBOX.value,))
    return {
        "status": run_status(wiki, today, device, True),
        "inbox_total": len(inbox),
        "decision_total": sum(n for _, n in index.topics()),
        "sync": sync_state(scheduler) if scheduler is not None else None,
    }


def sync_state(scheduler: Any) -> dict[str, Any]:
    """사이드바 카드의 수집 진행 · 지금 수집 버튼 (scheduler.Scheduler)."""
    progress = scheduler.progress
    paused = scheduler.paused_until()
    if paused == "forever":
        paused_label = "다시 켤 때까지"
    elif paused:
        paused_label = f"{paused.astimezone():%H:%M}까지"
    else:
        paused_label = None
    return {
        "running": scheduler.running,
        "progress": progress.label if progress else None,
        "ratio": round(100 * progress.done / progress.total)
        if progress and progress.total and progress.stage != "classify"
        else None,
        "started": scheduler.started_at.astimezone().strftime("%H:%M")
        if scheduler.started_at
        else "",
        "can_run": scheduler.can_run_now(),
        "paused": paused_label,
    }


# ---------- 내부 ----------


def _lead(wiki: Wiki, top: Ranked) -> Lead | None:
    stored = wiki.read(Path(top.article.path))
    if stored is None:  # 다른 기기의 pull로 사라졌다. 인덱스가 다시 맞춰질 때까지 리드를 비운다
        return None
    markdown = decision_markdown(stored, top.company) if stored.decision else ""
    return Lead(top, stored, markdown)


def _recent(
    by_day: dict[date, list[IndexedArticle]],
    day: date,
    weights: dict[str, float],
    names: dict[str, str],
    moment: datetime,
) -> list[tuple[date, Ranked]]:
    window = [d for d in by_day if day - timedelta(days=RECENT_DAYS) <= d < day]
    pool = [(d, a) for d in window for a in by_day[d]]
    day_of = {a.id: d for d, a in pool}
    ranked = rank((a for _, a in pool), weights, names, moment)[:RECENT_SIZE]
    return [(day_of[r.article.id], r) for r in ranked]


def _rail(index: Index, mcp_log: Path, now: datetime) -> Rail:
    topics = index.topics()
    inbox = sorted(
        index.select("WHERE bucket = ?", (Bucket.INBOX.value,)),
        key=lambda a: (a.collected or "", a.relevance or 0.0),
        reverse=True,
    )
    return Rail(
        topics=[t for t in topics if t[1] > 1][:TOPICS_SIZE],  # 1건짜리는 아직 묶이지 않은 주제
        topic_total=sum(n for _, n in topics),
        inbox=inbox[:INBOX_PREVIEW],
        inbox_total=len(inbox),
        mcp=mcp_entries(mcp_log, now),
    )


def configured_sources(wiki: Wiki) -> tuple:
    try:
        return wiki.sources()
    except (OSError, SourceConfigError):  # 설정이 아직 없는 위키
        return ()


def _end_of(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), datetime.min.time()).astimezone()


def _clock_time(value: str | None) -> str:
    if not value:
        return "--:--"
    return datetime.fromisoformat(value).astimezone().strftime("%H:%M")


def call_text(tool: str, args: dict[str, Any]) -> str:
    if tool == "search_wiki":
        queries = [_clip(q) for q in (args.get("queries") or [])[:2]]
        return f"search_wiki({json.dumps(queries, ensure_ascii=False)})"
    return f'find_decision_cases("{_clip(args.get("topic") or args.get("problem") or "")}")'


def _clip(text: str, size: int = 16) -> str:
    return text if len(text) <= size else text[: size - 1] + "…"
