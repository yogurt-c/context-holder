"""구독 로그인한 코딩 에이전트 CLI로 구조화된 JSON 응답을 받는다.

API 키를 쓰지 않고 Claude Code(`claude -p`)나 Codex(`codex exec`)의 로그인 세션을 쓴다.
엔진이 부르는 에이전트는 사용자 설정 · hook · MCP · 도구를 끈 상태로 빈 임시 폴더에서
실행해서 사용자 환경과 섞이지 않게 한다.
"""

import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from context_holder.tools import tool

AGENTS = {"claude": "Claude", "codex": "Codex"}  # 설정 값 → 화면 이름
AGENT_TOOLS = {"claude": "Claude Code", "codex": "Codex CLI"}  # 설치하는 명령줄 도구 이름


class ErrorKind(StrEnum):
    NOT_INSTALLED = "not_installed"  # 에이전트 명령이 없다
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

ENV_NOT_FOUND = 127  # `/usr/bin/env`가 명령을 찾지 못했다 (계정 폴더를 넘길 때)
AUTH_HINTS = ("login", "log in", "sign in", "api key", "unauthorized", "oauth", "access token")
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
        executable: str | None = None,  # 없으면 찾는다 (앱은 PATH가 짧다)
        timeout: float = 300.0,
        prefix: Sequence[str] = (),  # 계정 설정 폴더 (agentaccounts.command_prefix)
    ):
        self.model = model
        self.runner = runner
        self.executable = executable or tool("claude")
        self.timeout = timeout
        self.prefix = list(prefix)

    def structured(self, prompt: str, system: str, schema: dict[str, Any]) -> LlmResult:
        args = [
            *self.prefix,
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
            run = self.runner(args, prompt, self.timeout, workdir)
        except FileNotFoundError as e:
            raise LlmError(ErrorKind.NOT_INSTALLED, "Claude Code가 설치돼 있지 않다") from e
        except subprocess.TimeoutExpired as e:
            raise LlmError(ErrorKind.TIMEOUT, f"{self.timeout:.0f}초 안에 끝나지 않았다") from e
        if self.prefix and run.returncode == ENV_NOT_FOUND:
            raise LlmError(ErrorKind.NOT_INSTALLED, "Claude Code가 설치돼 있지 않다")
        return run


class CodexCli:
    """`codex exec --json --output-schema`. 답은 마지막 agent_message의 JSON 문자열이다."""

    def __init__(
        self,
        model: str | None = None,  # 없으면 Codex 기본 모델
        runner: Runner = subprocess_runner,
        executable: str | None = None,
        timeout: float = 300.0,
        prefix: Sequence[str] = (),
    ):
        self.model = model
        self.runner = runner
        self.executable = executable or tool("codex")
        self.timeout = timeout
        self.prefix = list(prefix)

    def structured(self, prompt: str, system: str, schema: dict[str, Any]) -> LlmResult:
        with tempfile.TemporaryDirectory(prefix="ctx-codex-") as workdir:
            schema_file = Path(workdir) / "schema.json"
            schema_file.write_text(json.dumps(strict_schema(schema)), encoding="utf-8")
            args = [
                *self.prefix,
                self.executable,
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-user-config",  # 사용자 MCP · 프로필을 끈다. 로그인은 그대로 쓴다
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--cd",
                workdir,
                "--output-schema",
                str(schema_file),
                *(["--model", self.model] if self.model else []),
                "-",
            ]
            # exec에는 시스템 프롬프트 옵션이 없어 지시를 프롬프트 앞에 붙인다
            started = time.monotonic()
            run = self._run(args, f"{system}\n\n---\n\n{prompt}", workdir)
        return _parse_codex(run, int((time.monotonic() - started) * 1000))

    def _run(self, args: list[str], prompt: str, workdir: str) -> Run:
        try:
            run = self.runner(args, prompt, self.timeout, workdir)
        except FileNotFoundError as e:
            raise LlmError(ErrorKind.NOT_INSTALLED, "Codex CLI가 설치돼 있지 않다") from e
        except subprocess.TimeoutExpired as e:
            raise LlmError(ErrorKind.TIMEOUT, f"{self.timeout:.0f}초 안에 끝나지 않았다") from e
        if self.prefix and run.returncode == ENV_NOT_FOUND:
            raise LlmError(ErrorKind.NOT_INSTALLED, "Codex CLI가 설치돼 있지 않다")
        return run


class StructuredLlm(Protocol):
    def structured(self, prompt: str, system: str, schema: dict[str, Any]) -> LlmResult: ...


def make_llm(agent: str, model: str | None = None, prefix: Sequence[str] = ()) -> StructuredLlm:
    if agent == "claude":
        return ClaudeCli(model=model or "sonnet", prefix=prefix)
    if agent == "codex":
        return CodexCli(model=model, prefix=prefix)
    raise ValueError(f"모르는 에이전트: {agent}")


def strict_schema(schema: Any) -> Any:
    """OpenAI strict 모드용 사본: 속성이 있는 객체마다 additionalProperties: false."""
    if isinstance(schema, list):
        return [strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    strict = {key: strict_schema(value) for key, value in schema.items()}
    if strict.get("type") == "object" and "properties" in strict:
        strict.setdefault("additionalProperties", False)
    return strict


def _parse_codex(run: Run, duration_ms: int) -> LlmResult:
    answer, failure = None, None
    for line in run.stdout.splitlines():
        event = _json_body(line)
        if event is None:
            continue
        kind = event.get("type")
        item = event.get("item") or {}
        if kind == "item.completed" and item.get("type") == "agent_message":
            answer = str(item.get("text") or "")
        elif kind == "turn.failed":
            failure = str((event.get("error") or {}).get("message") or "")
        elif kind == "error" and failure is None:
            failure = str(event.get("message") or "")
    if failure is not None or (run.returncode != 0 and answer is None):
        text = failure or run.stderr or run.stdout
        raise LlmError(_kind_from_text(text), _snippet(text))
    if answer is None:
        raise LlmError(ErrorKind.BAD_OUTPUT, "agent_message가 없다")
    data = _json_body(answer)
    if data is None:
        raise LlmError(ErrorKind.BAD_OUTPUT, f"JSON이 아닌 답: {_snippet(answer)}")
    return LlmResult(data=data, cost_usd=0.0, duration_ms=duration_ms)


def _parse(run: Run) -> LlmResult:
    body = _json_body(run.stdout)
    if run.returncode != 0 and body is None:
        text = run.stderr or run.stdout
        raise LlmError(_kind_from_text(text), _snippet(text))
    if body is None:
        raise LlmError(ErrorKind.BAD_OUTPUT, f"JSON이 아닌 출력: {_snippet(run.stdout)}")
    if run.returncode != 0 or body.get("is_error"):
        # 실패 응답도 JSON이다. 필드 이름(credential_expires_at 등)이 아니라 내용으로 판단한다
        message = str(body.get("result") or body.get("subtype") or "알 수 없는 오류")
        raise LlmError(_kind_from_body(body, message), _snippet(message))
    data = body.get("structured_output")
    if not isinstance(data, dict):
        raise LlmError(ErrorKind.BAD_OUTPUT, "structured_output이 없다")
    return LlmResult(
        data=data,
        cost_usd=float(body.get("total_cost_usd") or 0.0),
        duration_ms=int(body.get("duration_ms") or 0),
    )


def _json_body(stdout: str) -> dict[str, Any] | None:
    try:
        body = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _kind_from_body(body: dict[str, Any], message: str) -> ErrorKind:
    status = body.get("api_error_status")
    if status == 429:
        return ErrorKind.LIMIT
    if status in (401, 403):
        return ErrorKind.AUTH
    return _kind_from_text(message)


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
