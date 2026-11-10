"""앱 업데이트: 상태 모델, 앱과 주고받는 줄, 메뉴 항목, 팝업 · 설정 화면."""

import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from context_holder import __version__, appsettings
from context_holder.scheduler import Scheduler
from context_holder.screens.app import COOKIE, create_app
from context_holder.screens.updateroutes import update_view
from context_holder.tray import AppBridge, NoticeLog, status_event
from context_holder.updates import CHECK_EVERY, Updates, UpdateState, _count
from context_holder.wiki import Wiki
from tests.test_scheduler import seoul_time, setup  # noqa: F401
from tests.test_screens import SOURCES, TOKEN, add, context
from tests.test_setup import HTMX, flow, screen  # noqa: F401

NOW = datetime(2026, 11, 9, 5, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def made(tmp_path):
    events: list[dict] = []
    changes: list[int] = []
    clock = Clock()
    updates = Updates(
        events.append, tmp_path / "app-state.json", clock, on_change=lambda: changes.append(1)
    )
    return updates, events, changes, clock


def feed(updates: Updates, **data) -> None:
    updates.feed(json.dumps(data))


# ---------- 상태 ----------


def test_check_then_available(made):
    updates, events, changes, clock = made
    assert updates.current == __version__ and updates.state.state == "idle"
    assert updates.check()
    assert events == [{"event": "update", "action": "check"}]
    assert updates.state.state == "checking" and not updates.check()  # 확인 중에는 또 묻지 않는다
    clock.now += timedelta(seconds=3)
    feed(updates, state="available", version="0.2.0")
    s = updates.state
    assert (s.state, s.version, s.checked) == ("available", "0.2.0", clock.now)
    assert s.notes_url.endswith("/releases/tag/v0.2.0") and len(changes) == 2


def test_feed_ignores_broken_lines(made):
    updates, *_ = made
    for line in ("{broken", "[]", json.dumps({"version": "1"}), json.dumps({"state": "dance"})):
        updates.feed(line)
    assert updates.state == UpdateState()
    feed(updates, state="downloading", done="x", total=-5, error="e" * 500)
    assert (updates.state.done, updates.state.total, len(updates.state.error)) == (0, 0, 160)
    assert updates.state.checked is None  # 확인 중이 아니었으면 확인 시각을 바꾸지 않는다


def test_install_progress_ready_restart(made):
    updates, events, _, _ = made
    assert not updates.install()  # 받을 버전이 없다
    feed(updates, state="available", version="0.2.0")
    assert updates.install() and events[-1]["action"] == "install"
    assert updates.state.state == "downloading" and updates.state.version == "0.2.0"
    feed(updates, state="downloading", version="0.2.0", done=21_500_000, total=43_000_000)
    assert updates.state.ratio == 50 and updates.state.megabytes == "21.5 / 43.0MB"
    feed(updates, state="downloading", version="0.2.0", done=1_200_000)
    assert updates.state.ratio is None and updates.state.megabytes == "1.2MB"
    assert not updates.restart(busy=False)  # 아직 다 받지 않았다
    feed(updates, state="ready", version="0.2.0")
    assert not updates.restart(busy=True)  # 수집 중에는 기다린다
    assert updates.restart(busy=False) and events[-1]["action"] == "restart"
    assert not updates.check()  # 다 받은 뒤에는 다시 확인하지 않는다


def test_off_build_never_checks(made):
    updates, events, *_ = made
    feed(updates, state="off")
    assert not updates.check() and events == []


def test_skip_hides_toast_for_that_version(made, tmp_path):
    updates, *_ = made
    feed(updates, state="available", version="0.2.0")
    assert updates.toast()
    updates.skip()
    assert not updates.toast()
    assert appsettings.load(tmp_path / "app-state.json").skipped_update == "0.2.0"
    feed(updates, state="available", version="0.3.0")
    assert updates.toast()  # 새 버전은 다시 알린다


def test_close_hides_failure_without_skipping_the_version(made, tmp_path):
    updates, *_ = made
    feed(updates, state="error", version="0.3.0", error="network")
    assert updates.toast()  # 설치 실패는 띄운다
    updates.close()
    assert updates.state.state == "available" and not updates.toast()  # 이번 실행에서는 숨긴다
    assert appsettings.load(tmp_path / "app-state.json").skipped_update == ""  # 건너뛰지는 않는다
    feed(updates, state="available", version="0.4.0")
    assert updates.toast()
    feed(updates, state="error", error="offline")
    assert not updates.toast()  # 확인 실패는 띄우지 않는다
    updates.close()
    assert updates.state.state == "idle"


def test_toast_states(made):
    updates, *_ = made
    for state, shown in (("idle", False), ("checking", False), ("downloading", True)):
        feed(updates, state=state, version="0.2.0")
        assert updates.toast() is shown
    feed(updates, state="ready", version="0.2.0")
    assert updates.toast()


def test_tick_checks_on_start_and_every_six_hours(made, tmp_path):
    updates, events, _, clock = made
    updates.tick()
    assert len(events) == 1
    feed(updates, state="idle")
    clock.now += CHECK_EVERY - timedelta(minutes=1)
    updates.tick()
    assert len(events) == 1
    clock.now += timedelta(minutes=1)
    updates.tick()
    assert len(events) == 2
    feed(updates, state="idle")
    updates.set_auto(False)
    clock.now += CHECK_EVERY
    updates.tick()
    assert len(events) == 2 and not updates.auto()
    assert not appsettings.load(tmp_path / "app-state.json").auto_update


def test_menu_item(made):
    updates, *_ = made
    assert updates.menu_item(busy=False) is None
    feed(updates, state="available", version="0.2.0")
    assert updates.menu_item(busy=False) == {
        "label": "업데이트 0.2.0 설치",
        "enabled": True,
        "cmd": "update_install",
    }
    feed(updates, state="downloading", version="0.2.0")
    assert updates.menu_item(busy=False) == {"label": "업데이트 내려받는 중…", "enabled": False}
    feed(updates, state="ready", version="0.2.0")
    assert updates.menu_item(busy=False)["cmd"] == "update_restart"
    busy = updates.menu_item(busy=True)
    assert not busy["enabled"] and busy["label"].startswith("수집이 끝나면")


def test_count():
    assert (_count("7"), _count(None), _count([1]), _count(-3)) == (7, 0, 0, 0)


# ---------- 앱과 주고받기 ----------


def test_bridge_routes_update_lines(setup, tmp_path):  # noqa: F811
    scheduler, _wiki, clock, *_ = setup
    from context_holder.index import Index

    out = io.StringIO()
    bridge = AppBridge(out, Index(tmp_path / "i.db"), NoticeLog(tmp_path / "n.json"))
    bridge.scheduler = scheduler
    bridge.updates = Updates(bridge.emit, tmp_path / "s.json", clock, on_change=bridge.refresh)
    bridge.command('update {"state": "available", "version": "0.2.0"}')
    menu = [json.loads(line) for line in out.getvalue().splitlines()][-1]
    assert any(i.get("cmd") == "update_install" for i in menu["items"])
    bridge.command("update_install")
    assert bridge.updates.state.state == "downloading"
    bridge.command('update {"state": "ready", "version": "0.2.0"}')
    bridge.command("update_restart")
    actions = [
        e["action"] for e in map(json.loads, out.getvalue().splitlines()) if e["event"] == "update"
    ]
    assert actions == ["install", "restart"]
    bridge.command("pause today")  # 다른 명령은 그대로 스케줄러로 간다
    assert scheduler.paused_until() is not None
    bridge.updates = Updates(bridge.emit, tmp_path / "s2.json", clock)
    bridge.tick()  # 알림 확인 + 자동 업데이트 확인
    assert json.loads(out.getvalue().splitlines()[-1]) == {"event": "update", "action": "check"}
    bridge.updates = None
    bridge.tick()
    bridge.command("update_install")  # 업데이트를 모르는 엔진: 무시


def test_status_without_updates_has_no_update_item(setup, tmp_path):  # noqa: F811
    scheduler, *_ = setup
    from context_holder.index import Index

    event = status_event(scheduler, Index(tmp_path / "i.db"))
    assert not any("업데이트" in i.get("label", "") for i in event["items"])


# ---------- 화면 ----------


def make_wiki(tmp_path) -> Wiki:
    wiki = Wiki(tmp_path / "screen-wiki")
    wiki.ensure_config()
    (wiki.root / "config/sources.yaml").write_text(SOURCES, encoding="utf-8")
    return wiki


def app_client(tmp_path, updates, **extra):
    wiki = make_wiki(tmp_path)
    add(wiki, "https://d2.naver.com/a1", "글 하나")
    ctx = replace(context(wiki, tmp_path), updates=updates, app_mode=True, **extra)
    c = TestClient(create_app(ctx), follow_redirects=False)
    c.cookies.set(COOKIE, TOKEN)
    return c


def test_toast_on_every_page(made, tmp_path):
    updates, events, *_ = made
    c = app_client(tmp_path, updates)
    page = c.get("/today").text
    assert 'id="update-toast"' in page and "update-toast available" not in page
    assert "every 60s" in page
    feed(updates, state="available", version="0.2.0")
    toast = c.get("/update/toast").text
    assert "0.2.0로 업데이트할 수 있어요" in toast and "변경 사항 보기" in toast
    assert 'hx-post="/update/skip"' in toast and "이 버전 건너뛰기" in toast
    response = c.post("/update/install", headers=HTMX)
    assert response.headers["HX-Trigger"] == "update-changed"
    assert events[-1]["action"] == "install"
    feed(updates, state="downloading", version="0.2.0", done=10_000_000, total=40_000_000)
    toast = c.get("/update/toast").text
    assert "25%" in toast and "every 1s" in toast and 'aria-valuenow="25"' in toast
    # 1초마다 다시 그려도 떠오르는 효과는 처음 한 번만 (팝업이 위아래로 들썩이지 않게)
    assert 'hx-get="/update/toast?shown=1"' in toast and "update-toast downloading" in toast
    assert "update-toast downloading shown" in c.get("/update/toast?shown=1").text
    assert 'style="animation-delay:-' in toast  # 도는 아이콘이 매초 처음으로 돌아가지 않는다
    feed(updates, state="downloading", version="0.2.0")
    assert "indeterminate" in c.get("/update/toast").text
    feed(updates, state="ready", version="0.2.0")
    toast = c.get("/update/toast").text
    assert "업데이트 준비 완료" in toast and "disabled" not in toast
    c.post("/update/restart", headers=HTMX)
    assert events[-1]["action"] == "restart"
    feed(updates, state="error", version="0.2.0", error="서명이 맞지 않아요")
    assert "서명이 맞지 않아요" in c.get("/update/toast").text
    assert 'hx-post="/update/close"' in c.get("/update/toast").text
    c.post("/update/close", headers=HTMX)
    assert (
        updates.state.state == "available" and not updates.toast()
    )  # 닫으면 이번 실행에서는 숨긴다
    assert "<aside" not in c.get("/update/toast").text
    assert 'hx-get="/update/toast"' in c.get("/update/toast?shown=1").text  # 숨기면 다시 처음부터
    feed(updates, state="available", version="0.3.0")
    c.post("/update/skip", headers=HTMX)
    assert appsettings.load(tmp_path / "app-state.json").skipped_update == "0.3.0"


def test_no_toast_outside_app(tmp_path):
    wiki = make_wiki(tmp_path)
    c = TestClient(create_app(context(wiki, tmp_path)), follow_redirects=False)
    c.cookies.set(COOKIE, TOKEN)
    assert "update-toast" not in c.get("/today").text
    assert c.get("/update/toast").status_code == 404
    assert update_view(None, False, NOW) is None


def test_settings_update_section(flow, tmp_path):  # noqa: F811
    events: list[dict] = []
    updates = Updates(events.append, flow.settings_path, lambda: NOW)
    c = screen(flow, tmp_path, updates=updates)
    page = c.get("/settings").text
    assert "앱 업데이트" in page and __version__ in page and "업데이트 확인" in page
    c.post("/update/check", headers=HTMX)
    assert events == [{"event": "update", "action": "check"}]
    assert "확인하는 중" in c.get("/settings/update").text
    feed(updates, state="idle")
    section = c.get("/settings/update").text
    assert "최신 버전이에요" in section and "확인" in section
    updates.state = replace(updates.state, checked=NOW - timedelta(days=2))
    assert "11월 7일" in c.get("/settings/update").text
    feed(updates, state="available", version="0.2.0")
    section = c.get("/settings/update").text
    assert "새 버전이 있어요" in section and ">업데이트<" in section
    feed(updates, state="downloading", version="0.2.0", done=5_000_000, total=10_000_000)
    assert "50%" in c.get("/settings/update").text
    feed(updates, state="ready", version="0.2.0")
    assert "다시 시작" in c.get("/settings/update").text
    feed(updates, state="error", version="0.2.0", error="x")
    assert "다시 시도" in c.get("/settings/update").text
    feed(updates, state="error", error="offline")
    assert "확인하지 못했어요" in c.get("/settings/update").text
    feed(updates, state="off")
    section = c.get("/settings/update").text
    assert "받지 않아요" in section and "auto_update" not in section
    assert "저장했어요" in c.post("/update/auto", data={}, headers=HTMX).text
    assert not updates.auto()


def test_restart_waits_for_sync(made, tmp_path, setup, monkeypatch):  # noqa: F811
    updates, events, *_ = made
    scheduler, *_ = setup
    monkeypatch.setattr(Scheduler, "running", property(lambda self: True))
    c = app_client(tmp_path, updates, scheduler=scheduler)
    feed(updates, state="ready", version="0.2.0")
    toast = c.get("/update/toast").text
    assert "수집이 끝나면 다시 시작할 수 있어요" in toast and "/update/restart" not in toast
    c.post("/update/restart", headers=HTMX)
    assert events == []
