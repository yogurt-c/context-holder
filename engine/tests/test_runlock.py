from datetime import UTC, datetime, timedelta

from context_holder.runlock import INIT_LOCK, Outcome, RunLock, daily_lock

START = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = START):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def lock_for(repo, device: str, clock: Clock, name: str = "2026-11-06") -> RunLock:
    return RunLock(repo, name, device, clock)


def test_daily_lock_uses_local_date():
    assert daily_lock(START) == START.astimezone().date().isoformat()


def test_first_device_takes_the_lock_and_pushes_it(make_device):
    a, b = make_device("a"), make_device("b")
    clock = Clock()

    status = lock_for(a, "a", clock).acquire()

    assert status.outcome is Outcome.ACQUIRED
    assert status.took_over is None
    b.pull()
    record = lock_for(b, "b", clock).read()
    assert record["device"] == "a"
    assert record["status"] == "running"


def test_other_device_waits_while_running(make_device):
    a, b = make_device("a"), make_device("b")
    clock = Clock()
    lock_for(a, "a", clock).acquire()

    clock.now += timedelta(hours=1)
    status = lock_for(b, "b", clock).acquire()

    assert status.outcome is Outcome.BUSY
    assert status.holder["device"] == "a"


def test_done_today_means_no_collect(make_device):
    a, b = make_device("a"), make_device("b")
    clock = Clock()
    lock_a = lock_for(a, "a", clock)
    lock_a.acquire()
    lock_a.finish({"saved": 3}, "done")

    status = lock_for(b, "b", clock).acquire()
    assert status.outcome is Outcome.DONE
    assert status.holder["result"] == {"saved": 3}
    assert lock_for(a, "a", clock).acquire().outcome is Outcome.DONE  # 같은 기기도 다시 안 한다


def test_heartbeat_keeps_long_runs_alive(make_device):
    a, b = make_device("a"), make_device("b")
    clock = Clock()
    lock_a = lock_for(a, "a", clock)
    lock_a.acquire()

    clock.now += timedelta(hours=1, minutes=50)
    (a.root / "wiki").mkdir()
    (a.root / "wiki/x.md").write_text("x")
    lock_a.heartbeat("20건")
    clock.now += timedelta(hours=1, minutes=50)  # 시작 후 3시간 40분, heartbeat 후 1시간 50분

    assert lock_for(b, "b", clock).acquire().outcome is Outcome.BUSY
    assert (b.root / "wiki/x.md").exists()  # heartbeat와 함께 쓴 글도 올라갔다


def test_heartbeat_without_changes_does_not_fail(make_device):
    a = make_device("a")
    lock_a = lock_for(a, "a", Clock())
    lock_a.acquire()
    lock_a.heartbeat("same")
    lock_a.heartbeat("same")  # 시간이 같으면 바뀐 게 없다


def test_stale_lock_is_taken_over(make_device):
    a, b = make_device("a"), make_device("b")
    clock = Clock()
    lock_for(a, "a", clock).acquire()

    clock.now += timedelta(hours=2, minutes=1)  # a가 중간에 꺼졌다
    status = lock_for(b, "b", clock).acquire()

    assert status.outcome is Outcome.ACQUIRED
    assert status.took_over == "a"
    assert status.holder["device"] == "b"


def test_same_device_resumes_its_own_run(make_device):
    a = make_device("a")
    clock = Clock()
    lock_for(a, "a", clock).acquire()  # 앱이 죽어서 끝내지 못했다

    status = lock_for(a, "a", clock).acquire()
    assert status.outcome is Outcome.ACQUIRED
    assert status.took_over is None


def test_stopped_run_can_be_retried_the_same_day(make_device):
    a, b = make_device("a"), make_device("b")
    clock = Clock()
    lock_a = lock_for(a, "a", clock)
    lock_a.acquire()
    lock_a.release({"saved": 1}, "limit")  # 한도에 걸려 멈췄다

    status = lock_for(b, "b", clock).acquire()
    assert status.outcome is Outcome.ACQUIRED
    assert status.took_over is None


def test_race_only_one_device_wins(make_device, monkeypatch):
    a, b = make_device("a"), make_device("b")
    clock = Clock()
    lock_b = lock_for(b, "b", clock)
    real_pull = b.pull
    calls = []

    def pull_then_a_pushes():
        result = real_pull()
        if not calls:  # b가 확인한 직후 a가 먼저 잠금을 push한다
            calls.append(lock_for(a, "a", clock).acquire())
        return result

    monkeypatch.setattr(b, "pull", pull_then_a_pushes)
    status = lock_b.acquire()

    assert calls[0].outcome is Outcome.ACQUIRED
    assert status.outcome is Outcome.BUSY
    assert status.holder["device"] == "a"
    assert lock_b.read()["device"] == "a"


def test_daily_collect_waits_for_init_on_another_device(make_device):
    a, b = make_device("a"), make_device("b")
    clock = Clock()
    lock_for(a, "a", clock, INIT_LOCK).acquire()

    status = lock_for(b, "b", clock).acquire(blockers=[INIT_LOCK])
    assert status.outcome is Outcome.BUSY
    assert status.holder["device"] == "a"

    mine = lock_for(a, "a", clock).acquire(blockers=[INIT_LOCK])  # init을 돌리는 기기 자신은 된다
    assert mine.outcome is Outcome.ACQUIRED


def test_finished_init_does_not_block(make_device):
    a, b = make_device("a"), make_device("b")
    clock = Clock()
    init = lock_for(a, "a", clock, INIT_LOCK)
    init.acquire()
    init.finish({}, "init done")

    assert lock_for(b, "b", clock).acquire(blockers=[INIT_LOCK]).outcome is Outcome.ACQUIRED


def test_records_are_written_without_a_previous_file(make_device):
    a = make_device("a")
    clock = Clock()
    lock_a = lock_for(a, "a", clock)
    lock_a.finish({"saved": 0}, "finish without acquire")
    assert lock_a.read()["status"] == "done"
    lock_b = lock_for(a, "a", clock, "other")
    lock_b.heartbeat("beat")
    lock_c = lock_for(a, "a", clock, "third")
    lock_c.release({}, "release")
    assert lock_c.read()["status"] == "stopped"
