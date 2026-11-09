"""메뉴바 메뉴 모델, 알림 규칙, 앱과 주고받는 줄."""

import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from context_holder.classify import Bucket
from context_holder.index import Index
from context_holder.scheduler import Progress
from context_holder.tray import AppBridge, Notice, NoticeLog, notices, status_event
from tests.test_scheduler import MONDAY_8, FakeChild, record, seoul_time, setup  # noqa: F401
from tests.test_screens import SOURCES, add

FRIDAY_8 = MONDAY_8 + timedelta(days=4)


def labels(event) -> list[str]:
    return [i.get("label", "—") for i in event["items"]]


def item(event, label_start: str) -> dict:
    return next(i for i in event["items"] if i.get("label", "").startswith(label_start))


@pytest.fixture
def index(setup, tmp_path):  # noqa: F811
    _scheduler, wiki, *_ = setup
    wiki.ensure_config()
    (wiki.root / "config/sources.yaml").write_text(SOURCES, encoding="utf-8")
    return Index(tmp_path / "index.db")


# ---------- 메뉴 ----------


def test_menu_before_first_run_today(setup, index):  # noqa: F811
    scheduler, *_ = setup
    event = status_event(scheduler, index)
    assert event["icon"] == "normal"
    assert labels(event)[:2] == ["오늘 아직 수집 전", "평일 09:30, Mac을 켜거나 깨울 때 수집해요"]
    assert item(event, "지금 수집") == {"label": "지금 수집", "enabled": True, "cmd": "sync_now"}
    pause = item(event, "수집 일시정지")
    assert [c["cmd"] for c in pause["children"]] == ["pause 1h", "pause today", "pause forever"]
    assert event["items"][-1] == {"label": "context-holder 종료", "enabled": True, "quit": True}
    assert item(event, "Inbox")["open"] == "/inbox" and item(event, "설정")["open"] == "/settings"
    assert all(not i.get("label", "").startswith("요약 대기") for i in event["items"])


def test_menu_after_done_shows_lead_counts_and_next_run(setup, index):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    clock.now = FRIDAY_8
    add(wiki, "https://t/a", "사이드카 운영기 " * 6, 0.95, collected=clock.now, topic="사이드카")
    add(
        wiki,
        "https://t/b",
        "보류 글",
        0.5,
        collected=clock.now,
        bucket=Bucket.INBOX,
    )
    add(wiki, "https://t/c", "대기 글", collected=clock.now, pending=True)
    index.sync(wiki)
    record(
        wiki,
        clock.now,
        status="done",
        device="air",
        finished_at=clock.now.isoformat(),
        result={"saved": 2, "inboxed": 1, "dropped": 2, "sources": 13, "failed_sources": 2},
    )

    event = status_event(scheduler, index)

    assert event["icon"] == "normal"
    head, sub = labels(event)[:2]
    assert head == "오늘 08:00 수집 완료, 신규 5건 · 소스 2곳 실패"
    assert sub == "이 기기에서 수집 · 다음 수집 월요일 09:30"
    lead = event["items"][3]
    assert lead["open"] == "/today" and lead["label"].endswith("…") and len(lead["label"]) == 40
    assert item(event, "오늘 수집 완료")["enabled"] is False
    assert item(event, "Inbox 정리 (1)")["open"] == "/inbox"
    assert item(event, "요약 대기 1건")["enabled"] is False


def test_menu_icon_turns_error_when_half_the_sources_failed(setup, index):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    record(
        wiki, clock.now, status="done", device="studio", result={"sources": 4, "failed_sources": 2}
    )
    event = status_event(scheduler, index)
    assert event["icon"] == "error" and labels(event)[1].startswith("studio에서 수집")
    assert labels(event)[0].startswith("오늘 --:-- 수집 완료")


def test_menu_while_running_shows_progress(setup, index):  # noqa: F811
    scheduler, *_ = setup
    seen = []

    def during():
        seen.append(status_event(scheduler, index))

    scheduler.background = lambda work: work()
    scheduler.spawn = lambda: FakeChild(
        ['{"event": "progress", "stage": "collect", "done": 7, "total": 13}'], on_wait=during
    )
    scheduler.run_now()
    (event,) = seen
    assert event["icon"] == "busy"
    assert labels(event)[:2] == ["수집 중 · 소스 7/13", "이 기기에서 08:00에 시작"]
    assert item(event, "수집 중…")["enabled"] is False


def test_menu_when_other_device_runs_paused_stopped_or_failed(setup, index):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    record(wiki, clock.now, status="running", device="studio", heartbeat_at=clock.now.isoformat())
    event = status_event(scheduler, index)
    assert event["icon"] == "busy" and labels(event)[0] == "studio가 수집 중"
    assert item(event, "지금 수집")["enabled"] is False

    record(wiki, clock.now, status="stopped")
    scheduler.pause("1h")
    event = status_event(scheduler, index)
    assert event["icon"] == "paused" and labels(event)[:2] == ["수집 일시정지", "09:00까지"]
    assert item(event, "수집 다시 켜기")["cmd"] == "resume"
    scheduler.pause("forever")
    assert labels(status_event(scheduler, index))[1] == "다시 켤 때까지"
    clock.now += timedelta(hours=20)
    scheduler.pause("today")
    assert labels(status_event(scheduler, index))[1].endswith("00:00까지")
    scheduler.resume()
    record(wiki, clock.now, status="stopped")  # 날짜가 바뀌었다

    event = status_event(scheduler, index)
    assert event["icon"] == "error" and labels(event)[0] == "수집이 멈췄어요"


