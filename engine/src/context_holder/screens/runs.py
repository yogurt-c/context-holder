"""수집 기록(`state/runs/{날짜}.json`)을 읽어 상태 알림을 만든다 (plan 7장 상태별 화면).

- 며칠째 수집하지 못했다: 마지막 기록이 이틀 이상 지났다.
- 수집이 멈췄다: 마지막 기록이 에이전트 로그인 · 한도 문제로 멈췄다.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from context_holder.llm import AGENT_TOOLS, AGENTS
from context_holder.wiki import Wiki

RUNS_DIR = "state/runs"
STALE_DAYS = 2
STOP_TITLE = {  # {name}: Claude · Codex, {tool}: Claude Code · Codex CLI
    "auth": "{name} 로그인이 만료돼 수집이 멈췄어요.",
    "limit": "{name} 사용 한도에 걸려 수집이 멈췄어요.",
    "not_installed": "{tool}를 찾지 못해 수집이 멈췄어요.",
}
STOP_DEFAULT = "수집이 중간에 멈췄어요."


@dataclass(frozen=True)
class Alert:
    tone: str  # error | busy (components.css .banner)
    title: str
    body: str
    action: str | None = None  # "sync": 앱이 띄웠으면 지금 수집 버튼을 단다


def run_records(wiki: Wiki) -> list[tuple[date, dict[str, Any]]]:
    """날짜 이름의 기록만 날짜순으로. init 잠금이나 깨진 파일은 건너뛴다."""
    records = []
    for path in sorted((wiki.root / RUNS_DIR).glob("*.json")):
        day = _day(path)
        if day is None:
            continue
        try:
            records.append((day, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return records


def alerts(wiki: Wiki, pending: int, now: datetime, agent: str = "claude") -> list[Alert]:
    records = run_records(wiki)
    if not records:  # 아직 한 번도 수집하지 않은 위키: 사이드바가 알려 준다
        return []
    found = []
    last_day, last = records[-1]
    if last.get("status") == "stopped":
        kind = (last.get("result") or {}).get("stopped")
        waiting = f" 요약 대기 {pending}건은 다음 수집 때 다시 만들어요." if pending else ""
        found.append(
            Alert(
                "busy",
                STOP_TITLE.get(kind, STOP_DEFAULT).format(
                    name=AGENTS[agent], tool=AGENT_TOOLS[agent]
                ),
                f"{last_day.month}월 {last_day.day}일 수집에서 멈췄어요.{waiting}",
            )
        )
    gap = (now.astimezone().date() - last_day).days
    if gap >= STALE_DAYS:
        found.append(
            Alert(
                "error",
                f"마지막 수집이 {gap}일 전이에요.",
                f"{last_day.month}월 {last_day.day}일 뒤로 새 글을 모으지 않았어요. "
                "모든 기기가 꺼져 있었을 수 있어요.",
                action="sync",
            )
        )
    return found


def _day(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None
