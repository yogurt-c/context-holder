"""기기 로컬 앱 설정 (`~/.context-holder/app-state.json`). 위키에 올리지 않는다.

수집 시각은 기기마다 달라도 된다. 하루 한 번 잠금이 있어 먼저 맡은 기기가 수집한다.
같은 파일에 스케줄러의 일시정지 상태도 있다. 서로의 키는 건드리지 않는다.
"""

import json
from dataclasses import asdict, dataclass, fields, replace
from datetime import time
from pathlib import Path
from typing import Any

THEMES = ("system", "light", "dark")


@dataclass(frozen=True)
class Settings:
    daily_at: str = "09:30"  # 매일 수집 시각 (HH:MM)
    weekends: bool = False  # 주말에도 그 시각에 수집
    notify: bool = True  # 수집 완료 알림
    autostart: bool = True  # 로그인할 때 자동 실행
    theme: str = "system"  # system | light | dark
    setup_done: bool = False  # 첫 실행 설정을 마쳤다

    @property
    def daily_time(self) -> time:
        return time.fromisoformat(self.daily_at)


def load(path: Path) -> Settings:
    data = _read(path)
    names = {f.name for f in fields(Settings)}
    values = {k: v for k, v in data.items() if k in names}
    try:
        settings = Settings(**values)
        settings.daily_time  # noqa: B018 - 깨진 시각이면 기본값으로
    except (TypeError, ValueError):
        return Settings()
    return settings if settings.theme in THEMES else replace(settings, theme="system")


def save(path: Path, settings: Settings) -> None:
    data = {**_read(path), **asdict(settings)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def update(path: Path, **changes: Any) -> Settings:
    settings = replace(load(path), **changes)
    settings.daily_time  # noqa: B018 - 잘못된 시각이면 ValueError
    if settings.theme not in THEMES:
        raise ValueError(f"모르는 화면 모드: {settings.theme}")
    save(path, settings)
    return settings


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