def test_menu_after_failed_attempt(setup, index):  # noqa: F811
    scheduler, *_ = setup
    scheduler.spawn = lambda: FakeChild(code=1)
    scheduler.run_now()
    event = status_event(scheduler, index)
    assert event["icon"] == "error"
    assert labels(event)[:2] == ["수집하지 못했어요", "08:00에 실패 · 30분 뒤 다시 해요"]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (MONDAY_8, "내일 09:30"),
        (FRIDAY_8, "월요일 09:30"),
        (FRIDAY_8 + timedelta(days=1), "월요일 09:30"),
    ],
)
def test_next_run_skips_weekends(setup, index, day, expected):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    clock.now = day
    record(wiki, clock.now, status="done", device="air", finished_at=day.isoformat())
    assert labels(status_event(scheduler, index))[1].endswith(expected)


# ---------- 알림 ----------


def test_brief_notice_names_the_lead_and_counts(setup, index):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    add(wiki, "https://t/a", "리드 글", 0.9, collected=clock.now, topic="사이드카")
    add(wiki, "https://t/b", "둘째 글", 0.5, collected=clock.now)
    index.sync(wiki)
    record(wiki, clock.now, status="done", device="air", result={"saved": 2})
    assert notices(scheduler, index) == [
        Notice("brief", "오늘의 브리프, 신규 2건", "리드 글 외 1건. 결정 사례 1건", "/today")
    ]


def test_brief_notice_with_one_article_or_index_not_ready(setup, index):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    record(wiki, clock.now, status="done", device="air", result={"saved": 1})
    assert notices(scheduler, index)[0].body == "눌러서 브리프를 보세요."
    add(wiki, "https://t/a", "하나뿐", collected=clock.now)
    index.sync(wiki)
    assert notices(scheduler, index)[0].body == "하나뿐"


def test_quiet_results_and_other_devices_do_not_notify(setup, index):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    record(wiki, clock.now, status="done", device="air", result={"saved": 0, "sources": 13})
    assert notices(scheduler, index) == []
    record(wiki, clock.now, status="done", device="studio", result={"saved": 5})
    assert notices(scheduler, index) == []


def test_claude_and_source_failure_notices(setup, index):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    result = {"stopped": "auth", "sources": 13, "failed_sources": 7}
    record(wiki, clock.now, status="stopped", device="air", result=result)
    assert [(n.kind, n.title, n.open) for n in notices(scheduler, index)] == [
        ("claude", "Claude 연결이 끊겼어요", "/today"),
        ("sources", "소스 7곳에서 수집하지 못했어요", "/sources"),
    ]


def test_stale_notice_after_three_days(setup, index):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    record(wiki, clock.now - timedelta(days=2), status="done")
    assert notices(scheduler, index) == []
    record(wiki, clock.now - timedelta(days=3), status="done")
    (wiki.root / f"state/runs/{(clock.now - timedelta(days=2)).astimezone().date()}.json").unlink()
    assert [n.title for n in notices(scheduler, index)] == ["3일째 수집하지 못했어요"]


def test_notice_log_limits_per_day_and_kind(tmp_path):
    log = NoticeLog(tmp_path / "notices.json")
    day = MONDAY_8.date()
    a, b, c = (Notice(k, k, "", "/") for k in ("brief", "claude", "sources"))
    assert log.take([a, a], day) == [a]
    assert log.take([a, b, c], day) == [b]  # 하루 2번까지
    assert log.take([c], day) == []
    assert log.take([c], day + timedelta(days=1)) == [c]
    (tmp_path / "notices.json").write_text("{broken")
    assert log.take([a], day) == [a]


# ---------- 앱과 주고받기 ----------


def test_bridge_emits_status_once_per_change_and_notices(setup, index):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    out = io.StringIO()
    bridge = AppBridge(out, index, NoticeLog(wiki.root.parent / "notices.json"))
    bridge.refresh()
    bridge.check_notices()
    assert out.getvalue() == ""  # 스케줄러가 붙기 전
    bridge.scheduler = scheduler
    bridge.refresh()
    bridge.refresh()
    record(wiki, clock.now, status="done", device="air", result={"saved": 1})
    bridge.check_notices()
    bridge.check_notices()
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [e["event"] for e in events] == [
        "status",
        "notify",
    ]  # 설정 전이라 자동 실행은 알리지 않는다


def test_bridge_commands(setup, index):  # noqa: F811
    scheduler, wiki, clock, *_ = setup
    bridge = AppBridge(io.StringIO(), index, NoticeLog(wiki.root.parent / "n.json"))
    bridge.command("pause 1h")  # 스케줄러가 없으면 무시
    bridge.scheduler = scheduler
    bridge.command("pause today")
    assert scheduler.paused_until() is not None
    bridge.command("resume")
    assert scheduler.paused_until() is None
    bridge.command("pause week")
    bridge.command("dance")
    assert scheduler.paused_until() is None
    scheduler.spawn = lambda: FakeChild(on_wait=lambda: record(wiki, clock.now, status="done"))
    bridge.command("sync_now")
    assert scheduler.done_today()


def test_bridge_survives_closed_pipe(setup, index):  # noqa: F811
    class Closed:
        def write(self, _):
            raise BrokenPipeError

        def flush(self):  # pragma: no cover - write에서 끝난다
            pass

    bridge = AppBridge(Closed(), index, NoticeLog(setup[1].root / "n.json"))
    bridge.emit({"event": "status"})


def test_progress_dataclass_reexport():
    assert Progress("collect", 1, 2).label == "소스 1/2"


def test_clock_constant_is_monday():
    assert MONDAY_8.astimezone().weekday() in (0, 6) and MONDAY_8.tzinfo is UTC
    assert datetime(2026, 11, 9).weekday() == 0
