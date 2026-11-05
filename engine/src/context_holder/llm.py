"""Claude 구독(`claude -p`)으로 구조화된 JSON 응답을 받는다.

API 키를 쓰지 않고 Claude Code의 로그인 세션을 쓴다. 엔진이 부르는 Claude는
사용자 설정 · hook · MCP · 도구를 모두 끈 상태로 실행해서 사용자 환경과 섞이지 않게 한다.
"""

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple


class ErrorKind(StrEnum):
    NOT_INSTALLED = "not_installed"  # claude 명령이 없다
    AUTH = "auth"  # 로그인이 필요하거나 만료됐다
    LIMIT = "limit"  # 구독 사용 한도에 걸렸다
    TIMEOUT = "timeout"
    FAILED = "failed"  # 그 밖의 실행 오류
    BAD_OUTPUT = "bad_output"  # 응답이 기대한 JSON이 아니다


class LlmError(RuntimeError):
    def __init__(self, kind: ErrorKind, message: str):
        super().__init__(message)
        self.kind = kind

    def __str__(self) -> str:
        return f"{self.kind}: {self.args[0]}"


@dataclass(frozen=True)
class LlmResult:
    data: dict[str, Any]
    cost_usd: float  # 구독이라 실제 청구는 없다. 사용량 가늠용
    duration_ms: int


class Run(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], str, float, str], Run]

AUTH_HINTS = ("login", "log in", "api key", "unauthorized", "oauth", "credential")
LIMIT_HINTS = ("usage limit", "rate limit", "limit reached", "quota")


def subprocess_runner(args: list[str], stdin: str, timeout: float, cwd: str) -> Run:
    env = {**os.environ, "CONTEXT_HOLDER_INTERNAL": "1"}  # 우리 hook이 다시 실행되지 않게 표시
    done = subprocess.run(
        args,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
        check=False,
    )
    return Run(done.returncode, done.stdout, done.stderr)


class ClaudeCli:
    def __init__(
        self,
        model: str = "sonnet",
        runner: Runner = subprocess_runner,
        executable: str = "claude",
        timeout: float = 300.0,
    ):
        self.model = model
        self.runner = runner
        self.executable = executable
        self.timeout = timeout

    def structured(self, prompt: str, system: str, schema: dict[str, Any]) -> LlmResult:
        args = [
            self.executable,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--system-prompt",
            system,
            "--json-schema",
            json.dumps(schema, ensure_ascii=False),
        ]
        with tempfile.TemporaryDirectory(prefix="ctx-claude-") as workdir:
            run = self._run(args, prompt, workdir)
        return _parse(run)

    def _run(self, args: list[str], prompt: str, workdir: str) -> Run:
        try:
            return self.runner(args, prompt, self.timeout, workdir)
        except FileNotFoundError as e:
            raise LlmError(ErrorKind.NOT_INSTALLED, "Claude Code가 설치돼 있지 않다") from e
        except subprocess.TimeoutExpired as e:
            raise LlmError(ErrorKind.TIMEOUT, f"{self.timeout:.0f}초 안에 끝나지 않았다") from e


def _parse(run: Run) -> LlmResult:
    if run.returncode != 0:
        raise LlmError(
            _kind_from_text(run.stderr or run.stdout), _snippet(run.stderr or run.stdout)
        )
    try:
        body = json.loads(run.stdout)
    except json.JSONDecodeError as e:
        raise LlmError(ErrorKind.BAD_OUTPUT, f"JSON이 아닌 출력: {_snippet(run.stdout)}") from e
    if not isinstance(body, dict):
        raise LlmError(ErrorKind.BAD_OUTPUT, "응답이 객체가 아니다")
    if body.get("is_error"):
        message = str(body.get("result") or body.get("subtype") or "알 수 없는 오류")
        raise LlmError(_kind_from_text(message), _snippet(message))
    data = body.get("structured_output")
    if not isinstance(data, dict):
        raise LlmError(ErrorKind.BAD_OUTPUT, "structured_output이 없다")
    return LlmResult(
        data=data,
        cost_usd=float(body.get("total_cost_usd") or 0.0),
        duration_ms=int(body.get("duration_ms") or 0),
    )


def _kind_from_text(text: str) -> ErrorKind:
    lowered = text.lower()
    if any(hint in lowered for hint in LIMIT_HINTS):
        return ErrorKind.LIMIT
    if any(hint in lowered for hint in AUTH_HINTS):
        return ErrorKind.AUTH
    return ErrorKind.FAILED


def _snippet(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"
