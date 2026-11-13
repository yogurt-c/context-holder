"""엔진 화면 서버 (plan 7장). Tauri 창이 `http://127.0.0.1:<port>`를 띄운다.

보호:
- 시작할 때 만든 토큰을 앱에만 준다. 첫 요청 `/?token=…`에서 HttpOnly 쿠키로 바꾸고 주소에서 지운다.
- Host 헤더가 서버 주소가 아니면 거절한다 (DNS rebinding).
- 쓰기 요청은 HTMX가 붙이는 HX-Request 헤더도 요구한다. 쿠키는 SameSite=Strict다.
"""

import json
import logging
import secrets
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from context_holder import appsettings, onboarding
from context_holder import topics as topicmod
from context_holder.embed import Embedder
from context_holder.feedback import Kind, record_feedback
from context_holder.index import Index
from context_holder.llm import AGENT_TOOLS, AGENTS, ClaudeCli
from context_holder.mcptools import WikiTools
from context_holder.screens import (
    addarticle,
    articleview,
    bookmarksview,
    decisionsview,
    inboxview,
    searchview,
    setuproutes,
    sourcesview,
    updateroutes,
    views,
)
from context_holder.screens.runs import alerts
from context_holder.screens.suggestions import Suggester, Suggestion
from context_holder.wiki import Wiki

