"""첫 실행 설정(`/setup`)과 설정(`/settings`) 화면 (plan 3.2 · 7장)."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from context_holder import appsettings, claudesetup
from context_holder.gitsync import WikiRepo
from context_holder.setupflow import SetupFlow, model_downloaded

HOURS = [f"{h:02d}" for h in range(24)]
MINUTES = [f"{m:02d}" for m in range(0, 60, 10)]


def setup_view(flow: SetupFlow) -> dict[str, Any]:
    flow.claude_settled()
    claude = flow.claude()
    github_done = flow.ready()
    return {
        "github": flow.github,
        "github_done": github_done,
        "token_source": flow.token_source(),
        "claude": claude,
        "claude_job": flow.claude_job,
        "mcp_ok": flow.mcp_ok() if claude.installed else False,
        "translocated": claudesetup.translocated(),
        "settings": appsettings.load(flow.settings_path),
        "busy": flow.busy(),
        "can_finish": github_done and claude.logged_in,
        "hours": HOURS,
        "minutes": MINUTES,
    }


def settings_view(models_dir, model_id: str, settings_path) -> dict[str, Any]:
    """바로 그릴 수 있는 것만. 연결 상태는 화면이 뜬 뒤 따로 채운다 (connections_view)."""
    return {
        "settings": appsettings.load(settings_path),
        "model_ready": model_downloaded(models_dir, model_id),
        "hours": HOURS,
        "minutes": MINUTES,
    }


def connections_view(flow: SetupFlow) -> dict[str, Any]:
    repo = WikiRepo.open(flow.wiki_root)
    claude = flow.claude()
    registered = flow.registered() if claude.installed else None
    return {
        "token_source": flow.token_source(),
        "remote": repo.remote_url if repo else None,
        "claude": claude,
        "registered": registered is not None,
        "mcp_ok": registered == claudesetup.engine_command(),
        "translocated": claudesetup.translocated(),
    }


def add_routes(
    app: FastAPI,
    flow: SetupFlow,
    templates,
    page: Callable[..., HTMLResponse],
    model: Callable[[], tuple[Any, str]],  # (모델 폴더, 모델 id)
    download_model: Callable[[], None],
    on_settings: Callable[[appsettings.Settings], None],
) -> None:
    def setup_panel(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "_setup_panel.html", {"s": setup_view(flow)})

    @app.get("/setup", response_class=HTMLResponse)
    def setup(request: Request) -> HTMLResponse:
        return page(request, "setup.html", "setup", s=setup_view(flow))

    @app.get("/setup/panel", response_class=HTMLResponse)
    def setup_poll(request: Request) -> HTMLResponse:
        return setup_panel(request)

    @app.post("/setup/github", response_class=HTMLResponse)
    def setup_github(request: Request) -> HTMLResponse:
        flow.start_github()
        return setup_panel(request)

    @app.post("/setup/github/open", response_class=HTMLResponse)
    def setup_github_open(request: Request) -> HTMLResponse:
        flow.open_github()
        return setup_panel(request)

    @app.post("/setup/claude/install", response_class=HTMLResponse)
    def setup_claude_install(request: Request) -> HTMLResponse:
        flow.install_claude()
        return setup_panel(request)

    @app.post("/setup/claude/login", response_class=HTMLResponse)
    def setup_claude_login(request: Request) -> HTMLResponse:
        flow.login_claude()
        return setup_panel(request)

    @app.post("/setup/mcp", response_class=HTMLResponse)
    def setup_mcp(request: Request) -> HTMLResponse:
        ok, _ = flow.register_mcp()
        response = setup_panel(request)
        if not ok:
            response.headers["HX-Trigger"] = "mcp-failed"
        return response

    @app.post("/setup/finish")
    def setup_finish(
        autostart: bool = Form(False),
        notify: bool = Form(False),
        hour: str = Form("09"),
        minute: str = Form("30"),
    ) -> Response:
        daily_at = _time(hour, minute)
        if daily_at is None:
            return PlainTextResponse("시각을 다시 골라 주세요", status_code=400)
        on_settings(flow.finish(autostart, daily_at, notify))
        return Response(headers={"HX-Redirect": "/today"})

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request) -> HTMLResponse:
        models_dir, model_id = model()
        view = settings_view(models_dir, model_id, flow.settings_path)
        return page(request, "settings.html", "settings", v=view)

    @app.get("/settings/connections", response_class=HTMLResponse)
    def settings_connections(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "_connections.html", {"c": connections_view(flow)}
        )

    @app.post("/settings", response_class=HTMLResponse)
    def save_settings(
        hour: str = Form(...),
        minute: str = Form(...),
        theme: str = Form("system"),
        weekends: bool = Form(False),
        notify: bool = Form(False),
        autostart: bool = Form(False),
    ) -> Response:
        daily_at = _time(hour, minute)
        if daily_at is None or theme not in appsettings.THEMES:
            return PlainTextResponse("잘못된 값", status_code=400)
        saved = appsettings.update(
            flow.settings_path,
            daily_at=daily_at,
            theme=theme,
            weekends=weekends,
            notify=notify,
            autostart=autostart,
        )
        on_settings(saved)
        return HTMLResponse('<span class="done-label">저장했어요</span>')

    @app.post("/settings/github")
    def settings_github() -> Response:
        flow.relogin_github()
        return Response(headers={"HX-Redirect": "/setup"})

    @app.post("/settings/claude", response_class=HTMLResponse)
    def settings_claude() -> HTMLResponse:
        flow.login_claude()
        return HTMLResponse('<span class="done-label">브라우저에서 승인하세요</span>')

    @app.post("/settings/mcp", response_class=HTMLResponse)
    def settings_mcp(request: Request) -> HTMLResponse:
        """다시 등록하고 연결 영역을 통째로 다시 그린다 (상태 문구도 바뀌어야 한다)."""
        ok, out = flow.register_mcp()
        view = connections_view(flow)
        view["mcp_message"] = "다시 연결했어요" if ok else f"연결하지 못했어요: {out[:120]}"
        return templates.TemplateResponse(request, "_connections.html", {"c": view})

    @app.post("/settings/model", response_class=HTMLResponse)
    def settings_model() -> HTMLResponse:
        flow.background(download_model)
        return HTMLResponse('<span class="done-label">내려받는 중이에요. 몇 분 걸려요</span>')

    @app.get("/start")
    def start() -> RedirectResponse:
        return RedirectResponse("/setup" if flow.needed() else "/today", status_code=303)


def _time(hour: str, minute: str) -> str | None:
    """고른 시 · 분. 목록에 없는 값이면 None."""
    return f"{hour}:{minute}" if hour in HOURS and minute in MINUTES else None
