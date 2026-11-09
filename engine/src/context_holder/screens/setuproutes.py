"""첫 실행 설정(`/setup`)과 설정(`/settings`) 화면 (plan 3.2 · 7장)."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from context_holder import appsettings, claudesetup
from context_holder.gitsync import WikiRepo
from context_holder.setupflow import SetupFlow, model_downloaded

TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


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
        "mcp_preview": claudesetup.mcp_preview(),
        "settings": appsettings.load(flow.settings_path),
        "busy": flow.busy(),
        "can_finish": github_done and claude.logged_in,
    }


def settings_view(flow: SetupFlow, models_dir, model_id: str) -> dict[str, Any]:
    repo = WikiRepo.open(flow.wiki_root)
    claude = flow.claude()
    registered = claudesetup.registered_command(flow.run) if claude.installed else None
    return {
        "settings": appsettings.load(flow.settings_path),
        "token_source": flow.token_source(),
        "remote": repo.remote_url if repo else None,
        "claude": claude,
        "registered": " ".join(registered) if registered else None,
        "mcp_ok": registered == claudesetup.engine_command(),
        "engine_command": " ".join(claudesetup.engine_command()),
        "model_id": model_id,
        "model_ready": model_downloaded(models_dir, model_id),
        "busy": flow.busy(),
        "claude_job": flow.claude_job,
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
        daily_at: str = Form("09:30"),
    ) -> Response:
        if not _valid_time(daily_at):
            return PlainTextResponse("시각은 HH:MM으로 넣어요", status_code=400)
        on_settings(flow.finish(autostart, daily_at, notify))
        return Response(headers={"HX-Redirect": "/today"})

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request) -> HTMLResponse:
        models_dir, model_id = model()
        return page(
            request, "settings.html", "settings", v=settings_view(flow, models_dir, model_id)
        )

    @app.post("/settings", response_class=HTMLResponse)
    def save_settings(
        daily_at: str = Form(...),
        theme: str = Form("system"),
        weekends: bool = Form(False),
        notify: bool = Form(False),
        autostart: bool = Form(False),
    ) -> Response:
        if not _valid_time(daily_at) or theme not in appsettings.THEMES:
            return PlainTextResponse("잘못된 값", status_code=400)
        before = appsettings.load(flow.settings_path)
        saved = appsettings.update(
            flow.settings_path,
            daily_at=daily_at,
            theme=theme,
            weekends=weekends,
            notify=notify,
            autostart=autostart,
        )
        on_settings(saved)
        response = HTMLResponse('<span class="done-label">저장했어요</span>')
        if saved.theme != before.theme:
            response.headers["HX-Refresh"] = "true"  # 화면 모드는 새로 그려야 바뀐다
        return response

    @app.post("/settings/github")
    def settings_github() -> Response:
        flow.relogin_github()
        return Response(headers={"HX-Redirect": "/setup"})

    @app.post("/settings/claude", response_class=HTMLResponse)
    def settings_claude() -> HTMLResponse:
        flow.login_claude()
        return HTMLResponse('<span class="done-label">브라우저에서 승인하세요</span>')

    @app.post("/settings/mcp", response_class=HTMLResponse)
    def settings_mcp() -> HTMLResponse:
        ok, out = flow.register_mcp()
        text = "다시 등록했어요" if ok else f"등록하지 못했어요: {out[:120]}"
        return HTMLResponse(f'<span class="done-label">{_escape(text)}</span>')

    @app.post("/settings/model", response_class=HTMLResponse)
    def settings_model() -> HTMLResponse:
        flow.background(download_model)
        return HTMLResponse('<span class="done-label">내려받는 중이에요. 몇 분 걸려요</span>')

    @app.get("/start")
    def start() -> RedirectResponse:
        return RedirectResponse("/setup" if flow.needed() else "/today", status_code=303)


def _valid_time(value: str) -> bool:
    import re

    return re.match(TIME_PATTERN, value) is not None


def _escape(text: str) -> str:
    import html

    return html.escape(text)
