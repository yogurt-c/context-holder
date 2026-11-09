"""앱 업데이트 화면: 우측 하단 팝업과 설정의 "앱 업데이트" (updates.py).

버튼은 일을 시킨 뒤 `update-changed`를 알린다.
팝업과 설정 영역은 그 신호와 시간으로 스스로 다시 그린다.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, Response

from context_holder.screens.views import short_day
from context_holder.updates import WORKING, Updates

CHANGED = {"HX-Trigger": "update-changed"}


def update_view(updates: Updates | None, busy: bool, now: datetime) -> dict[str, Any] | None:
    if updates is None:  # 앱 밖(브라우저 · 개발 서버)에서는 보이지 않는다
        return None
    s = updates.state
    return {
        "s": s,
        "current": updates.current,
        "toast": updates.toast(),
        "working": s.state in WORKING,
        "busy": busy,
        "checked": _checked(s.checked, now),
        "auto": updates.auto(),
    }


def add_routes(
    app: FastAPI,
    updates: Updates,
    templates,
    busy: Callable[[], bool],
    clock: Callable[[], datetime],
) -> None:
    def view() -> dict[str, Any] | None:
        return update_view(updates, busy(), clock())

    @app.get("/update/toast", response_class=HTMLResponse)
    def toast(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "_update_toast.html", {"update": view()})

    @app.get("/settings/update", response_class=HTMLResponse)
    def settings_update(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "_update_settings.html", {"update": view()})

    @app.post("/update/check")
    def check() -> Response:
        updates.check()
        return Response(headers=CHANGED)

    @app.post("/update/install")
    def install() -> Response:
        updates.install()
        return Response(headers=CHANGED)

    @app.post("/update/restart")
    def restart() -> Response:
        updates.restart(busy=busy())
        return Response(headers=CHANGED)

    @app.post("/update/dismiss")
    def dismiss() -> Response:
        updates.dismiss()
        return Response(headers=CHANGED)

    @app.post("/update/auto", response_class=HTMLResponse)
    def auto(auto_update: bool = Form(False)) -> HTMLResponse:
        updates.set_auto(auto_update)
        return HTMLResponse('<span class="done-label">저장했어요</span>')


def _checked(at: datetime | None, now: datetime) -> str:
    if at is None:
        return ""
    local = at.astimezone()
    day = "오늘" if local.date() == now.astimezone().date() else short_day(local.date())
    return f"{day} {local:%H:%M} 확인"
