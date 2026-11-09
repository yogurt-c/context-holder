"""Tauri가 띄우는 엔진 흉내. pygit2를 불러오고, 살아 있는 동안 상태를 한 줄씩 출력한다.

- 시작하면 ready 줄을 출력한다 (시작 시간 확인).
- 앱이 죽어 stdin이 닫히면 스스로 끝난다 (고아 프로세스 방지).
- SPIKE_CRASH_ONCE 파일 경로를 주면 처음 한 번은 2초 뒤 비정상 종료한다 (재시작 확인).
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

START = time.perf_counter()

import pygit2  # 묶은 바이너리에서 libgit2를 불러오는 시간까지 잰다


def watch_stdin() -> None:
    sys.stdin.read()  # 부모가 죽으면 EOF
    print(json.dumps({"event": "stdin_closed"}), flush=True)
    os._exit(0)


def main() -> int:
    threading.Thread(target=watch_stdin, daemon=True).start()
    print(
        json.dumps(
            {
                "event": "ready",
                "pid": os.getpid(),
                "libgit2": pygit2.LIBGIT2_VERSION,
                "startup_sec": round(time.perf_counter() - START, 3),
            }
        ),
        flush=True,
    )
    marker = os.environ.get("SPIKE_CRASH_ONCE")
    beats = 0
    while True:
        time.sleep(0.5)
        beats += 1
        print(json.dumps({"event": "beat", "n": beats}), flush=True)
        if marker and not Path(marker).exists() and beats == 4:
            Path(marker).write_text("crashed")
            print(json.dumps({"event": "crash"}), flush=True)
            return 3


if __name__ == "__main__":
    raise SystemExit(main())
