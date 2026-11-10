"""엔진 화면 서버 (plan 7장). Tauri 창이 `http://127.0.0.1:<port>`를 띄운다.

보호:
- 시작할 때 만든 토큰을 앱에만 준다. 첫 요청 `/?token=…`에서 HttpOnly 쿠키로 바꾸고 주소에서 지운다.
- Host 헤더가 서버 주소가 아니면 거절한다 (DNS rebinding).
- 쓰기 요청은 HTMX가 붙이는 HX-Request 헤더도 요구한다. 쿠키는 SameSite=Strict다.
"""

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from context_holder import appsettings, onboarding
from context_holder.embed import Embedder
from context_holder.feedback import Kind, record_feedback
from context_holder.index import Index
from context_holder.llm import ClaudeCli
from context_holder.mcptools import WikiTools
from context_holder.screens import (
    articleview,
    decisionsview,
    inboxview,
    searchview,
    setuproutes,
    sourcesview,
    updateroutes,
    views,
)
from context_holder.screens.runs import alerts
from context_holder.wiki import Wiki

COOKIE = "ch_token"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)
PAGES = {  # 앱 밖(브라우저)에서만 보이는 자리
    "settings": ("설정", "설정은 앱에서 열 수 있어요."),
}
FEEDBACK_DONE = {Kind.BOOKMARK: "북마크했어요", Kind.NOT_RELEVANT: "관련없음으로 남겼어요"}


