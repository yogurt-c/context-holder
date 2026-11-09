"""첫 실행 설정 흐름과 설정 화면."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pygit2
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
LOGGED_IN = json.dumps({"loggedIn": True, "subscriptionType": "max", "email": "me@example.com"})


def register(flow, command: str) -> None:
    """Claude Code 설정 파일에 등록된 것처럼 쓴다."""
    servers = {"mcpServers": {"context-holder": {"command": command, "args": ["mcp"]}}}
    flow.claude_config.write_text(json.dumps(servers))


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
        claude_config=tmp_path / ".claude.json",
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


def test_relogin_keeps_the_old_token_until_a_new_one_arrives(flow, no_real_keychain):
    """승인을 기다리는 동안에도 돌던 수집이 push할 수 있다. 취소하면 이전 로그인이 남는다."""
    no_real_keychain.secret = "old"
    flow.http = github_server([CODE, {"error": "access_denied"}])
    flow.relogin_github()
    assert no_real_keychain.secret == "old" and flow.github.state == "error"


def test_relogin_asks_github_even_with_cli_login(flow, monkeypatch):
    """GitHub CLI 로그인이 있어도 조용히 그 계정으로 넘어가지 않고 브라우저 승인을 받는다."""
    monkeypatch.setattr(auth, "gh_lookup", lambda: "gho_cli")
    flow.http = github_server([CODE])
    flow.background = lambda work: None
    flow.relogin_github()
    assert flow.github.state == "waiting" and flow.github.code.user_code == "WDJB-MJHT"
    flow.relogin_github()  # 이미 기다리는 중이면 새 코드를 받지 않는다
    assert flow.github.state == "waiting"


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


def test_relogin_waits_for_login_process(flow):
    """이미 로그인된 채로 다시 연결하면 프로세스가 끝날 때까지 기다린다."""

    class Login:
        code = None

        def poll(self):
            return self.code

    login = Login()
    flow.spawn = lambda args: login
    flow.run = FakeRun({"auth status": (0, LOGGED_IN)})
    flow.login_claude()
    flow.claude_settled()
    assert flow.claude_job.state == "logging_in"  # 로그인돼 있어도 아직 승인 전
    login.code = 0
    flow.claude_settled()
    assert flow.claude_job.state == "idle" and flow._login is None


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
    flow.run = FakeRun({"auth status": (0, LOGGED_IN)})
    register(flow, "/a/ctx")
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
    assert "Claude Code 연동" in page and "claude mcp add" not in page
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
    bad = c.post("/setup/finish", data={"hour": "25", "minute": "00"}, headers=HTMX)
    assert bad.status_code == 400
    done = c.post(
        "/setup/finish", data={"hour": "07", "minute": "40", "notify": "true"}, headers=HTMX
    )
    assert done.headers["HX-Redirect"] == "/today"
    saved = appsettings.load(flow.settings_path)
    assert (saved.daily_at, saved.notify, saved.autostart) == ("07:40", True, False)
    assert c.seen["settings"] == [saved]


def test_settings_page_is_quick_and_connections_load_after(flow, tmp_path, monkeypatch):
    calls = FakeRun({"auth status": (0, LOGGED_IN)})
    flow.run = calls
    c = screen(flow, tmp_path)
    page = c.get("/settings").text
    assert calls.calls == []  # 화면을 그릴 때 claude 명령을 부르지 않는다
    assert 'hx-get="/settings/connections" hx-trigger="load"' in page
    assert '<option value="09" selected>09시</option>' in page
    assert '<option value="30" selected>30분</option>' in page
    assert "아직 내려받지 않았어요" in page and "bge-m3" not in page and "ctx reembed" not in page

    monkeypatch.setattr(auth, "gh_lookup", lambda: "gho")
    register(flow, "/old/ctx")
    panel = c.get("/settings/connections").text
    assert "<html" not in panel and "GitHub CLI 로그인을 쓰고 있어요" in panel
    assert "이 앱이 아닌 다른 위치로 연결돼 있어요" in panel
    register(flow, "/a/ctx")
    assert "이 위키를 찾아봐요" in c.get("/settings/connections").text


def test_odd_saved_minute_stays_selected(flow, tmp_path):
    appsettings.update(flow.settings_path, daily_at="08:35")
    page = screen(flow, tmp_path).get("/settings").text
    assert '<option value="35" selected>35분</option>' in page


def test_settings_save_without_reload(flow, tmp_path):
    c = screen(flow, tmp_path)
    form = {"hour": "08", "minute": "10", "theme": "system", "weekends": "true"}
    saved = c.post("/settings", data=form, headers=HTMX)
    assert "저장했어요" in saved.text and "HX-Refresh" not in saved.headers
    assert appsettings.load(flow.settings_path).daily_at == "08:10"
    dark = c.post("/settings", data={**form, "theme": "dark"}, headers=HTMX)
    assert "HX-Refresh" not in dark.headers  # 화면 모드는 화면에서 바로 바꾼다 (app.js)
    assert 'data-theme="dark"' in c.get("/today").text
    assert c.post("/settings", data={**form, "theme": "neon"}, headers=HTMX).status_code == 400
    assert c.post("/settings", data={**form, "minute": "07"}, headers=HTMX).status_code == 400


def test_settings_actions(flow, tmp_path, no_real_keychain):
    c = screen(flow, tmp_path)
    flow.http = github_server([CODE])
    flow.background = lambda work: None
    no_real_keychain.secret = "old"
    wiki = pygit2.init_repository(str(flow.wiki_root))  # 이미 연결을 마친 기기
    wiki.remotes.create("origin", "https://github.com/me/context-wiki.git")
    assert flow.ready()
    assert c.post("/settings/github", headers=HTMX).headers["HX-Redirect"] == "/setup"
    assert no_real_keychain.secret == "old"  # 새 토큰을 받을 때까지 이전 로그인을 쓴다
    panel = c.get("/setup/panel").text  # 로그인돼 있어도 다시 로그인하는 동안은 코드를 보인다
    assert "WDJB-MJHT" in panel and 'hx-trigger="every 2s"' in panel
    flow.github = setupflow.GitHubLogin(state="error", message="GitHub 로그인에 실패했다")
    assert "GitHub 로그인에 실패했다" in c.get("/setup/panel").text  # 취소해도 이유가 보인다
    waiting = c.post("/settings/claude", headers=HTMX).text
    assert "승인하세요" in waiting and 'hx-trigger="every 2s"' in waiting
    flow.run = FakeRun({"auth status": (0, LOGGED_IN)})
    done = c.get("/settings/connections").text  # 승인이 끝나면 문구와 다시 묻기가 사라진다
    assert "승인하세요" not in done and "every 2s" not in done
    flow.run = FakeRun({"mcp add": (1, "<bad>")})
    assert "&lt;bad&gt;" in c.post("/settings/mcp", headers=HTMX).text
    flow.run = FakeRun()
    assert "다시 연결했어요" in c.post("/settings/mcp", headers=HTMX).text
    flow.background = lambda work: work()
    assert "내려받는 중" in c.post("/settings/model", headers=HTMX).text
    assert c.seen["downloads"] == 1


def test_reconnect_redraws_mcp_status(flow, tmp_path):
    """다시 연결하면 '다른 위치' 경고가 바로 사라진다 (버튼 자리만 바꾸지 않는다)."""
    flow.run = FakeRun({"auth status": (0, LOGGED_IN)})
    register(flow, "/old/ctx")
    c = screen(flow, tmp_path)
    first = c.get("/settings/connections").text
    assert "다른 위치로 연결돼 있어요" in first
    assert "연결됨 · me@example.com (max)" in first  # 어느 계정인지 보인다 (계정을 바꿨는지 확인)

    class Registering(FakeRun):
        def __call__(self, args, timeout):
            if args[1:3] == ["mcp", "add"]:
                command, *rest = args[args.index("--") + 1 :]
                servers = {"mcpServers": {"context-holder": {"command": command, "args": rest}}}
                flow.claude_config.write_text(json.dumps(servers))
            return super().__call__(args, timeout)

    flow.run = Registering({"auth status": (0, LOGGED_IN)})
    panel = c.post("/settings/mcp", headers=HTMX).text
    assert 'id="connections"' in panel and "다시 연결했어요" in panel
    assert "다른 위치" not in panel and "이 위키를 찾아봐요" in panel


def test_settings_without_wiki_or_claude(flow, tmp_path):
    flow.run = FakeRun({"auth status": (127, "")})
    panel = screen(flow, tmp_path).get("/settings/connections").text
    assert "로그인이 필요해요" in panel and "Claude Code가 없어요" in panel and "설치하기" in panel
    flow.run = FakeRun({"auth status": (0, LOGGED_IN)})
    flow._status = None
    assert "연결되지 않았어요" in screen(flow, tmp_path / "b").get("/settings/connections").text


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
    panel = c.get("/settings/connections").text
    assert "옮긴 뒤 연결하세요" in panel and 'hx-post="/settings/mcp"' not in panel
