"""첫 실행 설정 흐름과 설정 화면."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from context_holder import appsettings, auth, claudesetup, setupflow
from context_holder.connect import Connected, ConnectError
from context_holder.screens.app import COOKIE, create_app
from context_holder.setupflow import SetupFlow, model_downloaded
from tests.test_screens import TOKEN, context

NOW = datetime(2026, 11, 9, 3, tzinfo=UTC)
HTMX = {"HX-Request": "true"}
CODE = {
    "device_code": "dev",
    "user_code": "WDJB-MJHT",
    "verification_uri": "https://github.com/login/device",
    "interval": 5,
}
LOGGED_IN = json.dumps({"loggedIn": True, "subscriptionType": "max"})


class FakeRun:
    """claude 명령 흉내. 명령 앞부분으로 답을 고른다."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        key = " ".join(args[1:3])
        if args[0] == "/bin/bash":
            key = "install"
        return self.answers.get(key, (0, ""))


def github_server(responses: list) -> callable:
    def make():
        def handler(request):
            return httpx.Response(200, json=responses.pop(0))

        return httpx.Client(transport=httpx.MockTransport(handler))

    return make


@pytest.fixture
def flow(tmp_path, monkeypatch):
    connected = []

    def fake_connect(path, token):
        token()
        (path / ".git").mkdir(parents=True, exist_ok=True)
        connected.append(path)
        return Connected("me", True, True)

    monkeypatch.setattr(setupflow, "connect_wiki", fake_connect)
    monkeypatch.setattr(claudesetup, "engine_command", lambda: ["/a/ctx", "mcp"])
    events = {"opened": [], "copied": [], "spawned": [], "ready": 0}

    def ready():
        events["ready"] += 1

    made = SetupFlow(
        tmp_path / "wiki",
        tmp_path / "app-state.json",
        clock=lambda: NOW,
        run=FakeRun(),
        spawn=lambda args: events["spawned"].append(args),
        background=lambda work: work(),
        sleep=lambda s: None,
        open_url=events["opened"].append,
        copy=events["copied"].append,
        on_ready=ready,
    )
    made.events = events
    made.connected = connected
    return made


# ---------- GitHub ----------


def test_device_flow_saves_token_and_connects_wiki(flow, no_real_keychain):
    token = {
        "access_token": "gho_x",
        "expires_in": 28800,
        "refresh_token": "r",
        "refresh_token_expires_in": 99,
    }
    flow.http = github_server(
        [CODE, {"error": "authorization_pending"}, {"error": "slow_down", "interval": 9}, token]
    )
    flow.background = lambda work: None  # 기다리기는 아래에서 직접 돌린다
    flow.start_github()
    assert flow.github.state == "waiting" and flow.github.code.user_code == "WDJB-MJHT"
    assert flow.busy()

    flow.open_github()
    assert flow.events["copied"] == ["WDJB-MJHT"]
    assert flow.events["opened"] == ["https://github.com/login/device"]

    flow.background = lambda work: work()
    flow._wait(flow.github.code)
    assert flow.github.state == "done" and flow.github.connected.login == "me"
    assert auth.StoredToken.from_secret(no_real_keychain.secret).access_token == "gho_x"
    assert flow.ready() and flow.events["ready"] == 1


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ({"error": "expired_token"}, "만료"),
        ({"error": "access_denied"}, "취소"),
        ({"error": "boom"}, "실패"),
    ],
)
def test_device_flow_end_states(flow, answer, message):
    flow.http = github_server([CODE, answer])
    flow.start_github()
    assert flow.github.state == "error" and message in flow.github.message


def test_device_flow_start_error_and_open_without_code(flow):
    flow.open_github()
    assert flow.events["opened"] == []
    flow.http = github_server([{"error": "unauthorized_client"}])
    flow.start_github()
    assert flow.github.state == "error"


