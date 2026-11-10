"""메뉴바 메뉴와 알림을 정한다 (docs/ui/menubar.html). Tauri 앱은 받은 대로 그리기만 한다.

엔진은 stdout에 이벤트를 JSON 한 줄씩 쓴다.
- {"event": "status", "icon": ..., "items": [...]}: 아이콘 상태와 메뉴 항목
- {"event": "notify", "title": ..., "body": ..., "open": "/today"}: macOS 알림

메뉴 항목: {"label", "enabled"} 에 동작 하나를 붙인다.
- "open": 창을 열고 그 화면으로 간다  - "cmd": 엔진 stdin으로 보낼 명령
- "children": 하위 메뉴  - "quit": 앱 종료  - {"separator": true}
알림은 하루 최대 2번, 종류마다 하루 한 번. 조용히 끝난 일은 알리지 않는다.
"""

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from context_holder.appsettings import Settings
from context_holder.classify import Bucket
from context_holder.index import Index
from context_holder.llm import AGENTS
from context_holder.scheduler import FOREVER, PAUSES, Scheduler
from context_holder.screens.runs import run_records
from context_holder.screens.views import configured_sources, local_day, rank, short_day
from context_holder.updates import Updates
from context_holder.wiki import Wiki

MAX_NOTICES = 2
STALE_NOTICE_DAYS = 3
WEEKDAYS = "월화수목금토일"
CLAUDE_STOPS = ("auth", "limit", "not_installed")


@dataclass(frozen=True)
class Notice:
    kind: str  # brief | claude | sources | stale
    title: str
    body: str
    open: str

    def event(self) -> dict[str, Any]:
        return {"event": "notify", "title": self.title, "body": self.body, "open": self.open}


def status_event(
    scheduler: Scheduler, index: Index, updates: Updates | None = None
) -> dict[str, Any]:
    now = scheduler.clock()
    record = scheduler.today_record()
    paused = scheduler.paused_until()
    head, sub, icon = _headline(scheduler, record, paused, now)
    inbox = len(index.select("WHERE bucket = ?", (Bucket.INBOX.value,)))
    pending = len(index.select("WHERE status = ?", ("pending",)))
    items: list[dict[str, Any]] = [{"label": head, "enabled": False}]
    if sub:
        items.append({"label": sub, "enabled": False})
    items.append({"separator": True})
    items += [
        {"label": "Today 열기", "enabled": True, "open": "/today"},
        {"label": "Search 열기", "enabled": True, "open": "/search"},
        {"label": f"Inbox 열기 ({inbox})" if inbox else "Inbox 열기", "enabled": True, "open": "/inbox"},
        {"separator": True},
        _sync_item(scheduler, record),
        _pause_item(paused),
    ]
    if pending:
        items.append({"label": f"요약 대기 {pending}건", "enabled": False})
    update = updates.menu_item(scheduler.running) if updates else None
    if update:
        items += [{"separator": True}, update]
    items += [
        {"separator": True},
        {"label": "설정", "enabled": True, "open": "/settings"},
        {"label": "Context Holder 종료", "enabled": True, "quit": True},
    ]
    return {"event": "status", "icon": icon, "items": items}


def notices(scheduler: Scheduler, index: Index) -> list[Notice]:
    """지금 알릴 만한 일. 하루 한 번 거르기는 NoticeLog가 한다."""
    now = scheduler.clock()
    today = now.astimezone().date()
    found = []
    record = scheduler.today_record()
    if record and record.get("device") == scheduler.device:  # 다른 기기의 수집은 알리지 않는다
        result = record.get("result") or {}
        saved = int(result.get("saved", 0))
        if record.get("status") == "done" and saved:
            found.append(_brief_notice(scheduler.wiki, index, now, saved))
        if record.get("status") == "stopped" and result.get("stopped") in CLAUDE_STOPS:
            found.append(
                Notice(
                    "claude",
                    f"{AGENTS[scheduler.settings().agent]} 연결이 끊겼어요",
                    "새 글은 가져만 두고 분류와 요약은 미뤘어요. 다음 수집 때 이어서 해요.",
                    "/today",
                )
            )
        sources, failed = int(result.get("sources", 0)), int(result.get("failed_sources", 0))
        if sources and failed * 2 >= sources:
            found.append(
                Notice(
                    "sources",
                    f"소스 {failed}곳에서 수집하지 못했어요",
                    "네트워크나 차단 문제일 수 있어요. 눌러서 확인하세요.",
                    "/sources",
                )
            )
    records = run_records(scheduler.wiki)
    if records and not scheduler.running:
        gap = (today - records[-1][0]).days
        if gap >= STALE_NOTICE_DAYS:
            found.append(
                Notice(
                    "stale",
                    f"{gap}일째 수집하지 못했어요",
                    "모든 기기가 꺼져 있었거나 일시정지 상태였어요.",
                    "/today",
                )
            )
    return found


