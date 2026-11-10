"""첫 실행 설정(`/setup`)과 설정(`/settings`) 화면 (plan 3.2 · 7장)."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from context_holder import agentaccounts, appsettings, claudesetup, codexsetup
from context_holder.classify import load_default_profile
from context_holder.gitsync import WikiRepo
from context_holder.llm import AGENT_TOOLS, AGENTS
from context_holder.onboarding import Onboarding
from context_holder.roles import catalog
from context_holder.setupflow import SetupFlow, model_downloaded
from context_holder.wiki import Wiki

HOURS = [f"{h:02d}" for h in range(24)]
MCP_HOSTS = {"claude": "Claude Code", "codex": "Codex"}  # 위키 검색을 붙이는 곳
INSTALL_PREVIEW = {
    "claude": " ".join(claudesetup.install_command()[2:]),
    "codex": " ".join(codexsetup.install_command()[2:]),
}
MINUTES = [f"{m:02d}" for m in range(0, 60, 10)]


def setup_view(
    flow: SetupFlow,
    journey: Onboarding,
    sync: dict[str, Any] | None = None,
    mcp_error: str = "",
) -> dict[str, Any]:
    """journey: 시작하기 단계별 완료. sync: 사이드바와 같은 수집 진행 (앱이 띄웠을 때만)."""
    flow.claude_settled()
    flow.codex_settled()
    settings = appsettings.load(flow.settings_path)
    agent = settings.agent
    status = flow.agent_status()
    # 다시 로그인하는 동안은 이전 토큰이 있어도 코드 입력 단계를 보인다
    github_done = flow.ready() and flow.github.state not in ("waiting", "connecting")
    return {
        "github": flow.github,
        "github_done": github_done,
        "token_source": flow.token_source(),
        "agent": agent,
        "agents": AGENTS,
        "agent_name": AGENTS[agent],
        "agent_tool": AGENT_TOOLS[agent],
        "mcp_host": MCP_HOSTS[agent],
        "install_preview": INSTALL_PREVIEW[agent],
        "status": status,
        "job": flow.codex_job if agent == "codex" else flow.claude_job,
        "mcp_ok": flow.agent_mcp_ok() if status.installed else False,
        "mcp_error": mcp_error,
        "translocated": claudesetup.translocated(),
        "settings": appsettings.load(flow.settings_path),
        "busy": flow.busy() or bool(sync and sync["running"]),
        "journey": journey,
        "current": next((step.key for step in journey.steps if not step.done), None),
        "sync": sync,
        "can_finish": github_done and status.logged_in and not status.outdated,
        "hours": HOURS,
        "minutes": MINUTES,
        "roles": roles_view(flow),
    }


def roles_view(flow: SetupFlow, error: str = "", saved: bool = False) -> dict[str, Any]:
    """위키에 고른 직군. 아직 고르지 않았으면 기본 직군에 체크해 둔다."""
    ready = flow.has_wiki()
    wiki = Wiki(flow.wiki_root)
    chosen = wiki.roles() if ready else ()
    picked = chosen or load_default_profile().roles
    return {
        "roles": [(role, role.id in picked) for role in catalog().roles],
        "ready": ready,
        "legacy": ready and not chosen and (wiki.root / "config/profile.yaml").exists(),
        "error": error,
        "saved": saved,
    }


def ensure_roles(flow: SetupFlow) -> None:
    """시작하기를 마칠 때 직군이 없으면(새 위키 · 예전 profile) 화면에 보인 기본 직군을 적는다."""
    wiki = Wiki(flow.wiki_root)
    if flow.has_wiki() and not wiki.roles():
        wiki.set_roles(load_default_profile().roles)


def settings_view(models_dir, model_id: str, flow: SetupFlow) -> dict[str, Any]:
    """바로 그릴 수 있는 것만. 연결 상태는 화면이 뜬 뒤 따로 채운다 (connections_view)."""
    return {
        "settings": appsettings.load(flow.settings_path),
        "agents": AGENTS,
        "agent": appsettings.load(flow.settings_path).agent,
        "model_ready": model_downloaded(models_dir, model_id),
        "hours": HOURS,
        "minutes": MINUTES,
        "roles": roles_view(flow),
    }


def connections_view(flow: SetupFlow) -> dict[str, Any]:
    flow.claude_settled()
    flow.codex_settled()
    repo = WikiRepo.open(flow.wiki_root)
    claude, codex = flow.claude(), flow.codex()
    registered = flow.registered() if claude.installed else None
    codex_registered = flow.codex_registered() if codex.installed else None
    jobs = (flow.claude_job.state, flow.codex_job.state)
    return {
        "agent": appsettings.load(flow.settings_path).agent,
        "agents": [agent_view(flow, "claude", claude), agent_view(flow, "codex", codex)],
        "token_source": flow.token_source(),
        "remote": repo.remote_url if repo else None,
        "translocated": claudesetup.translocated(),
        "polling": any(state in ("installing", "logging_in") for state in jobs),
        "mcp": [
            {
                "key": "claude",
                "name": "Claude Code",
                "tool": "Claude Code",
                "installed": claude.installed,
                "ok": registered == claudesetup.engine_command(),
                "registered": registered is not None,
                "post": "/settings/mcp",
            },
            {
                "key": "codex",
                "name": "Codex",
                "tool": "Codex CLI",
                "installed": codex.installed,
                "ok": codex_registered == codexsetup.engine_command(),
                "registered": codex_registered is not None,
                "post": "/settings/codex/mcp",
            },
        ],
    }


def agent_view(flow: SetupFlow, agent: str, active_status) -> dict[str, Any]:
    """에이전트 하나의 계정 카드들: 시스템 기본값, 그다음 이 앱에서 추가한 계정 (추가한 순서)."""
    job = flow.job(agent)
    active = flow.account(agent)
    pending = flow.logging_in(agent)
    tool = AGENT_TOOLS[agent]
    system = flow.system_status(agent) if active_status.installed else active_status
    cards = [
        {
            "id": agentaccounts.SYSTEM,
            "system": True,
            "email": system.account,
            "meta": (
                " · ".join(p for p in (f"터미널의 {tool} 로그인", system.org or system.detail) if p)
                if system.logged_in
                else f"터미널의 {tool}에 로그인돼 있지 않아요"
            ),
            "active": active == agentaccounts.SYSTEM,
            "logged_in": system.logged_in,
            "waiting": pending == agentaccounts.SYSTEM,
        }
    ]
    for account in flow.accounts(agent):
        is_active = account.id == active
        cards.append(
            {
                "id": account.id,
                "system": False,
                "email": account.email,
                "meta": " · ".join(
                    part
                    for part in (account.org or account.plan, agentaccounts.added_label(account))
                    if part
                ),
                "active": is_active,
                "logged_in": active_status.logged_in if is_active else bool(account.email),
                "waiting": pending == account.id,
            }
        )
    return {
        "key": agent,
        "name": AGENTS[agent],
        "tool": tool,
        "installed": active_status.installed and not active_status.outdated,
        "outdated": active_status.outdated,
        "installing": job.state == "installing",
        "busy": job.state in ("installing", "logging_in"),
        "error": job.message if job.state == "error" else "",
        "install_post": "/settings/codex/install" if agent == "codex" else None,
        "install_preview": INSTALL_PREVIEW[agent],
        "cards": cards,
    }


def add_routes(
    app: FastAPI,
    flow: SetupFlow,
    templates,
    page: Callable[..., HTMLResponse],
    model: Callable[[], tuple[Any, str]],  # (모델 폴더, 모델 id)
    download_model: Callable[[], None],
    on_settings: Callable[[appsettings.Settings], None],
    journey: Callable[[], Onboarding],
    sync: Callable[[], dict[str, Any] | None],
) -> None:
    def view(mcp_error: str = "") -> dict[str, Any]:
        return setup_view(flow, journey(), sync(), mcp_error)

    def setup_panel(request: Request, mcp_error: str = "") -> HTMLResponse:
        return templates.TemplateResponse(request, "_setup_panel.html", {"s": view(mcp_error)})

    @app.get("/setup", response_class=HTMLResponse)
    def setup(request: Request) -> HTMLResponse:
        return page(request, "setup.html", "setup", s=view())

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

    @app.post("/setup/agent", response_class=HTMLResponse)
    def setup_agent(request: Request, agent: str = Form(...)) -> Response:
        if agent not in AGENTS:
            return PlainTextResponse("잘못된 값", status_code=400)
        flow.choose_agent(agent)
        return setup_panel(request)

    @app.post("/setup/login/cancel", response_class=HTMLResponse)
    def setup_login_cancel(request: Request, agent: str = Form(...)) -> Response:
        if agent not in AGENTS:
            return PlainTextResponse("잘못된 값", status_code=400)
        flow.cancel_login(agent)
        return setup_panel(request)

    @app.post("/setup/codex/install", response_class=HTMLResponse)
    def setup_codex_install(request: Request) -> HTMLResponse:
        flow.install_codex()
        return setup_panel(request)

    @app.post("/setup/codex/login", response_class=HTMLResponse)
    def setup_codex_login(request: Request) -> HTMLResponse:
        flow.login_codex()
        return setup_panel(request)

    @app.post("/setup/mcp", response_class=HTMLResponse)
    def setup_mcp(request: Request) -> HTMLResponse:
        """고른 에이전트에 위키 검색을 붙인다."""
        ok, out = flow.register_codex_mcp() if flow.agent() == "codex" else flow.register_mcp()
        error = "" if ok else f"연결하지 못했어요: {out[:120] or '이유를 알 수 없어요'}"
        response = setup_panel(request, error)
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
        ensure_roles(flow)
        on_settings(flow.finish(autostart, daily_at, notify))
        return Response(headers={"HX-Redirect": "/today"})

    @app.post("/settings/roles", response_class=HTMLResponse)
    def save_roles(
        request: Request, roles: Annotated[list[str] | None, Form()] = None
    ) -> HTMLResponse:
        """수집할 직군. 위키에 적어 모든 기기가 같이 쓴다. 이미 모은 글은 그대로 둔다."""
        if not flow.has_wiki():
            return PlainTextResponse("위키를 먼저 연결해 주세요", status_code=400)
        try:
            Wiki(flow.wiki_root).set_roles(roles or [])
        except ValueError:
            view = roles_view(flow, error="직군을 하나 이상 골라 주세요")
        else:
            view = roles_view(flow, saved=True)
        return templates.TemplateResponse(request, "_roles_pick.html", {"r": view})

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request) -> HTMLResponse:
        models_dir, model_id = model()
        view = settings_view(models_dir, model_id, flow)
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

    def connections(request: Request, **extra: str) -> HTMLResponse:
        view = {**connections_view(flow), **extra}
        return templates.TemplateResponse(request, "_connections.html", {"c": view})

    @app.post("/settings/agent", response_class=HTMLResponse)
    def settings_agent(request: Request, agent: str = Form(...)) -> Response:
        """요약 · 분류 에이전트를 바꾸고 연결 영역도 다시 그리게 한다 (사용 중 표시)."""
        if agent not in AGENTS:
            return PlainTextResponse("잘못된 값", status_code=400)
        flow.choose_agent(agent)
        response = templates.TemplateResponse(
            request, "_agent_pick.html", {"agents": AGENTS, "agent": agent}
        )
        response.headers["HX-Trigger"] = "agent-changed"
        return response

    @app.post("/settings/account", response_class=HTMLResponse)
    def settings_account(
        request: Request, agent: str = Form(...), account: str = Form(...)
    ) -> Response:
        """카드를 눌러 활성 계정을 바꾼다."""
        if agent not in AGENTS:
            return PlainTextResponse("잘못된 값", status_code=400)
        try:
            flow.choose_account(agent, account)
        except ValueError:
            return PlainTextResponse("없는 계정", status_code=400)
        return connections(request)

    @app.post("/settings/account/add", response_class=HTMLResponse)
    def settings_account_add(request: Request, agent: str = Form(...)) -> Response:
        if agent not in AGENTS:
            return PlainTextResponse("잘못된 값", status_code=400)
        flow.add_account(agent)
        return connections(request)

    @app.post("/settings/account/login", response_class=HTMLResponse)
    def settings_account_login(
        request: Request, agent: str = Form(...), account: str = Form(...)
    ) -> Response:
        if agent not in AGENTS:
            return PlainTextResponse("잘못된 값", status_code=400)
        flow.relogin_account(agent, account)
        return connections(request)

    @app.post("/settings/account/remove", response_class=HTMLResponse)
    def settings_account_remove(
        request: Request, agent: str = Form(...), account: str = Form(...)
    ) -> Response:
        if agent not in AGENTS:
            return PlainTextResponse("잘못된 값", status_code=400)
        flow.remove_account(agent, account)
        return connections(request)

    @app.post("/settings/account/cancel", response_class=HTMLResponse)
    def settings_account_cancel(request: Request, agent: str = Form(...)) -> Response:
        if agent not in AGENTS:
            return PlainTextResponse("잘못된 값", status_code=400)
        flow.cancel_login(agent)
        return connections(request)

    @app.post("/settings/codex/install", response_class=HTMLResponse)
    def settings_codex_install(request: Request) -> HTMLResponse:
        flow.install_codex()
        return connections(request)

    @app.post("/settings/codex/mcp", response_class=HTMLResponse)
    def settings_codex_mcp(request: Request) -> HTMLResponse:
        ok, out = flow.register_codex_mcp()
        message = "연결됐어요" if ok else f"연결하지 못했어요: {out[:120]}"
        return connections(request, mcp_message_codex=message)

    @app.post("/settings/mcp", response_class=HTMLResponse)
    def settings_mcp(request: Request) -> HTMLResponse:
        """다시 등록하고 연결 영역을 통째로 다시 그린다 (상태 문구도 바뀌어야 한다)."""
        ok, out = flow.register_mcp()
        message = "연결됐어요" if ok else f"연결하지 못했어요: {out[:120]}"
        return connections(request, mcp_message_claude=message)

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