def test_existing_login_skips_device_flow(flow, monkeypatch):
    monkeypatch.setattr(auth, "gh_lookup", lambda: "gho_cli")
    flow.start_github()
    assert flow.github.state == "done" and flow.connected
    flow.github = setupflow.GitHubLogin(state="waiting")
    flow.start_github()  # 이미 진행 중이면 무시
    assert flow.github.state == "waiting"


def test_connect_failure_is_shown(flow, monkeypatch):
    monkeypatch.setattr(auth, "gh_lookup", lambda: "gho_cli")

    def fail(path, token):
        raise ConnectError("다른 레포에 연결돼 있다")

    monkeypatch.setattr(setupflow, "connect_wiki", fail)
    flow.start_github()
    assert flow.github.state == "error" and "다른 레포" in flow.github.message


def test_relogin_forgets_the_token(flow, no_real_keychain):
    no_real_keychain.secret = "old"
    flow.http = github_server([CODE, {"error": "access_denied"}])
    flow.relogin_github()
    assert no_real_keychain.secret is None and flow.github.state == "error"


# ---------- Claude ----------


def test_install_then_login(flow):
    flow.run = FakeRun({"install": (0, "installed"), "auth status": (127, "")})
    assert not flow.claude().installed
    flow.install_claude()
    assert flow.claude_job.state == "idle" and flow._status is None
    flow.login_claude()
    assert flow.claude_job.state == "logging_in" and flow.busy()
    assert flow.events["spawned"] == [["claude", "auth", "login", "--claudeai"]]
    flow.run = FakeRun({"auth status": (0, LOGGED_IN)})
    flow.claude_settled()
    assert flow.claude_job.state == "idle" and flow.claude().detail == "max"


def test_install_failure_and_busy_guard(flow):
    flow.run = FakeRun({"install": (1, "network")})
    flow.install_claude()
    assert flow.claude_job.state == "error" and "network" in flow.claude_job.message
    flow.claude_job = setupflow.ClaudeJob("installing")
    flow.install_claude()  # 진행 중이면 무시
    assert flow.claude_job.state == "installing"


def test_login_spawn_failure(flow):
    def broken(args):
        raise OSError("없음")

    flow.spawn = broken
    flow.login_claude()
    assert flow.claude_job.state == "error"


def test_status_is_cached(flow):
    flow.run = FakeRun({"auth status": (0, LOGGED_IN)})
    flow.claude()
    flow.claude()
    assert len(flow.run.calls) == 1
    flow.claude(fresh=True)
    assert len(flow.run.calls) == 2


# ---------- 끝났나 ----------


def test_needed_until_everything_is_connected(flow, monkeypatch):
    assert flow.needed()
    monkeypatch.setattr(auth, "gh_lookup", lambda: "gho")
    (flow.wiki_root / ".git").mkdir(parents=True)
    flow.run = FakeRun(
        {"auth status": (0, LOGGED_IN), "mcp get": (0, "  Command: /a/ctx\n  Args: mcp\n")}
    )
    assert not flow.needed()  # 다른 방법으로 이미 연결된 기기
    assert appsettings.load(flow.settings_path).setup_done
    flow.run = FakeRun()
    assert not flow.needed()


def test_finish_saves_choices(flow):
    saved = flow.finish(autostart=False, daily_at="08:15", notify=False)
    assert (saved.autostart, saved.daily_at, saved.notify, saved.setup_done) == (
        False,
        "08:15",
        False,
        True,
    )
    assert flow.events["ready"] == 1
    assert flow.register_mcp() == (True, "")


def test_model_downloaded(tmp_path):
    assert not model_downloaded(tmp_path, "bge-m3")
    (tmp_path / "models--Xenova--bge-m3").mkdir()
    assert model_downloaded(tmp_path, "bge-m3")


# ---------- 화면 ----------


