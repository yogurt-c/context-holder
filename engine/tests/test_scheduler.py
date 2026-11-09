"""매일 수집 스케줄러: 계기, 건너뛰기, 재시도 간격, 일시정지, 진행률."""

import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

from context_holder.scheduler import (
    FOREVER,
    RETRY_AFTER,
    Progress,
    Scheduler,
    sync_argv,
)
from context_holder.wiki import Wiki

# 2026-11-09은 월요일. 서울 기준 08:00 (UTC 23:00 전날)
MONDAY_8 = datetime(2026, 11, 8, 23, tzinfo=UTC)


class FakeChild:
    def __init__(self, lines=(), code=0, on_wait=None):
        self.stdout = list(lines)
        self.code = code
        self.on_wait = on_wait

    def wait(self):
        if self.on_wait:
            self.on_wait()
        return self.code


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def record(wiki: Wiki, now: datetime, **values) -> None:
    path = wiki.root / f"state/runs/{now.astimezone().date().isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")


@pytest.fixture
def setup(tmp_path):
    wiki = Wiki(tmp_path / "wiki")
    clock = Clock(MONDAY_8)
    children: list[FakeChild] = []
    spawned = []
    changes = []
    finished = []

    def spawn():
        child = children.pop(0) if children else FakeChild()
        spawned.append(clock.now)
        return child

    scheduler = Scheduler(
        wiki,
        tmp_path / "app-state.json",
        "air",
        clock,
        spawn,
        on_change=lambda: changes.append(1),
        on_finish=lambda: finished.append(1),
        background=lambda work: work(),  # 테스트에서는 바로 끝까지 읽는다
    )
    return scheduler, wiki, clock, children, spawned, changes, finished


def test_start_runs_once_and_done_record_stops_more_runs(setup):
    scheduler, wiki, clock, children, spawned, _, finished = setup
    children.append(FakeChild(on_wait=lambda: record(wiki, clock.now, status="done")))

    assert scheduler.trigger("start")
    assert scheduler.last.ok is True and not scheduler.running and finished == [1]
    clock.now += timedelta(hours=3)
    assert not scheduler.trigger("daily")  # 오늘은 끝났다
    assert len(spawned) == 1


def test_failure_waits_before_retrying(setup):
    scheduler, _wiki, clock, children, spawned, _, _ = setup
    children.append(FakeChild(code=1))

    scheduler.trigger("start")
    assert scheduler.last.ok is False
    clock.now += RETRY_AFTER - timedelta(minutes=1)
    assert not scheduler.trigger("wake")
    clock.now += timedelta(minutes=2)
    assert scheduler.trigger("wake") and len(spawned) == 2


def test_exit_zero_without_done_record_counts_as_not_finished(setup):
    scheduler, wiki, clock, children, _, _, _ = setup
    children.append(FakeChild(on_wait=lambda: record(wiki, clock.now, status="stopped")))
    scheduler.trigger("start")
    assert scheduler.last.ok is False  # 한도로 멈췄다: 나중에 다시 한다


def test_other_device_running_blocks_until_stale(setup):
    scheduler, wiki, clock, _, spawned, _, _ = setup
    record(wiki, clock.now, status="running", device="studio", heartbeat_at=clock.now.isoformat())
    assert scheduler.other_device_running() and not scheduler.can_run_now()
    assert not scheduler.trigger("start")
    clock.now += timedelta(hours=3)
    assert not scheduler.other_device_running() and scheduler.trigger("wake")
    assert len(spawned) == 1


@pytest.mark.parametrize(
    "values",
    [
        {"status": "running", "device": "air", "heartbeat_at": MONDAY_8.isoformat()},
        {"status": "running", "device": "studio"},
        {"status": "running", "device": "studio", "heartbeat_at": "bad"},
    ],
)
def test_own_or_broken_running_records_do_not_block(setup, values):
    scheduler, wiki, clock, *_ = setup
    record(wiki, clock.now, **values)
    assert not scheduler.other_device_running()


def test_broken_record_reads_as_none(setup):
    scheduler, wiki, clock, *_ = setup
    record(wiki, clock.now)
    path = next((wiki.root / "state/runs").glob("*.json"))
    path.write_text("{broken")
    assert scheduler.today_record() is None


def test_tick_triggers_on_weekday_after_0930_and_on_wake(setup):
    scheduler, _, clock, children, spawned, changes, _ = setup
    children += [FakeChild(code=1), FakeChild(code=1)]
    scheduler.tick()  # 08:00: 계기 아님, 메뉴 문구만 새로
    assert spawned == [] and changes == [1]
    clock.now += timedelta(minutes=1)
    scheduler.tick()
    assert spawned == []
    clock.now += timedelta(minutes=10)  # 잠들었다 깼다
    scheduler.tick()
    assert spawned == [clock.now]
    clock.now = MONDAY_8 + timedelta(hours=2)  # 10:00
    scheduler._last_tick = clock.now - timedelta(minutes=1)
    scheduler.tick()
    assert len(spawned) == 2  # 평일 09:30 이후 (재시도 간격도 지났다)


