"""Sources 화면 (plan 7장). 소스를 추가하고(addsource), 수집 대상에서 뺀다.

수집 기록(state/runs)의 소스별 결과(result.by_source)를 30일치 읽어 계산한다.
- 통과율 = 30일 동안 저장한 글 / 새로 모은 글
- 추이 = 30일을 6일씩 다섯 칸으로 나눈 새 글 수
추가 후보는 Claude 검색이 0건이었던 질의와 suggest_source로 남긴 글이다.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from context_holder.feedback import Kind, load_feedback
from context_holder.screens.runs import run_records
from context_holder.screens.views import call_text, configured_sources, short_day
from context_holder.sources import Mode, Source
from context_holder.wiki import Wiki

WINDOW_DAYS = 30
BINS = 5
BIN_DAYS = 6
SPARK_MAX = 18  # px
SPARK_MIN = 2
CANDIDATES = 5
METHOD = {Mode.FEED: "feed 본문", Mode.FETCH: "원문 요청"}
SEARCH_TOOLS = ("search_wiki", "find_decision_cases")


@dataclass(frozen=True)
class SourceRow:
    source: Source
    method: str
    last_check: str | None  # "오늘 09:13", "11월 7일" (기록이 없으면 None)
    error: str | None  # 마지막 확인에서 실패한 이유
    new: int
    saved: int
    spark: list[int]  # 막대 높이(px), 오래된 칸부터

    @property
    def pass_rate(self) -> int | None:
        return round(100 * self.saved / self.new) if self.new else None


@dataclass(frozen=True)
class MissedQuery:
    call: str
    times: int


@dataclass(frozen=True)
class Suggestion:
    url: str
    reason: str
    day: date


@dataclass(frozen=True)
class SourcesView:
    domestic: list[SourceRow]
    overseas: list[SourceRow]
    failures: list[tuple[str, str]]  # (회사, 이유): 마지막 수집에서 실패한 소스
    has_records: bool  # 소스별 기록이 하나라도 있나
    missed: list[MissedQuery]
    suggestions: list[Suggestion]


def build_sources(wiki: Wiki, mcp_log: Path, now: datetime) -> SourcesView:
    today = now.astimezone().date()
    since = today - timedelta(days=WINDOW_DAYS - 1)
    records = [(d, r) for d, r in run_records(wiki) if (r.get("result") or {}).get("by_source")]
    window = [(d, r) for d, r in records if d >= since]
    sources = configured_sources(wiki)
    collected = [s for s in sources if not s.excluded]  # 뺀 소스는 목록에 보이지 않는다
    rows = [_row(s, records, window, today) for s in collected]
    names = {s.id: s.name for s in collected}
    failures = []
    if records:
        _, last = records[-1]
        failures = [
            (names.get(sid, sid), entry["error"])
            for sid, entry in last["result"]["by_source"].items()
            if entry.get("error") and sid in names
        ]
    return SourcesView(
        [r for r in rows if r.source.region == "domestic"],
        [r for r in rows if r.source.region == "overseas"],
        failures,
        bool(records),
        missed_queries(mcp_log, now),
        suggestions(wiki, now),
    )


def missed_queries(log_path: Path, now: datetime) -> list[MissedQuery]:
    """최근 30일 동안 결과가 0건이었던 Claude 검색. 같은 질의는 묶어 센다."""
    if not log_path.exists():
        return []
    since = now - timedelta(days=WINDOW_DAYS)
    counts: dict[str, int] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            at = datetime.fromisoformat(row["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if row.get("tool") not in SEARCH_TOOLS or row.get("ids") or at < since:
            continue
        call = call_text(row["tool"], row.get("args") or {})
        counts[call] = counts.pop(call, 0) + 1  # 다시 넣어 최근 질의가 뒤로 가게 한다
    ordered = list(counts.items())[::-1]
    return [MissedQuery(call, n) for call, n in ordered[:CANDIDATES]]


def suggestions(wiki: Wiki, now: datetime) -> list[Suggestion]:
    since = (now - timedelta(days=WINDOW_DAYS)).isoformat()
    found: dict[str, Suggestion] = {}
    for f in load_feedback(wiki):
        if f.kind == Kind.SUGGEST_SOURCE.value and f.at >= since:
            day = datetime.fromisoformat(f.at).astimezone().date()
            found.pop(f.url, None)
            found[f.url] = Suggestion(f.url, f.note or "", day)
    return list(found.values())[::-1][:CANDIDATES]


# ---------- 내부 ----------


def _row(
    source: Source,
    records: list[tuple[date, dict[str, Any]]],
    window: list[tuple[date, dict[str, Any]]],
    today: date,
) -> SourceRow:
    mine = [(d, r, r["result"]["by_source"][source.id]) for d, r in window if source.id in _by(r)]
    last = next(
        ((d, r, _by(r)[source.id]) for d, r in reversed(records) if source.id in _by(r)), None
    )
    bins = [0] * BINS
    for d, _, entry in mine:
        n = (today - d).days // BIN_DAYS
        if n < BINS:
            bins[BINS - 1 - n] += int(entry.get("new", 0))
    top = max(bins)
    spark = [
        SPARK_MIN + round((SPARK_MAX - SPARK_MIN) * b / top) if top else SPARK_MIN for b in bins
    ]
    return SourceRow(
        source,
        METHOD.get(source.mode, str(source.mode)),
        _when(last[0], last[1], today) if last else None,
        last[2].get("error") if last else None,
        sum(int(e.get("new", 0)) for _, _, e in mine),
        sum(int(e.get("saved", 0)) for _, _, e in mine),
        spark,
    )


def _by(record: dict[str, Any]) -> dict[str, Any]:
    return (record.get("result") or {}).get("by_source") or {}


def _when(day: date, record: dict[str, Any], today: date) -> str:
    at = record.get("finished_at") or record.get("heartbeat_at")
    clock = datetime.fromisoformat(at).astimezone().strftime("%H:%M") if at else ""
    head = "오늘" if day == today else short_day(day)
    return f"{head} {clock}".strip()
