"""`ctx serve`: 화면 서버를 127.0.0.1의 임의 포트로 띄운다.

준비되면 stdout에 JSON 한 줄을 쓴다: {"port", "token", "url"}. Tauri 앱이 이 줄을 읽는다.
앱이 띄울 때는 stdin이 닫히면(앱이 죽으면) 함께 끝낸다. stdin의 각 줄은 메뉴 명령이다 (tray).
"""

import json
import socket
import threading
from collections.abc import Callable
from typing import IO

import uvicorn

from context_holder.screens.app import ScreenContext, create_app

HOST = "127.0.0.1"


def bind() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    sock.listen(64)
    return sock


def ready_line(port: int, token: str) -> str:
    url = f"http://{HOST}:{port}/?token={token}"
    return json.dumps({"port": port, "token": token, "url": url})


def watch_stdin(
    stream: IO[str], on_eof: Callable[[], None], on_line: Callable[[str], None] = lambda _: None
) -> None:
    """stdin이 닫힐 때까지 한 줄씩 넘기고 on_eof를 부른다. 앱 프로세스가 죽으면 stdin이 닫힌다."""
    for line in iter(stream.readline, ""):
        if line.strip():
            on_line(line.strip())
    on_eof()


def serve(
    make_context: Callable[[int], ScreenContext],
    out: IO[str],
    stdin: IO[str] | None,
    on_ready: Callable[[str], None],
    warm: Callable[[], None],
    on_line: Callable[[str], None] = lambda _: None,
    background: Callable[[], None] | None = None,
) -> None:  # pragma: no cover - 서버 루프를 붙잡는다. 부품은 따로 테스트한다
    sock = bind()
    port = sock.getsockname()[1]
    ctx = make_context(port)
    server = uvicorn.Server(
        uvicorn.Config(create_app(ctx), log_level="warning", access_log=False, lifespan="off")
    )
    threading.Thread(target=warm, daemon=True).start()
    if stdin is not None:

        def stop() -> None:
            server.should_exit = True

        threading.Thread(target=watch_stdin, args=(stdin, stop, on_line), daemon=True).start()
    line = ready_line(port, ctx.token)
    out.write(line + "\n")
    out.flush()
    on_ready(json.loads(line)["url"])
    if background is not None:  # 준비 줄 다음에 이벤트를 쓴다 (스케줄러)
        threading.Thread(target=background, daemon=True).start()
    server.run(sockets=[sock])