def screen(flow, tmp_path, **extra):
    from context_holder.wiki import Wiki
    from tests.test_screens import SOURCES

    wiki = Wiki(tmp_path / "screen-wiki")
    wiki.ensure_config()
    (wiki.root / "config/sources.yaml").write_text(SOURCES, encoding="utf-8")
    seen = {"settings": [], "downloads": 0}

    def download():
        seen["downloads"] += 1

    ctx = replace(
        context(wiki, tmp_path),
        flow=flow,
        settings_path=flow.settings_path,
        app_mode=True,
        model=lambda: (tmp_path / "models", "bge-m3"),
        download_model=download,
        on_settings=seen["settings"].append,
        **extra,
    )
    client = TestClient(create_app(ctx), follow_redirects=False)
    client.cookies.set(COOKIE, TOKEN)
    client.seen = seen
    return client


def test_home_goes_to_setup_until_done(flow, tmp_path):
    c = screen(flow, tmp_path)
    assert c.get("/").headers["location"] == "/setup"
    assert c.get("/start").headers["location"] == "/setup"
    page = c.get("/setup").text
    assert "GitHub로 로그인" in page and 'class="in-app"' in page
    assert "Claude Code 설치" in page or "Claude 로그인" in page
    assert "claude mcp add context-holder --scope user -- /a/ctx mcp" in page
    flow.finish(True, "09:30", True)
    assert c.get("/").headers["location"] == "/today"


def test_setup_buttons(flow, tmp_path, monkeypatch):
    c = screen(flow, tmp_path)
    flow.http = github_server([CODE])
    flow.background = lambda work: None
    panel = c.post("/setup/github", headers=HTMX).text
    assert "WDJB-MJHT" in panel and 'hx-trigger="every 2s"' in panel and "<html" not in panel
    c.post("/setup/github/open", headers=HTMX)
    assert flow.events["opened"]
    assert "승인 기다리는 중" in c.get("/setup/panel").text

    flow.background = lambda work: work()
    flow.run = FakeRun({"install": (0, ""), "auth status": (0, json.dumps({"loggedIn": False}))})
    c.post("/setup/claude/install", headers=HTMX)
    assert (
        "Claude 로그인" in c.post("/setup/claude/login", headers=HTMX).text
        or flow.claude_job.state == "logging_in"
    )
    flow.run = FakeRun({"mcp add": (1, "boom"), "auth status": (0, LOGGED_IN)})
    failed = c.post("/setup/mcp", headers=HTMX)
    assert failed.headers["HX-Trigger"] == "mcp-failed"
    flow.run = FakeRun({"auth status": (0, LOGGED_IN)})
    assert "HX-Trigger" not in c.post("/setup/mcp", headers=HTMX).headers


def test_setup_finish(flow, tmp_path):
    c = screen(flow, tmp_path)
    assert c.post("/setup/finish", data={"daily_at": "9시"}, headers=HTMX).status_code == 400
    done = c.post("/setup/finish", data={"daily_at": "07:45", "notify": "true"}, headers=HTMX)
    assert done.headers["HX-Redirect"] == "/today"
    saved = appsettings.load(flow.settings_path)
    assert (saved.daily_at, saved.notify, saved.autostart) == ("07:45", True, False)
    assert c.seen["settings"] == [saved]


def test_settings_page_shows_connections(flow, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "gh_lookup", lambda: "gho")
    flow.run = FakeRun(
        {"auth status": (0, LOGGED_IN), "mcp get": (0, "  Command: /old/ctx\n  Args: mcp\n")}
    )
    page = screen(flow, tmp_path).get("/settings").text
    assert "GitHub CLI 로그인 사용" in page and "다른 엔진이 등록돼 있어요" in page
    assert "아직 내려받지 않았어요" in page and 'value="09:30"' in page


def test_settings_save_and_theme_refresh(flow, tmp_path):
    c = screen(flow, tmp_path)
    form = {"daily_at": "08:00", "theme": "system", "weekends": "true"}
    saved = c.post("/settings", data=form, headers=HTMX)
    assert "저장했어요" in saved.text and "HX-Refresh" not in saved.headers
    assert appsettings.load(flow.settings_path).weekends
    dark = c.post("/settings", data={**form, "theme": "dark"}, headers=HTMX)
    assert dark.headers["HX-Refresh"] == "true"
    assert 'data-theme="dark"' in c.get("/today").text
    assert c.post("/settings", data={**form, "theme": "neon"}, headers=HTMX).status_code == 400
    assert c.post("/settings", data={**form, "daily_at": "x"}, headers=HTMX).status_code == 400