def test_weekend_morning_waits_for_start_or_wake(setup):
    scheduler, _, clock, _, spawned, _, _ = setup
    clock.now = datetime(2026, 11, 14, 2, tzinfo=UTC)  # 토요일 11:00
    scheduler.tick()
    assert spawned == []


def test_pause_blocks_triggers_but_not_run_now(setup):
    scheduler, wiki, clock, children, spawned, _changes, _ = setup
    scheduler.pause("1h")
    assert scheduler.paused_until() == clock.now + timedelta(hours=1)
    assert not scheduler.trigger("start")
    children.append(FakeChild(on_wait=lambda: record(wiki, clock.now, status="done")))
    assert scheduler.run_now() and len(spawned) == 1
    assert not scheduler.run_now()  # 오늘은 끝났다


def test_pause_spans_and_resume(setup):
    scheduler, _, clock, *_ = setup
    scheduler.pause("today")
    assert scheduler.paused_until().astimezone().date() > clock.now.astimezone().date()
    scheduler.pause(FOREVER)
    assert scheduler.paused_until() == FOREVER
    clock.now += timedelta(days=30)
    assert scheduler.paused_until() == FOREVER
    scheduler.resume()
    assert scheduler.paused_until() is None
    scheduler.pause("1h")
    clock.now += timedelta(hours=2)
    assert scheduler.paused_until() is None  # 지났다
    with pytest.raises(ValueError):
        scheduler.pause("week")


def test_broken_state_file_means_not_paused(setup):
    scheduler, *_ = setup
    scheduler.state_path.write_text("{broken")
    assert scheduler.paused_until() is None


def test_progress_lines_update_and_other_lines_are_logged(setup, caplog):
    scheduler, *_ = setup
    seen = []
    scheduler.on_change = lambda: seen.append(scheduler.progress)
    scheduler.feed('{"event": "progress", "stage": "collect", "done": 7, "total": 13}\n')
    scheduler.feed('{"event": "progress", "stage": "classify"}')  # 깨진 줄
    scheduler.feed("{not json")
    scheduler.feed('{"event": "other"}')
    with caplog.at_level("INFO", logger="context_holder.scheduler"):
        scheduler.feed("저장 1 · 보류 0\n")
        scheduler.feed("\n")
    assert seen == [Progress("collect", 7, 13), Progress("collect", 7, 13)]
    assert "sync: 저장 1 · 보류 0" in caplog.text


def test_running_child_shows_progress_and_blocks_second_start(setup):
    scheduler, _wiki, _clock, children, spawned, _, _ = setup
    observed = []

    def during():
        observed.append((scheduler.running, scheduler.progress, scheduler.start()))

    lines = ['{"event": "progress", "stage": "enrich", "done": 3, "total": 8}']
    children.append(FakeChild(lines, on_wait=during))
    scheduler.run_now()
    assert observed == [(True, Progress("enrich", 3, 8), False)]
    assert scheduler.progress is None and len(spawned) == 1


def test_spawn_failure_counts_as_failed_attempt(setup):
    scheduler, *_ = setup

    def broken():
        raise OSError("없음")

    scheduler.spawn = broken
    assert not scheduler.start()
    assert scheduler.last.ok is False and not scheduler.running


def test_progress_labels():
    assert Progress("collect", 7, 13).label == "소스 7/13"
    assert Progress("enrich", 3, 8).label == "요약 3/8"
    assert Progress("classify", 0, 11).label == "분류 중 (11건)"
    assert Progress("retry", 1, 2).label == "요약 대기 다시 만들기 1/2"
    assert Progress("new", 1, 2).label == "new 1/2"


def test_sync_argv_uses_module_or_frozen_executable(tmp_path, monkeypatch):
    assert sync_argv(tmp_path) == [
        sys.executable,
        "-m",
        "context_holder",
        "sync",
        "--events",
        "--wiki",
        str(tmp_path),
    ]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert sync_argv(tmp_path)[:2] == [sys.executable, "sync"]


def test_tick_redraws_menu_when_nothing_runs(setup):
    scheduler, wiki, clock, _, spawned, changes, _ = setup
    clock.now = MONDAY_8 + timedelta(hours=2)  # 평일 10:00, 오늘은 이미 끝났다
    record(wiki, clock.now, status="done")
    scheduler.tick()
    assert spawned == [] and changes == [1]
