"""앱이 띄운 엔진(`ctx serve --app`)의 매일 수집 스케줄러 (plan 3장).

계기: 앱 시작, 잠자기 해제, 평일 09:30 이후. 계기가 오면 오늘 수집이 안 됐을 때만 `ctx sync`를
자식 프로세스로 띄운다. 잠금 · pull · push는 `ctx sync`가 맡는다.

- 오늘 기록이 done이거나 다른 기기가 수집 중이면 띄우지 않는다.
- 실패하거나 멈췄으면 RETRY_AFTER가 지난 뒤 다음 계기에 다시 한다.
- 잠자기 해제는 따로 알림을 받지 않고, 벽시계가 기대보다 크게 건너뛴 것으로 안다.
- 일시정지(1시간 · 오늘 하루 · 다시 켤 때까지)는 기기 로컬 상태 파일에 남긴다.
"""

import json
import logging
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol

from context_holder.runlock import RUNS_DIR, STALE_AFTER
from context_holder.wiki import Wiki

log = logging.getLogger(__name__)

DAILY_AT = time(9, 30)
RETRY_AFTER = timedelta(minutes=30)
WAKE_GAP = timedelta(minutes=3)  # 1분마다 보는데 이만큼 건너뛰면 잠들었다 깬 것이다
TICK_SECONDS = 60
FOREVER = "forever"
PAUSES = {"1h": "1시간", "today": "오늘 하루", FOREVER: "다시 켤 때까지"}
STAGES = {"retry": "요약 대기 다시 만들기", "collect": "소스", "classify": "분류", "enrich": "요약"}

Clock = Callable[[], datetime]


class Child(Protocol):
    stdout: Iterable[str]

    def wait(self) -> int: ...


@dataclass(frozen=True)
class Progress:
    stage: str
    done: int
    total: int

    @property
    def label(self) -> str:
        name = STAGES.get(self.stage, self.stage)
        if self.stage == "classify":
            return f"{name} 중 ({self.total}건)"
        return f"{name} {self.done}/{self.total}"


@dataclass(frozen=True)
class Attempt:
    at: datetime
    ok: bool | None = None  # None: 진행 중


