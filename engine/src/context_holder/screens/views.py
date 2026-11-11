"""화면에 넣을 값을 만든다 (plan 7장). 템플릿은 여기서 만든 값만 그린다.

Today 순위 = 관련도 * 신선도 * 회사 가중치.
신선도는 게시일(없으면 수집일)에서 7일이 지날 때마다 절반이 된다.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from context_holder.classify import Bucket
from context_holder.index import Index, IndexedArticle
from context_holder.sources import SourceConfigError
from context_holder.wiki import StoredArticle, Wiki

HALF_LIFE_DAYS = 7
BRIEF_SIZE = 5  # 리드 1 + 목록 4. 나머지는 "더 보기"로 MORE_SIZE건씩 붙인다
MORE_SIZE = 10
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
    stopped: str = ""  # 멈춘 이유 (auth · limit · not_installed). 수집 카드가 고칠 길을 보인다


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
    call: str  # 목록에 보이는 짧은 말
    hits: int  # 가장 최근에 찾았을 때 나온 건수
    full: str = ""  # 잘리기 전 전체 말 (title)
    times: int = 1  # 같은 말을 찾은 횟수


@dataclass(frozen=True)
class Rail:
    topics: list[tuple[str, int]]
    topic_total: int
    inbox: list[IndexedArticle]
    inbox_total: int
    mcp: list[McpEntry]


@dataclass(frozen=True)
class Page:
    """커서 페이징 한 쪽. after는 다음 쪽을 부를 때 넘길 마지막 항목 id (없으면 끝)."""

    items: list[Any]
    after: str | None


@dataclass(frozen=True)
class TodayView:
    day: date
    is_today: bool
    prev_day: date  # 조회하지 않고 하루 전. 누르면 그날을 불러온다
    next_day: date | None  # 오늘이면 없다
    show_today: bool  # "오늘로": 그저께 이전을 볼 때만 (어제면 다음 날이 곧 오늘이다)
    lead: Lead | None
    briefs: list[Ranked]  # 2~5위
    more_after: str | None  # 6위부터 "더 보기"로 부를 커서 (5위 글 id)
    mark_decisions: bool  # 결정 사례가 아닌 글이 섞여 있어 결정 사례에 배지를 단다
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
        label = "오늘 아직 수집 전" if is_today else "이날은 수집하지 않았어요"
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
        reason = str(result.get("stopped") or "")
        return RunStatus("stopped", "수집이 중간에 멈췄어요", mine, holder, counts, where, reason)
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
        full, short = call_text(row["tool"], row.get("args") or {})
        entries.append(McpEntry(short, len(row["ids"]), full))
    return _merge(entries[::-1])[:MCP_SIZE]


def _merge(latest_first: list[McpEntry]) -> list[McpEntry]:
    """같은 말을 여러 번 찾았으면 가장 최근 줄 하나로 합치고 횟수를 센다."""
    merged: dict[str, McpEntry] = {}
    for e in latest_first:
        seen = merged.get(e.full)
        merged[e.full] = e if seen is None else replace(seen, times=seen.times + 1)
    return list(merged.values())


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
    day = min(day or today, today)
    ranked = _ranked_day(wiki, index, day)
    lead = _lead(wiki, ranked[0]) if ranked else None
    recent = [] if ranked else _recent(wiki, index, day)
    return TodayView(
        day=day,
        is_today=day == today,
        prev_day=day - timedelta(days=1),
        next_day=day + timedelta(days=1) if day < today else None,
        show_today=day < today - timedelta(days=1),
        lead=lead,
        briefs=ranked[1:BRIEF_SIZE],
        more_after=ranked[BRIEF_SIZE - 1].article.id if len(ranked) > BRIEF_SIZE else None,
        mark_decisions=any(not r.article.decision_topic for r in ranked[1:]),
        recent=recent,
        pending=sum(1 for a in index.select("WHERE status = ?", ("pending",))),
        status=run_status(wiki, day, device, day == today),
        rail=_rail(index, mcp_log, now),
    )


def today_more(wiki: Wiki, index: Index, day: date, after: str) -> tuple[Page, bool]:
    """Today "더 보기": after 글 다음부터 MORE_SIZE건과 결정 사례 배지를 달지.

    순위는 첫 화면과 같은 기준(그날 끝 시각)으로 다시 낸다.
    """
    ranked = _ranked_day(wiki, index, day)
    page = after_cursor(ranked, after, MORE_SIZE, lambda r: r.article.id)
    return page, any(not r.article.decision_topic for r in ranked[1:])


def after_cursor(items: list[Any], after: str, size: int, key: Callable[[Any], str]) -> Page:
    """정렬한 목록에서 after 다음부터 size개. after가 없어졌으면 (지워진 글) 빈 쪽을 준다."""
    ids = [key(item) for item in items]
    if after not in ids:
        return Page([], None)
    start = ids.index(after) + 1
    chunk = items[start : start + size]
    rest = start + size < len(items)
    return Page(chunk, key(chunk[-1]) if rest and chunk else None)


def sidebar(
    wiki: Wiki, index: Index, device: str, clock: Clock, scheduler: Any = None
) -> dict[str, Any]:
    """모든 화면의 사이드바: 오늘 수집 현황과 Inbox 건수. 앱이 띄웠으면 스케줄러 상태도."""
    today = clock().astimezone().date()
    inbox = index.select("WHERE bucket = ?", (Bucket.INBOX.value,))
    return {
        "status": run_status(wiki, today, device, True),
        "inbox_total": len(inbox),
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


def _ranked_day(wiki: Wiki, index: Index, day: date) -> list[Ranked]:
    weights, names = _source_weights(wiki)
    return rank(saved_between(index, day, day), weights, names, _end_of(day))


def _recent(wiki: Wiki, index: Index, day: date) -> list[tuple[date, Ranked]]:
    weights, names = _source_weights(wiki)
    first, last = day - timedelta(days=RECENT_DAYS), day - timedelta(days=1)
    pool = saved_between(index, first, last)
    ranked = rank(pool, weights, names, _end_of(day))[:RECENT_SIZE]
    return [(local_day(r.article.collected), r) for r in ranked]  # type: ignore[misc]


def saved_between(index: Index, first: date, last: date) -> list[IndexedArticle]:
    """first ~ last (로컬 날짜, 둘 다 포함)에 수집해 저장한 글.

    수집 시각은 UTC ISO 문자열이라 문자열 범위로 좁힌다. 시간대가 다르게 적힌 줄이 있어도
    빠지지 않게 하루씩 넓혀 읽고, 로컬 날짜로 다시 거른다.
    """
    start = _start_of(first - timedelta(days=1)).astimezone(UTC).isoformat()
    end = _end_of(last + timedelta(days=1)).astimezone(UTC).isoformat()
    rows = index.select(
        "WHERE bucket = ? AND collected >= ? AND collected < ?",
        (Bucket.SAVE.value, start, end),
    )
    return [a for a in rows if first <= local_day(a.collected) <= last]  # type: ignore[operator]


def _source_weights(wiki: Wiki) -> tuple[dict[str, float], dict[str, str]]:
    sources = configured_sources(wiki)
    return {s.id: s.weight for s in sources}, {s.id: s.name for s in sources}


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


def _start_of(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time()).astimezone()


def _end_of(day: date) -> datetime:
    return _start_of(day + timedelta(days=1))


def _clock_time(value: str | None) -> str:
    if not value:
        return "--:--"
    return datetime.fromisoformat(value).astimezone().strftime("%H:%M")


def call_text(tool: str, args: dict[str, Any]) -> tuple[str, str]:
    """Claude가 찾은 말 (전체, 짧게).

    검색은 첫 질의와 나머지 개수, 결정 사례 찾기는 주제(없으면 문제).
    """
    if tool == "search_wiki":
        queries = [str(q) for q in args.get("queries") or [] if q]
        rest = f" 외 {len(queries) - 1}개" if len(queries) > 1 else ""
        return " · ".join(queries), _clip(queries[0] if queries else "") + rest
    subject = str(args.get("topic") or args.get("problem") or "")
    return "결정 사례 · " + subject, "결정 사례 · " + _clip(subject)


def _clip(text: str, size: int = 20) -> str:
    """길면 단어 경계에서 자른다. 단어가 너무 길면 글자로 자른다."""
    if len(text) <= size:
        return text
    cut = text[: size - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > size // 2 else cut).rstrip(" ·") + "…"