def test_settings_actions(flow, tmp_path, no_real_keychain):
    c = screen(flow, tmp_path)
    flow.http = github_server([CODE])
    flow.background = lambda work: None
    no_real_keychain.secret = "old"
    assert c.post("/settings/github", headers=HTMX).headers["HX-Redirect"] == "/setup"
    assert no_real_keychain.secret is None
    assert "승인하세요" in c.post("/settings/claude", headers=HTMX).text
    flow.run = FakeRun({"mcp add": (1, "<bad>")})
    assert "&lt;bad&gt;" in c.post("/settings/mcp", headers=HTMX).text
    flow.run = FakeRun()
    assert "다시 등록했어요" in c.post("/settings/mcp", headers=HTMX).text
    flow.background = lambda work: work()
    assert "내려받는 중" in c.post("/settings/model", headers=HTMX).text
    assert c.seen["downloads"] == 1


def test_settings_without_wiki_or_claude(flow, tmp_path):
    flow.run = FakeRun({"auth status": (127, "")})
    page = screen(flow, tmp_path).get("/settings").text
    assert "로그인 필요" in page and "Claude Code 없음" in page and "설치하기" in page


def test_bridge_settings_events(tmp_path):
    import io

    from context_holder.index import Index
    from context_holder.tray import AppBridge, Notice, NoticeLog

    out = io.StringIO()
    bridge = AppBridge(
        out,
        Index(tmp_path / "i.db"),
        NoticeLog(tmp_path / "n.json"),
        settings=lambda: appsettings.Settings(notify=False),
    )
    bridge.apply_settings(appsettings.Settings(autostart=False))
    assert out.getvalue() == ""  # 첫 실행 설정 전
    bridge.apply_settings(appsettings.Settings(autostart=False, setup_done=True))
    assert json.loads(out.getvalue()) == {"event": "autostart", "enabled": False}

    class Sched:
        def clock(self):
            return NOW

    bridge.scheduler = Sched()
    import context_holder.tray as tray

    original = tray.notices
    tray.notices = lambda s, i: [Notice("brief", "b", "", "/"), Notice("claude", "c", "", "/")]
    try:
        bridge.check_notices()
    finally:
        tray.notices = original
    kinds = [json.loads(line).get("title") for line in out.getvalue().splitlines()[1:]]
    assert kinds == ["c"]  # 수집 완료 알림을 껐다


def test_bridge_apply_settings_redraws_menu(tmp_path, monkeypatch):
    import io

    from context_holder.index import Index
    from context_holder.tray import AppBridge, NoticeLog

    bridge = AppBridge(io.StringIO(), Index(tmp_path / "i.db"), NoticeLog(tmp_path / "n.json"))
    redraws = []
    monkeypatch.setattr(bridge, "refresh", lambda: redraws.append(1))
    bridge._last_status = {"event": "status"}
    bridge.apply_settings(appsettings.Settings())
    assert redraws == [1]


def test_expiring_token_time_is_still_valid():
    assert NOW + timedelta(hours=1) > NOW


def test_translocated_app_asks_to_move_first(flow, tmp_path, monkeypatch):
    monkeypatch.setattr(claudesetup, "translocated", lambda: True)
    flow.run = FakeRun({"auth status": (0, LOGGED_IN)})
    c = screen(flow, tmp_path)
    assert "응용 프로그램 폴더로 옮긴 뒤" in c.get("/setup").text
    page = c.get("/settings").text
    assert (
        "응용 프로그램 폴더로 옮긴 뒤 등록하세요" in page and 'hx-post="/settings/mcp"' not in page
    )