class Scheduler:
    def __init__(
        self,
        wiki: Wiki,
        state_path: Path,
        device: str,
        clock: Clock,
        spawn: Callable[[], Child],
        on_change: Callable[[], None] = lambda: None,
        on_finish: Callable[[], None] = lambda: None,
        background: Callable[[Callable[[], None]], None] | None = None,
    ):
        self.wiki = wiki
        self.state_path = state_path
        self.device = device
        self.clock = clock
        self.spawn = spawn
        self.on_change = on_change  # 메뉴 · 화면을 다시 그린다
        self.on_finish = on_finish  # 알림을 판단한다
        self.background = background or _thread
        self.progress: Progress | None = None
        self.started_at: datetime | None = None
        self.last: Attempt | None = None
        self._last_tick: datetime | None = None
        self._lock = threading.Lock()

    # ---------- 상태 ----------

    @property
    def running(self) -> bool:
        return self.started_at is not None

    def today_record(self) -> dict[str, Any] | None:
        day = self.clock().astimezone().date()
        path = self.wiki.root / RUNS_DIR / f"{day.isoformat()}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def done_today(self) -> bool:
        record = self.today_record()
        return bool(record and record.get("status") == "done")

    def other_device_running(self) -> bool:
        record = self.today_record()
        if not record or record.get("status") != "running" or record.get("device") == self.device:
            return False
        try:
            beat = datetime.fromisoformat(record["heartbeat_at"])
        except (KeyError, TypeError, ValueError):
            return False
        return self.clock() - beat < STALE_AFTER

    def paused_until(self) -> datetime | str | None:
        """일시정지 끝 시각. 다시 켤 때까지면 FOREVER, 아니면 None."""
        value = self._state().get("pause_until")
        if value == FOREVER:
            return FOREVER
        if not value:
            return None
        until = datetime.fromisoformat(value)
        return until if until > self.clock() else None

    def can_run_now(self) -> bool:
        """ "지금 수집"을 누를 수 있나. 일시정지와 재시도 간격은 무시한다."""
        return not (self.running or self.done_today() or self.other_device_running())

    # ---------- 계기 ----------

    def tick(self) -> None:
        """1분마다 부른다. 잠자기 해제나 평일 09:30 이후면 수집을 시도한다."""
        now = self.clock()
        woke = self._last_tick is not None and now - self._last_tick > WAKE_GAP
        self._last_tick = now
        local = now.astimezone()
        daily = local.weekday() < 5 and local.time() >= DAILY_AT
        if (woke or daily) and self.trigger("wake" if woke else "daily"):
            return
        self.on_change()  # 띄우지 않았어도 시각이 들어간 메뉴 문구를 새로 그린다

    def trigger(self, reason: str) -> bool:
        """계기가 왔다. 오늘 수집이 필요하면 띄운다."""
        if self.paused_until() or not self.can_run_now():
            return False
        if self.last and self.last.ok is not True and self.clock() - self.last.at < RETRY_AFTER:
            return False
        log.info("수집 시작 (%s)", reason)
        return self.start()

    def run_now(self) -> bool:
        return self.can_run_now() and self.start()

    # ---------- 일시정지 ----------

    def pause(self, span: str) -> None:
        now = self.clock()
        if span == "1h":
            until: str = (now + timedelta(hours=1)).isoformat()
        elif span == "today":
            until = _end_of(now.astimezone().date()).isoformat()
        elif span == FOREVER:
            until = FOREVER
        else:
            raise ValueError(f"모르는 일시정지: {span}")
        self._save({**self._state(), "pause_until": until})
        self.on_change()

    def resume(self) -> None:
        self._save({**self._state(), "pause_until": None})
        self.on_change()

    # ---------- 실행 ----------

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self.started_at = self.clock()
            self.progress = None
            self.last = Attempt(self.started_at)
        try:
            child = self.spawn()
        except OSError as e:
            log.warning("수집을 띄우지 못했다: %s", e)
            self._finish(ok=False)
            return False
        self.on_change()
        self.background(lambda: self._watch(child))
        return True

    def _watch(self, child: Child) -> None:
        for line in child.stdout:
            self.feed(line)
        code = child.wait()
        self._finish(ok=code == 0 and self._finished_today())

    def feed(self, line: str) -> None:
        """자식의 stdout 한 줄. 진행 줄만 읽고 나머지는 로그로 둔다."""
        line = line.strip()
        if not line.startswith("{"):
            if line:
                log.info("sync: %s", line)
            return
        try:
            event = json.loads(line)
            if event.get("event") == "progress":
                self.progress = Progress(event["stage"], int(event["done"]), int(event["total"]))
        except (ValueError, KeyError, TypeError, AttributeError):
            return
        self.on_change()

    def _finished_today(self) -> bool:
        record = self.today_record()
        return bool(record and record.get("status") == "done")

    def _finish(self, ok: bool) -> None:
        with self._lock:
            self.last = Attempt(self.last.at if self.last else self.clock(), ok)
            self.started_at = None
            self.progress = None
        self.on_finish()
        self.on_change()

    # ---------- 상태 파일 ----------

    def _state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def run_forever(
    scheduler: Scheduler, stop: threading.Event, every: Callable[[], None] = lambda: None
) -> None:  # pragma: no cover - 실제 스레드에서 1분마다 돈다
    """앱 시작을 첫 계기로 두고 1분마다 tick한다. every는 tick마다 함께 부른다 (알림 확인)."""
    if not scheduler.trigger("start"):
        scheduler.on_change()  # 첫 메뉴를 그린다 (오늘 수집이 이미 끝났어도)
    every()
    while not stop.wait(TICK_SECONDS):
        scheduler.tick()
        every()


def sync_argv(wiki_root: Path) -> list[str]:
    """`ctx sync --events`를 띄우는 명령. 묶은 앱(PyInstaller)에서는 실행 파일이 곧 ctx다."""
    head = (
        [sys.executable]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "context_holder"]
    )
    return [*head, "sync", "--events", "--wiki", str(wiki_root)]


def spawn_sync(wiki_root: Path, log_path: Path) -> Child:  # pragma: no cover - 실제 프로세스
    log_path.parent.mkdir(parents=True, exist_ok=True)
    errors = log_path.open("a", encoding="utf-8")
    return subprocess.Popen(
        sync_argv(wiki_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=errors,
        text=True,
        bufsize=1,
    )


def _thread(work: Callable[[], None]) -> None:  # pragma: no cover - 실제 스레드
    threading.Thread(target=work, daemon=True).start()


def _end_of(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), datetime.min.time()).astimezone()
