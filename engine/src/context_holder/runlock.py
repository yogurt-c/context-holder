"""git push를 잠금으로 써서 하루 한 기기만 수집한다 (plan 3.1).

1. pull 후 `state/runs/{이름}.json`을 본다.
2. 끝났거나 다른 기기가 돌리는 중이면 수집하지 않는다.
3. 없으면 `running`으로 써서 커밋하고 바로 push한다. push가 이기면 이 기기가 맡는다.
4. 수집하는 동안 일정 건수마다 `heartbeat_at`을 갱신해 push한다.
   마지막 heartbeat 후 STALE_AFTER가 지나면 기기가 꺼진 것으로 보고 다른 기기가 이어받는다.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from context_holder.gitsync import WikiRepo

STALE_AFTER = timedelta(hours=2)
INIT_LOCK = "init"
RUNS_DIR = "state/runs"

Clock = Callable[[], datetime]


class Outcome(Enum):
    ACQUIRED = "acquired"  # 이 기기가 맡는다
    DONE = "done"  # 오늘 수집은 이미 끝났다
    BUSY = "busy"  # 다른 기기가 돌리는 중이다


@dataclass(frozen=True)
class LockStatus:
    outcome: Outcome
    holder: dict[str, Any] | None = None  # 현재 잠금 기록 (BUSY · DONE일 때 누가 했는지)
    took_over: str | None = None  # 멈춘 실행을 이어받았으면 그 기기 이름


def daily_lock(now: datetime) -> str:
    """기기의 현지 날짜 기준으로 하루 한 번."""
    return now.astimezone().date().isoformat()


class RunLock:
    def __init__(self, repo: WikiRepo, name: str, device: str, clock: Clock):
        self.repo = repo
        self.name = name
        self.device = device
        self.clock = clock

    @property
    def path(self):
        return self.repo.root / RUNS_DIR / f"{self.name}.json"

    def read(self, name: str | None = None) -> dict[str, Any] | None:
        path = self.repo.root / RUNS_DIR / f"{name or self.name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def acquire(self, blockers: Iterable[str] = ()) -> LockStatus:
        """blockers: 이 잠금들이 돌아가는 중이면 함께 기다린다 (매일 수집은 init을 기다린다)."""
        self.repo.pull()
        blocked = self._check(blockers)
        if blocked:
            return blocked
        took_over = self._stale_holder()
        now = self.clock().isoformat()
        self._write(
            {"device": self.device, "status": "running", "started_at": now, "heartbeat_at": now}
        )
        self.repo.commit_all(f"수집 시작: {self.name} ({self.device})")
        self.repo.push()  # 거절되면 pull(원격 우선) 후 다시 push한다
        mine = self.read()
        if not mine or mine.get("device") != self.device or mine.get("status") != "running":
            return LockStatus(Outcome.BUSY, mine)  # 다른 기기가 먼저 잡았다
        return LockStatus(Outcome.ACQUIRED, mine, took_over)

    def _check(self, blockers: Iterable[str]) -> LockStatus | None:
        current = self.read()
        if current and current.get("status") == "done":
            return LockStatus(Outcome.DONE, current)
        for name in [self.name, *blockers]:
            record = self.read(name)
            if record and self._alive(record) and record.get("device") != self.device:
                return LockStatus(Outcome.BUSY, record)
        return None

    def _stale_holder(self) -> str | None:
        current = self.read()
        if current and current.get("status") == "running" and current["device"] != self.device:
            return current["device"]
        return None

    def _alive(self, record: dict[str, Any]) -> bool:
        if record.get("status") != "running":
            return False
        beat = datetime.fromisoformat(record["heartbeat_at"])
        return self.clock() - beat < STALE_AFTER

    def heartbeat(self, message: str) -> None:
        """진행 중인 결과와 함께 잠금을 갱신해 push한다."""
        record = self.read() or {}
        self._write({**record, "heartbeat_at": self.clock().isoformat()})
        if self.repo.commit_all(message):
            self.repo.push()

    def finish(self, result: dict[str, Any], message: str) -> None:
        record = self.read() or {}
        now = self.clock().isoformat()
        self._write(
            {**record, "status": "done", "heartbeat_at": now, "finished_at": now, "result": result}
        )
        self.repo.commit_all(message)
        self.repo.push()

    def release(self, result: dict[str, Any], message: str) -> None:
        """한도 · 로그인 문제로 멈췄다. 다음 실행이나 다른 기기가 바로 이어받게 잠금을 푼다."""
        record = self.read() or {}
        now = self.clock().isoformat()
        self._write({**record, "status": "stopped", "heartbeat_at": now, "result": result})
        self.repo.commit_all(message)
        self.repo.push()

    def _write(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