COOKIE = "ch_token"
SUGGEST_MAX_QUERY = 60  # 추천은 검색어 앞부분만 본다
STOP_FIXES = ("auth", "limit", "not_installed")  # 수집 카드에서 고칠 수 있는 멈춤 이유
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)
PAGES = {  # 앱 밖(브라우저)에서만 보이는 자리
    "settings": ("설정", "설정은 앱에서 열 수 있어요."),
}
log = logging.getLogger(__name__)
REMOVE_BUSY = "검색 색인을 맞추는 중이라 지금은 지울 수 없어요. 잠시 후 다시 시도해 주세요."
FEEDBACK_KINDS = frozenset({Kind.BOOKMARK, Kind.UNBOOKMARK})  # 화면에서 남기는 피드백


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
    regroup_llm: Callable[[], Any] = field(default=lambda: ClaudeCli())  # 글 추가 · 주제 묶기


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
    search_lock = threading.RLock()  # 검색 모델과 인덱스 연결을 요청끼리 나눠 쓰지 않게
    suggester = Suggester(ctx.wiki, ctx.index, ctx.embedder, model_lock=search_lock)
    suggester.warm()
    write_lock = threading.Lock()  # Inbox 파일 이동이 겹치지 않게

    addition = addarticle.AddArticleJob(ctx, write_lock, search_lock, lambda: agent())
    if ctx.scheduler is not None:
        ready = getattr(ctx.scheduler, "ready", lambda: True)
        ctx.scheduler.ready = lambda: addition.state != "running" and ready()

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
        """사이드바의 "Onboarding n/5". 다 끝났으면 None (탭을 뺀다)."""
        if ctx.flow is None or ctx.settings_path is None:
            return None
        return onboarding.pending(ctx.flow, ctx.wiki, ctx.settings_path)

    def journey() -> onboarding.Onboarding:
        settings = appsettings.load(ctx.settings_path) if ctx.settings_path else None
        return onboarding.build(ctx.flow, ctx.wiki, settings or appsettings.Settings())

    def sync_now_state() -> dict[str, Any] | None:
        return views.sync_state(ctx.scheduler) if ctx.scheduler is not None else None

    def busy() -> bool:
        return addition.state == "running" or (ctx.scheduler is not None and ctx.scheduler.running)

    def update_view() -> dict[str, Any] | None:
        return updateroutes.update_view(ctx.updates, busy(), ctx.clock())

    if ctx.updates is not None:
        updateroutes.add_routes(app, ctx.updates, templates, busy, ctx.clock)

    def agent() -> str:
        return appsettings.load(ctx.settings_path).agent if ctx.settings_path else "claude"

    templates.env.globals["agent_name"] = lambda: AGENTS[agent()]

    # 수집 카드에서 다시 로그인하고, 승인되면 바로 다시 수집한다 ({"agent": …} · {"error": …})
    login_retry: dict[str, str] = {}

    def sync_fix(status: views.RunStatus) -> dict[str, Any] | None:
        """에이전트 문제로 멈췄으면 카드에 보일 원인과 해결 방법."""
        if ctx.flow is None or status.state != "stopped" or status.stopped not in STOP_FIXES:
            return None
        chosen = agent()
        job = ctx.flow.job(chosen)
        return {
            "kind": status.stopped,
            "agent": chosen,
            "name": AGENTS[chosen],
            "tool": AGENT_TOOLS[chosen],
            "waiting": job.state == "logging_in",
            "error": login_retry.get("error") or (job.message if job.state == "error" else ""),
        }

    templates.env.globals["sync_fix"] = sync_fix

    def current_alerts() -> list:
        pending = len(ctx.index.select("WHERE status = ?", ("pending",)))
        return alerts(ctx.wiki, pending, ctx.clock(), agent())

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

    @app.get("/today/more", response_class=HTMLResponse)
    def today_more(request: Request, date: str = "", after: str = "") -> HTMLResponse:
        day = _parse_day(date) or ctx.clock().astimezone().date()
        page_, mark = views.today_more(ctx.wiki, ctx.index, day, after)
        return templates.TemplateResponse(
            request, "today_more.html", {"page": page_, "mark": mark, "day": day}
        )

    @app.get("/articles/add", response_class=HTMLResponse)
    def add_form(request: Request, new: bool = False) -> HTMLResponse:
        if new:
            addition.dismiss()
        return templates.TemplateResponse(request, "_add_article.html", {"job": addition})

    @app.get("/articles/add/status", response_class=HTMLResponse)
    def add_status(request: Request) -> HTMLResponse:
        response = templates.TemplateResponse(request, "_add_status.html", {"job": addition})
        current = urlsplit(request.headers.get("HX-Current-URL", "")).path
        # 다른 화면에서는 알림만 띄운다. Today로 가면 새 글이 이미 그려진다.
        if addition.take_changed() and current == "/today":
            response.headers["HX-Refresh"] = "true"  # 추가한 글이 오늘의 브리프에 보이게
        return response

    @app.post("/articles/add/dismiss", response_class=HTMLResponse)
    def add_dismiss(request: Request) -> HTMLResponse:
        addition.dismiss()
        return add_status(request)

    @app.post("/articles/add", response_class=HTMLResponse)
    def add_article(request: Request, url: str = Form("")) -> HTMLResponse:
        addition.start(url, busy=busy())
        response = add_form(request)
        if not addition.in_modal:  # 입력 창을 닫고 우측 하단에서 진행을 보인다
            response.headers["HX-Trigger"] = "add-started"
        return response

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

    @app.get("/search/more", response_class=HTMLResponse)
    def search_more(request: Request, after: str = "") -> HTMLResponse:
        params = request.query_params
        query = searchview.parse_query(params, {k: params.getlist(k) for k in ("company", "tag")})
        with search_lock:
            page_ = searchview.search_more(tools, ctx.wiki, query, names(), ctx.clock(), after)
        return templates.TemplateResponse(
            request, "search_more.html", {"page": page_, "query": query}
        )

    @app.get("/suggest", response_class=HTMLResponse)
    def suggest(
        request: Request, q: str = "", to: str = Query("search", alias="for")
    ) -> HTMLResponse:
        """검색창 추천. Decisions에서는 넓은 주제 비교표로, Search에서는 그 문장으로 찾는다."""
        with search_lock:
            items = suggester.suggest(q[:SUGGEST_MAX_QUERY], grouped_only=to == "decisions")

        def url(s: Suggestion) -> str:
            if to == "decisions":
                return "/decisions?" + urlencode({"sel": s.topic})
            return "/search?" + urlencode({"q": s.text})

        if not items:  # 빈 상자를 그리지 않는다
            return HTMLResponse("")
        return templates.TemplateResponse(
            request, "_suggest.html", {"items": [(s, url(s)) for s in items], "q": q.strip()}
        )

    @app.get("/article/{article_id}", response_class=HTMLResponse)
    def article(request: Request, article_id: str) -> Response:
        """글 상세 모달의 내용 (Search 결과 · Today 목록)."""
        with search_lock:
            view = articleview.build_article(tools, ctx.wiki, ctx.index, article_id, names())
        if view is None:
            return PlainTextResponse("없는 글", status_code=404)
        return templates.TemplateResponse(request, "_article.html", {"a": view})

    @app.post("/article/{article_id}/remove")
    def article_remove(article_id: str) -> Response:
        """지운다. 성공하면 화면(app.js)이 상세창을 닫고 보던 화면을 새로 그린다."""
        try:
            with write_lock:
                removed = articleview.remove_article(ctx.wiki, ctx.index, article_id)
        except sqlite3.OperationalError:  # 앱을 켠 직후 검색 색인을 맞추는 중이면 잠겨 있다
            log.warning("글 제거 실패: 인덱스가 잠겨 있다 (%s)", article_id, exc_info=True)
            return PlainTextResponse(REMOVE_BUSY, status_code=503)
        if not removed:
            return PlainTextResponse("이미 지웠거나 없는 글이에요.", status_code=404)
        return Response(status_code=204)

    @app.get("/decisions", response_class=HTMLResponse)
    def decisions(
        request: Request, sel: str | None = None, q: str = "", area: str = ""
    ) -> HTMLResponse:
        view = decisionsview.build_decisions(ctx.wiki, ctx.index, names(), sel, q, area)
        if request.headers.get("hx-request") == "true" and request.headers.get("hx-target"):
            return templates.TemplateResponse(request, "decisions_body.html", {"view": view})
        return page(request, "decisions.html", "decisions", view=view)

    @app.get("/decisions/more", response_class=HTMLResponse)
    def decisions_more(
        request: Request, area: str = "", q: str = "", sel: str = "", after: str = ""
    ) -> HTMLResponse:
        page_, view = decisionsview.decisions_more(ctx.wiki, ctx.index, area, q, sel, after)
        return templates.TemplateResponse(
            request, "decisions_more.html", {"page": page_, "view": view}
        )

    @app.get("/bookmarks", response_class=HTMLResponse)
    def bookmarks(request: Request) -> HTMLResponse:
        view = bookmarksview.build_bookmarks(ctx.wiki, ctx.index, names())
        return page(request, "bookmarks.html", "bookmarks", view=view)

    @app.get("/bookmarks/more", response_class=HTMLResponse)
    def bookmarks_more(request: Request, after: str = "") -> Response:
        page_ = bookmarksview.bookmarks_more(ctx.wiki, ctx.index, names(), after)
        if page_ is None:
            return PlainTextResponse("잘못된 커서", status_code=400)
        return templates.TemplateResponse(request, "bookmarks_more.html", {"page": page_})

    def decisions_body(
        request: Request, sel: str, saved: decisionsview.EditNote | None = None
    ) -> HTMLResponse:
        """주제를 고친 뒤 Decisions 본문을 다시 그린다. 주소도 그 주제로 바꾼다."""
        view = decisionsview.build_decisions(ctx.wiki, ctx.index, names(), sel)
        response = templates.TemplateResponse(
            request, "decisions_body.html", {"view": view, "saved": saved}
        )
        response.headers["HX-Push-Url"] = view.url()
        return response

    @app.get("/decisions/move", response_class=HTMLResponse)
    def decisions_move_form(request: Request, sel: str = "") -> Response:
        """사례 옮기기 창의 내용."""
        view = decisionsview.build_move(ctx.wiki, ctx.index, names(), sel)
        if view is None:
            return PlainTextResponse("없는 주제", status_code=404)
        return templates.TemplateResponse(request, "_topic_move.html", {"m": view})

    @app.post("/decisions/move", response_class=HTMLResponse)
    def decisions_move(
        request: Request,
        sel: str = Form(""),
        topic: str = Form(""),
        mode: str = Form("existing"),
        to: str = Form(""),
        name: str = Form(""),
        area: str = Form(""),
        question: str = Form(""),
    ) -> Response:
        """사례를 옮기고 창을 닫는다. 보던 주제에 사례가 남았으면 그대로, 없으면 옮긴 주제를 연다.

        못 옮기면 이유를 글로 준다 (app.js가 창에 보인다).
        """
        if not topic:
            return PlainTextResponse("옮길 사례를 골라 주세요.", status_code=422)
        try:
            if mode == "new":
                moved = topicmod.move(ctx.wiki, topic, name, area, question)
            else:
                moved = topicmod.move(ctx.wiki, topic, to)
        except ValueError as e:
            return PlainTextResponse(str(e), status_code=422)
        stay = topicmod.load(ctx.wiki).group(sel) is not None
        response = decisions_body(request, sel if stay else moved)
        response.headers.update(
            {
                "HX-Retarget": "#decisions-body",
                "HX-Reswap": "innerHTML",
                "HX-Trigger": "topic-moved",
            }
        )
        return response

    @app.post("/decisions/edit", response_class=HTMLResponse)
    def decisions_edit(
        request: Request,
        sel: str = Form(""),
        name: str = Form(""),
        area: str = Form(""),
        question: str = Form(""),
        undo: bool = Form(False),
    ) -> Response:
        """주제의 이름 · 분야 · 질문을 고친다. 못 고치면 이유를 글로 준다 (app.js가 폼에 보인다).

        고쳤으면 머리에 무엇을 바꿨는지와 되돌리기를 보인다. 되돌린 뒤에는 되돌리기를 두지 않는다.
        """
        before = topicmod.load(ctx.wiki).group(sel)
        try:
            renamed = topicmod.edit(ctx.wiki, sel, name, area, question)
        except ValueError as e:
            return PlainTextResponse(str(e), status_code=422)
        after = topicmod.load(ctx.wiki).group(renamed)
        note = decisionsview.edit_note(before, after, undo) if before and after else None
        return decisions_body(request, renamed, note)

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

    @app.post("/sync/login", response_class=HTMLResponse)
    def sync_login(request: Request) -> Response:
        if ctx.flow is None:
            return PlainTextResponse("앱에서만 로그인할 수 있다", status_code=409)
        chosen = agent()
        login_retry.clear()
        ctx.flow.relogin_account(chosen, ctx.flow.account(chosen))
        login_retry["agent"] = chosen
        return sync_card(request)

    @app.get("/sync-card/login", response_class=HTMLResponse)
    def sync_login_poll(request: Request) -> HTMLResponse:
        """승인을 기다린다. 로그인됐으면 바로 다시 수집하고, 아니면 이유를 남긴다."""
        chosen = login_retry.get("agent")
        if ctx.flow is None or not chosen:
            return sync_card(request)
        ctx.flow.settle(chosen)
        state = ctx.flow.job(chosen).state
        if state == "logging_in":
            return sync_card(request)
        login_retry.clear()
        if state != "error":  # error: 띄우지 못했거나 시간이 지났다. 작업 메시지를 보인다
            if ctx.flow.agent_status(fresh=True).logged_in:
                if ctx.scheduler is not None:
                    ctx.scheduler.run_now()
            else:
                login_retry["error"] = "로그인하지 못했어요. 다시 시도해 주세요."
        return sync_card(request)

    @app.post("/sync/login/cancel", response_class=HTMLResponse)
    def sync_login_cancel(request: Request) -> HTMLResponse:
        chosen = login_retry.pop("agent", None)
        if ctx.flow is not None and chosen:
            ctx.flow.cancel_login(chosen)
        return sync_card(request)

    @app.get("/{name}", response_class=HTMLResponse)
    def placeholder(request: Request, name: str) -> Response:
        if name not in PAGES:
            return PlainTextResponse("없는 화면", status_code=404)
        title, note = PAGES[name]
        return page(request, "placeholder.html", name, title=title, note=note)

    @app.post("/feedback", response_class=HTMLResponse)
    def feedback(request: Request, article_id: str = Form(...), kind: str = Form(...)) -> Response:
        """북마크 · 해제를 남기고, 바뀐 아이콘 버튼을 돌려준다 (그 버튼 자리만 바꾼다)."""
        article = ctx.index.get(article_id)
        try:
            chosen = Kind(kind)
        except ValueError:
            chosen = None
        if article is None or chosen not in FEEDBACK_KINDS:
            return PlainTextResponse("잘못된 요청", status_code=400)
        record_feedback(ctx.wiki, ctx.device, chosen, article.url, ctx.clock())
        response = templates.TemplateResponse(
            request,
            "_bookmark_toggle.html",
            {"article_id": article.id, "on": chosen == Kind.BOOKMARK},
        )
        # 열려 있는 Bookmarks 목록에서 그 줄만 숨기거나 다시 보인다 (app.js)
        changed = {"id": article.id, "on": chosen == Kind.BOOKMARK}
        response.headers["HX-Trigger"] = json.dumps({"bookmark-changed": changed})
        return response

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
