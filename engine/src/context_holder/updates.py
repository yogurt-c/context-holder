"""앱 업데이트 (plan 2장). 엔진은 언제 확인할지 정하고 화면 · 메뉴에 보여 준다.

확인 · 내려받기 · 교체 · 다시 시작은 앱(Tauri 업데이터)이 한다.
둘은 stdin · stdout으로만 주고받는다.
- 엔진 → 앱: {"event": "update", "action": "check" | "install" | "restart"}
- 앱 → 엔진: update {"state": ..., "version": ..., "done": ..., "total": ..., "error": ...}

state: off(이 빌드는 업데이트를 받지 않는다) · idle · checking · available
· downloading · ready · error.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from context_holder import __version__, appsettings

STATES = ("off", "idle", "checking", "available", "downloading", "ready", "error")
WORKING = ("checking", "downloading")  # 화면이 자주 다시 물어야 하는 상태
CHECK_EVERY = timedelta(hours=2)
RELEASES = "https://github.com/yogurt-c/context-holder/releases"
ERROR_SIZE = 160


@dataclass(frozen=True)
class UpdateState:
    state: str = "idle"
    version: str = ""  # 받을 수 있는 새 버전
    done: int = 0  # 내려받은 바이트
    total: int = 0  # 전체 바이트 (모르면 0)
    error: str = ""
    checked: datetime | None = None  # 마지막으로 확인을 마친 때

    @property
    def ratio(self) -> int | None:
        return min(100, self.done * 100 // self.total) if self.total else None

    @property
    def notes_url(self) -> str:
        return f"{RELEASES}/tag/v{self.version}" if self.version else RELEASES

    @property
    def megabytes(self) -> str:
        done = f"{self.done / 1_000_000:.1f}"
        return f"{done} / {self.total / 1_000_000:.1f}MB" if self.total else f"{done}MB"


class Updates:
    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None],
        settings_path: Path,
        clock: Callable[[], datetime],
        on_change: Callable[[], None] = lambda: None,
    ):
        self.emit = emit
        self.settings_path = settings_path
        self.clock = clock
        self.on_change = on_change
        self.state = UpdateState()
        self.current = __version__
        self.closed = ""  # 이번 실행에서 닫은 버전

    # ---------- 앱이 알려 준 상태 ----------

    def feed(self, payload: str) -> None:
        """`update {...}` 뒤의 JSON. 모르는 상태나 깨진 줄은 무시한다."""
        try:
            data = json.loads(payload)
            state = str(data["state"])
        except (ValueError, KeyError, TypeError):
            return
        if state not in STATES:
            return
        checked = self.state.checked
        if self.state.state == "checking" and state != "checking":
            checked = self.clock()
        self.state = UpdateState(
            state=state,
            version=str(data.get("version") or ""),
            done=_count(data.get("done")),
            total=_count(data.get("total")),
            error=str(data.get("error") or "")[:ERROR_SIZE],
            checked=checked,
        )
        self.on_change()

    # ---------- 사용자 · 시간이 시키는 일 ----------

    def check(self) -> bool:
        if self.state.state in ("off", "checking", "downloading", "ready"):
            return False
        self.state = replace(self.state, state="checking", error="")
        self.emit({"event": "update", "action": "check"})
        self.on_change()
        return True

    def install(self) -> bool:
        if not self.state.version or self.state.state not in ("available", "error"):
            return False
        self.state = replace(self.state, state="downloading", done=0, total=0, error="")
        self.emit({"event": "update", "action": "install"})
        self.on_change()
        return True

    def restart(self, busy: bool) -> bool:
        """다시 시작해 적용한다. 수집 중이면 기다린다 (수집 프로세스가 끊기지 않게)."""
        if self.state.state != "ready" or busy:
            return False
        self.emit({"event": "update", "action": "restart"})
        return True

    def skip(self) -> None:
        """이 버전 알림을 다시 띄우지 않는다. 설정 화면에는 계속 보인다."""
        if self.state.version:
            appsettings.update(self.settings_path, skipped_update=self.state.version)
        self.on_change()

    def close(self) -> None:
        """설치 실패 팝업을 닫는다. 버전은 건너뛰지 않고, 앱을 다시 켜기 전까지만 숨긴다."""
        self.closed = self.state.version
        if self.state.state == "error":
            self.state = replace(self.state, state="available" if self.state.version else "idle")
        self.on_change()

    def auto(self) -> bool:
        return appsettings.load(self.settings_path).auto_update

    def set_auto(self, enabled: bool) -> None:
        appsettings.update(self.settings_path, auto_update=enabled)

    def tick(self) -> None:
        """자동 확인을 켰으면 앱이 뜰 때와 그 뒤 CHECK_EVERY마다 확인한다."""
        if not self.auto():
            return
        checked = self.state.checked
        if checked is None or self.clock() - checked >= CHECK_EVERY:
            self.check()

    # ---------- 화면 ----------

    def toast(self) -> bool:
        """우측 하단 팝업을 띄울까."""
        s = self.state
        if s.state in ("downloading", "ready"):
            return True
        if s.state == "error":
            return bool(s.version)  # 확인 실패는 띄우지 않고, 설치 실패만 띄운다
        if s.state == "available":
            hidden = (appsettings.load(self.settings_path).skipped_update, self.closed)
            return s.version not in hidden
        return False

    def menu_item(self, busy: bool) -> dict[str, Any] | None:
        s = self.state
        if s.state == "available":
            return {"label": f"업데이트 {s.version} 설치", "enabled": True, "cmd": "update_install"}
        if s.state == "downloading":
            return {"label": "업데이트 내려받는 중…", "enabled": False}
        if s.state == "ready":
            label = "수집이 끝나면 업데이트를 적용해요" if busy else "다시 시작해서 업데이트 적용"
            return {"label": label, "enabled": not busy, "cmd": "update_restart"}
        return None


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
