import pytest

from context_holder.collectrun import Checkpoint, Work, run_locked
from context_holder.gitsync import GitSyncError
from context_holder.runlock import LockStatus, Outcome


class FakeLock:
    name = "2026-11-06"

    def __init__(self, outcome=Outcome.ACQUIRED, fail_on=()):
        self.outcome = outcome
        self.fail_on = set(fail_on)
        self.events: list[tuple] = []

    def acquire(self, blockers=()):
        self.events.append(("acquire", tuple(blockers)))
        return LockStatus(self.outcome, {"device": "a"})

    def heartbeat(self, message):
        self.events.append(("heartbeat", message))
        if "heartbeat" in self.fail_on:
            raise GitSyncError("offline")

    def finish(self, result, message):
        self.events.append(("finish", result))
        if "finish" in self.fail_on:
            raise GitSyncError("offline")

    def release(self, result, message):
        self.events.append(("release", result))


def test_checkpoint_pushes_every_n_articles():
    lock = FakeLock()
    checkpoint = Checkpoint(lock, every=2)
    for _ in range(5):
        checkpoint.tick()
    assert [e for e in lock.events if e[0] == "heartbeat"] == [
        ("heartbeat", "수집 중: 2026-11-06 2건"),
        ("heartbeat", "수집 중: 2026-11-06 4건"),
    ]


def test_checkpoint_keeps_going_when_offline():
    lock = FakeLock(fail_on={"heartbeat"})
    Checkpoint(lock, every=1).tick()  # 예외를 내지 않는다
    assert lock.events == [("heartbeat", "수집 중: 2026-11-06 1건")]


def test_finished_work_marks_the_lock_done():
    lock = FakeLock()

    def work(checkpoint):
        checkpoint.tick()
        return Work("report", {"saved": 1}, finished=True)

    run = run_locked(lock, work, blockers=["init"])

    assert run.work.result == "report"
    assert run.push_error is None
    assert lock.events == [("acquire", ("init",)), ("finish", {"saved": 1})]


def test_stopped_work_releases_the_lock():
    lock = FakeLock()
    run = run_locked(lock, lambda c: Work("r", {"saved": 0}, finished=False))
    assert run.status.outcome is Outcome.ACQUIRED
    assert lock.events[-1] == ("release", {"saved": 0})


@pytest.mark.parametrize("outcome", [Outcome.DONE, Outcome.BUSY])
def test_work_is_skipped_without_the_lock(outcome):
    lock = FakeLock(outcome)
    run = run_locked(lock, lambda c: pytest.fail("수집하면 안 된다"))
    assert run.work is None
    assert run.status.outcome is outcome


def test_crash_releases_the_lock_and_reraises():
    lock = FakeLock()

    def work(checkpoint):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_locked(lock, work)
    assert lock.events[-1] == ("release", {})


def test_final_push_failure_is_reported():
    lock = FakeLock(fail_on={"finish"})
    run = run_locked(lock, lambda c: Work("r", {}, finished=True))
    assert run.push_error == "offline"