@dataclass
class ScreenContext:
    wiki: Wiki
    index: Index
    device: str
    mcp_log: Path
    clock: Callable[[], datetime]
    token: str
    hosts: frozenset[str]  # 허용하는 Host 헤더 (127.0.0.1:<port>)
    embedder: Callable[[], Embedder | None] = field(default=lambda: None)  # 첫 검색 때 불러온다
    scheduler: Any = None  # 앱이 띄웠을 때만 (scheduler.Scheduler): 지금 수집 · 진행률
    flow: Any = None  # setupflow.SetupFlow: 첫 실행 설정 · 연결 상태
    settings_path: Path | None = None  # app-state.json (화면 모드 등)
    app_mode: bool = False  # 앱 창 안: 사이드바 위 여백을 줄인다
    model: Callable[[], tuple[Path, str]] = field(default=lambda: (Path("models"), "bge-m3"))
    download_model: Callable[[], None] = field(default=lambda: None)
    on_settings: Callable[[Any], None] = field(default=lambda settings: None)
    updates: Any = None  # updates.Updates: 앱이 띄웠을 때만
    regroup_llm: Callable[[], Any] = field(default=lambda: ClaudeCli())  # 주제 정리 (버튼)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def create_app(ctx: ScreenContext) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(
        directory=str(resources.files("context_holder.screens").joinpath("templates"))
    )
    templates.env.globals.update(
        short_day=views.short_day,
        long_day=views.long_day,
        kinds=searchview.KINDS,
        periods=searchview.PERIODS,
    )
    static = resources.files("context_holder.screens").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static)), name="static")
    app.add_middleware(BaseHTTPMiddleware, dispatch=_guard(ctx))

    tools = WikiTools(ctx.index, ctx.wiki, ctx.embedder, ctx.device, ctx.mcp_log, ctx.clock)
    search_lock = threading.Lock()  # 검색 모델과 인덱스 연결을 요청끼리 나눠 쓰지 않게
    write_lock = threading.Lock()  # Inbox 파일 이동이 겹치지 않게

    def names() -> dict[str, str]:
        return {s.id: s.name for s in views.configured_sources(ctx.wiki)}

    def page(request: Request, name: str, active: str, **values) -> HTMLResponse:
        side = views.sidebar(ctx.wiki, ctx.index, ctx.device, ctx.clock, ctx.scheduler)
        theme = appsettings.load(ctx.settings_path).theme if ctx.settings_path else "system"
        shell = {
            "theme": theme,
            "in_app": ctx.app_mode,
            "update": update_view(),
            "onboarding": pending_onboarding(),
        }
        return templates.TemplateResponse(
            request, name, {"active": active, "side": side, "pages": PAGES, **shell, **values}
        )

    def pending_onboarding() -> onboarding.Onboarding | None:
        """사이드바의 "시작하기 n/5". 다 끝났으면 None (탭을 뺀다)."""
        if ctx.flow is None or ctx.settings_path is None:
            return None
        return onboarding.pending(ctx.flow, ctx.wiki, ctx.settings_path)

    def journey() -> onboarding.Onboarding:
        settings = appsettings.load(ctx.settings_path) if ctx.settings_path else None
        return onboarding.build(ctx.flow, ctx.wiki, settings or appsettings.Settings())

    def sync_now_state() -> dict[str, Any] | None:
        return views.sync_state(ctx.scheduler) if ctx.scheduler is not None else None

    def busy() -> bool:
        return ctx.scheduler is not None and ctx.scheduler.running

    def update_view() -> dict[str, Any] | None:
        return updateroutes.update_view(ctx.updates, busy(), ctx.clock())

    if ctx.updates is not None:
        updateroutes.add_routes(app, ctx.updates, templates, busy, ctx.clock)

    def current_alerts() -> list:
        pending = len(ctx.index.select("WHERE status = ?", ("pending",)))
        return alerts(ctx.wiki, pending, ctx.clock())

    @app.get("/")
    def home() -> RedirectResponse:
        needed = ctx.flow is not None and ctx.flow.needed()
        return RedirectResponse("/setup" if needed else "/today", status_code=303)

    if ctx.flow is not None:
        setuproutes.add_routes(
            app,
            ctx.flow,
            templates,
            page,
            ctx.model,
            ctx.download_model,
            ctx.on_settings,
            journey,
            sync_now_state,
        )

    @app.get("/today", response_class=HTMLResponse)
    def today(request: Request, date: str | None = None) -> HTMLResponse:
        day = _parse_day(date)
        view = views.build_today(ctx.wiki, ctx.index, ctx.device, ctx.mcp_log, day, ctx.clock)
        return page(request, "today.html", "today", view=view, alerts=current_alerts())

    def mcp_connected() -> bool | None:
        return ctx.flow.mcp_ok() if ctx.flow is not None else None

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request) -> HTMLResponse:
        params = request.query_params
        query = searchview.parse_query(params, {k: params.getlist(k) for k in ("company", "tag")})
        with search_lock:
            view = searchview.build_search(
                tools, ctx.wiki, ctx.index, query, names(), ctx.clock(), mcp_connected
            )
        if request.headers.get("hx-request") == "true":  # 검색어 · 필터가 바뀌면 결과만 다시 그린다
            return templates.TemplateResponse(request, "search_body.html", {"view": view})
        return page(request, "search.html", "search", view=view)

    @app.get("/article/{article_id}", response_class=HTMLResponse)
    def article(request: Request, article_id: str) -> Response:
        """글 상세 모달의 내용 (Search 결과 · Today 목록)."""
        with search_lock:
            view = articleview.build_article(tools, ctx.wiki, ctx.index, article_id, names())
        if view is None:
            return PlainTextResponse("없는 글", status_code=404)
        return templates.TemplateResponse(request, "_article.html", {"a": view})

    regroup = decisionsview.RegroupJob(ctx.wiki, ctx.regroup_llm)

    @app.get("/decisions", response_class=HTMLResponse)
    def decisions(
        request: Request, sel: str | None = None, q: str = "", all: str = ""
    ) -> HTMLResponse:
        view = decisionsview.build_decisions(ctx.wiki, ctx.index, names(), sel, q, all == "1")
        if request.headers.get("hx-request") == "true" and request.headers.get("hx-target"):
            return templates.TemplateResponse(
                request, "decisions_body.html", {"view": view, "job": regroup}
            )
        return page(request, "decisions.html", "decisions", view=view, job=regroup)

    @app.post("/decisions/regroup", response_class=HTMLResponse)
    def decisions_regroup(request: Request) -> HTMLResponse:
        regroup.start(busy=busy())
        return templates.TemplateResponse(request, "_regroup.html", {"job": regroup})

    @app.get("/decisions/regroup", response_class=HTMLResponse)
    def decisions_regroup_poll(request: Request) -> HTMLResponse:
        response = templates.TemplateResponse(request, "_regroup.html", {"job": regroup})
        if regroup.state == "done":
            regroup.state = "idle"  # 한 번 알렸다
            response.headers["HX-Refresh"] = "true"  # 새 주제로 화면을 다시 그린다
        return response

    @app.get("/inbox", response_class=HTMLResponse)
    def inbox(request: Request, sel: str | None = None, confirm: str | None = None) -> Response:
        view = inboxview.build_inbox(ctx.wiki, ctx.index, names(), sel)
        return page(request, "inbox.html", "inbox", view=view, confirm=confirm == "init")

    @app.post("/inbox/decide", response_class=HTMLResponse)
    def inbox_decide(
        request: Request, article_id: str = Form(...), choice: str = Form(...)
    ) -> Response:
        if choice not in ("save", "dismiss"):
            return PlainTextResponse("잘못된 요청", status_code=400)
        with write_lock:
            article = inboxview.pending_inbox(ctx.index, article_id)
            if article is None:
                return PlainTextResponse("이미 정리했거나 없는 글", status_code=404)
            recent, init = inboxview.inbox_items(ctx.index, {})
            following = inboxview.neighbor([i.article for i in [*recent, *init]], article_id)
            inboxview.decide(
                ctx.wiki, ctx.index, ctx.device, article, choice == "save", ctx.clock()
            )
        return inbox(request, sel=following)

    @app.post("/inbox/dismiss-init", response_class=HTMLResponse)
    def inbox_dismiss_init(request: Request) -> Response:
        with write_lock:
            inboxview.dismiss_init(ctx.wiki, ctx.index, ctx.device, ctx.clock())
        return inbox(request)

    @app.get("/sources", response_class=HTMLResponse)
    def sources(request: Request) -> HTMLResponse:
        view = sourcesview.build_sources(ctx.wiki, ctx.mcp_log, ctx.clock())
        return page(request, "sources.html", "sources", view=view, alerts=current_alerts())

    def sync_card(request: Request) -> HTMLResponse:
        side = views.sidebar(ctx.wiki, ctx.index, ctx.device, ctx.clock, ctx.scheduler)
        return templates.TemplateResponse(request, "_sync_card.html", {"side": side})

    @app.get("/sync-card", response_class=HTMLResponse)
    def sync_card_poll(request: Request) -> HTMLResponse:
        response = sync_card(request)
        if ctx.scheduler is None or not ctx.scheduler.running:
            response.headers["HX-Refresh"] = (
                "true"  # 수집이 끝났다: 새 글을 보이게 화면을 새로 고친다
            )
        return response

    @app.post("/sync", response_class=HTMLResponse)
    def sync_now(request: Request) -> Response:
        if ctx.scheduler is None:
            return PlainTextResponse("앱에서만 수집할 수 있다", status_code=409)
        ctx.scheduler.run_now()
        return sync_card(request)

    @app.get("/{name}", response_class=HTMLResponse)
    def placeholder(request: Request, name: str) -> Response:
        if name not in PAGES:
            return PlainTextResponse("없는 화면", status_code=404)
        title, note = PAGES[name]
        return page(request, "placeholder.html", name, title=title, note=note)

    @app.post("/feedback", response_class=HTMLResponse)
    def feedback(article_id: str = Form(...), kind: str = Form(...)) -> Response:
        article = ctx.index.get(article_id)
        try:
            chosen = Kind(kind)
        except ValueError:
            chosen = None
        if article is None or chosen not in FEEDBACK_DONE:
            return PlainTextResponse("잘못된 요청", status_code=400)
        record_feedback(ctx.wiki, ctx.device, chosen, article.url, ctx.clock())
        return HTMLResponse(f'<span class="done-label">{FEEDBACK_DONE[chosen]}</span>')

    return app


def _guard(ctx: ScreenContext):
    async def dispatch(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.headers.get("host") not in ctx.hosts:
            return PlainTextResponse("허용하지 않은 주소", status_code=403)
        given = request.query_params.get("token")
        if given is not None:
            if not secrets.compare_digest(given, ctx.token):
                return PlainTextResponse("토큰이 맞지 않다", status_code=403)
            rest = urlencode(
                [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
            )
            target = request.url.path + (f"?{rest}" if rest else "")
            response = RedirectResponse(target, status_code=303)
            response.set_cookie(COOKIE, ctx.token, httponly=True, samesite="strict")
            return _harden(response)
        if not secrets.compare_digest(request.cookies.get(COOKIE, ""), ctx.token):
            return PlainTextResponse("앱에서 열어 주세요", status_code=403)
        if request.method != "GET" and request.headers.get("hx-request") != "true":
            return PlainTextResponse("허용하지 않은 요청", status_code=403)
        return _harden(await call_next(request))

    return dispatch


def _harden(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
