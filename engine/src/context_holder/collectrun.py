"""수집 잠금을 잡고 수집을 돌린 뒤, 일정 건수마다 커밋 · push한다 (plan 3.1, 5장 0 · 5단계)."""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from context_holder.gitsync import GitSyncError
from context_holder.runlock import LockStatus, Outcome, RunLock

log = logging.getLogger(__name__)

CHECKPOINT_EVERY = 20  # 이만큼 쓸 때마다 heartbeat와 함께 push한다


@dataclass
class Checkpoint:
    """글을 쓸 때마다 tick한다. 기기가 중간에 꺼져도 push한 만큼은 남는다."""

    lock: RunLock
    every: int = CHECKPOINT_EVERY
    count: int = 0

    def tick(self) -> None:
        self.count += 1
        if self.count % self.every == 0:
            self.save()

    def save(self) -> None:
        try:
            self.lock.heartbeat(f"수집 중: {self.lock.name} {self.count}건")
        except GitSyncError as e:
            log.warning("push하지 못했다. 다음에 다시 올린다: %s", e)


@dataclass(frozen=True)
class Work[T]:
    result: T
    summary: dict[str, Any]  # 잠금 파일에 남길 결과 수치
    finished: bool  # False면 한도 · 로그인 문제로 멈췄다. 잠금을 풀어 다시 시도하게 한다


@dataclass(frozen=True)
class LockedRun[T]:
    status: LockStatus
    work: Work[T] | None = None
    push_error: str | None = None  # 마지막 push 실패. 로컬 커밋은 다음 실행에서 올린다


def run_locked[T](
    lock: RunLock,
    work: Callable[[Checkpoint], Work[T]],
    blockers: Iterable[str] = (),
) -> LockedRun[T]:
    status = lock.acquire(blockers)
    if status.outcome is not Outcome.ACQUIRED:
        return LockedRun(status)
    checkpoint = Checkpoint(lock)
    try:
        done = work(checkpoint)
    except BaseException:
        _try(lambda: lock.release({}, f"수집 중단: {lock.name}"))
        raise
    if done.finished:
        error = _try(lambda: lock.finish(done.summary, f"수집 완료: {lock.name}"))
    else:
        error = _try(lambda: lock.release(done.summary, f"수집 멈춤: {lock.name}"))
    return LockedRun(status, done, error)  # 마지막 push가 중간에 못 올린 커밋까지 올린다


def _try(action: Callable[[], None]) -> str | None:
    try:
        action()
    except GitSyncError as e:
        log.warning("push하지 못했다. 다음에 다시 올린다: %s", e)
        return str(e)
    return None
