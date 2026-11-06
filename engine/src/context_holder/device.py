"""이 기기의 이름. 기기별 피드백 파일과 수집 잠금 기록에 쓴다.

처음 한 번 Mac 이름으로 만들어 `~/.context-holder/device.json`에 저장한다.
나중에 Mac 이름을 바꿔도 파일 이름이 바뀌지 않게 저장한 이름을 계속 쓴다.
"""

import json
import re
import socket
import subprocess
from pathlib import Path


def device_name(home: Path) -> str:
    path = home / "device.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["name"]
    name = _slug(_mac_name()) or "device"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name}, ensure_ascii=False), encoding="utf-8")
    return name


def _mac_name() -> str:
    try:
        out = subprocess.run(
            ["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return socket.gethostname()
    return out.stdout.strip() if out.returncode == 0 else socket.gethostname()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.removesuffix(".local").lower()).strip("-")[:40]