class NoticeLog:
    """보낸 알림을 기기 로컬 파일에 남긴다. 하루 최대 MAX_NOTICES번, 종류마다 한 번."""

    def __init__(self, path: Path):
        self.path = path

    def take(self, candidates: list[Notice], day: date) -> list[Notice]:
        sent = self._sent(day)
        chosen = []
        for notice in candidates:
            if len(sent) >= MAX_NOTICES:
                break
            if notice.kind not in sent:
                sent.append(notice.kind)
                chosen.append(notice)
        if chosen:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"day": day.isoformat(), "sent": sent}), "utf-8")
        return chosen

    def _sent(self, day: date) -> list[str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return list(data.get("sent") or []) if data.get("day") == day.isoformat() else []


# ---------- 내부 ----------


def _headline(
    scheduler: Scheduler, record: dict[str, Any] | None, paused, now: datetime
) -> tuple[str, str, str]:
    """(상태 줄, 둘째 줄, 아이콘)"""
    if scheduler.running:
        started = scheduler.started_at.astimezone().strftime("%H:%M")  # type: ignore[union-attr]
        step = f" · {scheduler.progress.label}" if scheduler.progress else ""
        return f"수집 중{step}", f"이 기기에서 {started}에 시작", "busy"
    if record and record.get("status") == "running" and scheduler.other_device_running():
        return f"{record.get('device')}가 수집 중", "끝나면 받아와요", "busy"
    if paused:
        until = "다시 켤 때까지" if paused == FOREVER else _until(paused, now)
        return "수집 일시정지", until, "paused"
    if record and record.get("status") == "done":
        result = record.get("result") or {}
        finished = _clock(record.get("finished_at"))
        new = sum(int(result.get(k, 0)) for k in ("saved", "inboxed", "dropped"))
        where = (
            "이 기기에서 수집"
            if record.get("device") == scheduler.device
            else (f"{record.get('device')}에서 수집")
        )
        failed = int(result.get("failed_sources", 0))
        icon = "error" if failed and failed * 2 >= int(result.get("sources", 0) or 1) else "normal"
        head = f"오늘 {finished} 수집 완료, 신규 {new}건"
        if failed:
            head += f" · 소스 {failed}곳 실패"
        return head, f"{where} · 다음 수집 {_next_day(now)}", icon
    if record and record.get("status") == "stopped":
        agent = AGENTS[scheduler.settings().agent]
        return "수집이 멈췄어요", f"{agent} 연결 문제예요. 다음 수집 때 이어서 해요", "error"
    if scheduler.last and scheduler.last.ok is False:
        at = scheduler.last.at.astimezone().strftime("%H:%M")
        return "수집하지 못했어요", f"{at}에 실패 · 30분 뒤 다시 해요", "error"
    return "오늘 아직 수집 전", "평일 09:30, Mac을 켜거나 깨울 때 수집해요", "normal"


def _sync_item(scheduler: Scheduler, record: dict[str, Any] | None) -> dict[str, Any]:
    if scheduler.running:
        return {"label": "수집 중…", "enabled": False}
    if record and record.get("status") == "done":
        return {"label": "오늘 수집 완료", "enabled": False}
    return {"label": "지금 수집", "enabled": scheduler.can_run_now(), "cmd": "sync_now"}


def _pause_item(paused) -> dict[str, Any]:
    if paused:
        return {"label": "수집 다시 켜기", "enabled": True, "cmd": "resume"}
    children = [
        {"label": label, "enabled": True, "cmd": f"pause {key}"} for key, label in PAUSES.items()
    ]
    return {"label": "수집 일시정지", "enabled": True, "children": children}


def _today_ranked(wiki: Wiki, index: Index, now: datetime):
    today = now.astimezone().date()
    saved = [
        a
        for a in index.select("WHERE bucket = ?", (Bucket.SAVE.value,))
        if a.collected and local_day(a.collected) == today
    ]
    sources = configured_sources(wiki)
    return rank(saved, {s.id: s.weight for s in sources}, {s.id: s.name for s in sources}, now)


def _brief_notice(wiki: Wiki, index: Index, now: datetime, saved: int) -> Notice:
    ranked = _today_ranked(wiki, index, now)
    if not ranked:
        return Notice(
            "brief", f"오늘의 브리프, 신규 {saved}건", "눌러서 브리프를 보세요.", "/today"
        )
    body = ranked[0].article.title + (f" 외 {len(ranked) - 1}건" if len(ranked) > 1 else "")
    decisions = sum(1 for r in ranked if r.article.decision_topic)
    if decisions:
        body += f". 결정 사례 {decisions}건"
    return Notice("brief", f"오늘의 브리프, 신규 {saved}건", body, "/today")


def _until(until: datetime, now: datetime) -> str:
    local = until.astimezone()
    if local.date() == now.astimezone().date():
        return f"{local:%H:%M}까지"
    return f"{short_day(local.date())} {local:%H:%M}까지"


def _next_day(now: datetime) -> str:
    local = now.astimezone().date()
    days = 3 if local.weekday() == 4 else 2 if local.weekday() == 5 else 1
    nxt = date.fromordinal(local.toordinal() + days)
    head = "내일" if days == 1 else f"{WEEKDAYS[nxt.weekday()]}요일"
    return f"{head} 09:30"


def _clock(value: str | None) -> str:
    if not value:
        return "--:--"
    return datetime.fromisoformat(value).astimezone().strftime("%H:%M")


def _clip(text: str, size: int = 40) -> str:
    return text if len(text) <= size else text[: size - 1] + "…"


class AppBridge:
    """엔진 stdout으로 앱에 이벤트를 보내고, stdin으로 온 메뉴 명령을 스케줄러에 넘긴다."""

    def __init__(
        self,
        out,
        index: Index,
        log: NoticeLog,
        settings: Callable[[], Settings] = Settings,
    ):
        self.out = out
        self.index = index
        self.log = log
        self.settings = settings
        self.scheduler: Scheduler | None = None
        self.updates: Updates | None = None
        self._last_status: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            try:
                self.out.write(json.dumps(event, ensure_ascii=False) + "\n")
                self.out.flush()
            except (BrokenPipeError, ValueError):  # 앱이 먼저 끝났다. stdin EOF로 곧 끝난다
                pass

    def refresh(self) -> None:
        if self.scheduler is None:
            return
        event = status_event(self.scheduler, self.index, self.updates)
        if event != self._last_status:
            if self._last_status is None:  # 첫 메뉴와 함께 자동 실행 설정을 알린다
                self.apply_settings(self.settings())
            self._last_status = event
            self.emit(event)

    def apply_settings(self, settings: Settings) -> None:
        """설정이 바뀌었다. 앱이 맡은 것(자동 실행)을 알리고 메뉴를 다시 그린다.

        자동 실행은 첫 실행 설정을 마친 뒤에만 알린다.
        사용자가 고르기 전에 로그인 항목을 넣지 않는다.
        """
        if settings.setup_done:
            self.emit({"event": "autostart", "enabled": settings.autostart})
        if self._last_status is not None:
            self.refresh()

    def check_notices(self) -> None:
        if self.scheduler is None:
            return
        today = self.scheduler.clock().astimezone().date()
        found = notices(self.scheduler, self.index)
        if not self.settings().notify:  # 수집 완료 알림을 껐다. 문제 알림은 그대로 보낸다
            found = [n for n in found if n.kind != "brief"]
        for notice in self.log.take(found, today):
            self.emit(notice.event())

    def tick(self) -> None:
        """1분마다: 알림을 확인하고, 때가 되면 새 버전을 확인한다."""
        self.check_notices()
        if self.updates is not None:
            self.updates.tick()

    def command(self, line: str) -> None:
        """앱이 보낸 줄.

        메뉴 명령: sync_now · pause 1h|today|forever · resume · update_install · update_restart.
        업데이트 상태: update {...}. 모르는 명령은 무시한다.
        """
        if self.scheduler is None:
            return
        if self.updates is not None and self._update_command(line.strip()):
            return
        words = line.split()
        if words == ["sync_now"]:
            self.scheduler.run_now()
        elif len(words) == 2 and words[0] == "pause" and words[1] in PAUSES:
            self.scheduler.pause(words[1])
        elif words == ["resume"]:
            self.scheduler.resume()

    def _update_command(self, line: str) -> bool:
        updates = self.updates
        assert updates is not None and self.scheduler is not None
        if line.startswith("update "):
            updates.feed(line.removeprefix("update "))
        elif line == "update_install":
            updates.install()
        elif line == "update_restart":
            updates.restart(busy=self.scheduler.running)
        else:
            return False
        return True
