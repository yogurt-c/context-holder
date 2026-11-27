"""글 상세에서 그 글을 두고 에이전트와 나누는 대화.

대화 기록은 화면(모달)이 들고 있다가 매번 함께 보낸다. 모달을 닫으면 버린다.
엔진은 기록을 남기지 않고, 글 내용과 지난 대화를 프롬프트로 다시 만들어 에이전트를 부른다.
`llm.py`와 같이 사용자 설정 · hook · MCP · 도구를 끈 채 빈 임시 폴더에서 실행한다.
"""

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from context_holder.llm import (
    ENV_NOT_FOUND,
    ErrorKind,
    LlmError,
    _kind_from_text,
    _snippet,
)
from context_holder.tools import tool
from context_holder.wiki import StoredArticle

MAX_TURNS = 20  # 이보다 오래된 대화는 잘라서 보낸다
MAX_CHARS = 4000  # 한 마디 최대 길이

ERROR_MESSAGES = {
    ErrorKind.NOT_INSTALLED: "에이전트가 설치돼 있지 않아요. 설정에서 확인해 주세요.",
    ErrorKind.AUTH: "에이전트 로그인이 필요해요. 설정에서 다시 로그인해 주세요.",
    ErrorKind.LIMIT: "에이전트 사용 한도에 걸렸어요. 잠시 후 다시 물어봐 주세요.",
    ErrorKind.TIMEOUT: "답이 너무 오래 걸려 멈췄어요. 다시 물어봐 주세요.",
}
FALLBACK_ERROR = "답을 받지 못했어요. 다시 물어봐 주세요."

MCP_NAME = "context-holder"
WIKI_TOOLS = (
    "read_original",
    "find_decision_cases",
    "search_wiki",
    "get_article",
    "get_decision",
    "list_topics",
)
# 도구 이름 → (쓰는 동안 보일 상태, 답 아래 "참고" 칸에 남길 이름)
TOOL_STATUS = {
    "read_original": ("원문 읽는 중", "원문"),
    "WebFetch": ("웹 페이지 읽는 중", "웹 페이지"),
    "web_search": ("웹에서 찾는 중", "웹 검색"),
    "find_decision_cases": ("비슷한 결정 사례 찾는 중", "위키 결정 사례"),
    "search_wiki": ("위키 검색 중", "위키 검색"),
    "get_article": ("위키 글 읽는 중", "위키 글"),
    "get_decision": ("결정 기록 읽는 중", "위키 결정 기록"),
    "list_topics": ("결정 주제 보는 중", "위키 결정 주제"),
}
OTHER_TOOL = ("도구 쓰는 중", "도구")


@dataclass(frozen=True)
class Event:
    """에이전트가 흘려 보내는 것: 답 글자(text) 또는 도구를 쓰는 중이라는 알림(status)."""

    kind: Literal["text", "status"]
    text: str
    used: str = ""  # status일 때 답 아래에 남길 짧은 이름


def tool_event(name: str) -> Event:
    """도구 이름(`mcp__context-holder__search_wiki`처럼 접두사가 붙어도 된다)을 상태 알림으로."""
    short = name.rsplit("__", 1)[-1]
    status, used = TOOL_STATUS.get(short, OTHER_TOOL)
    return Event("status", status, used)


@dataclass(frozen=True)
class Turn:
    role: Literal["user", "assistant"]
    text: str


class Proc(Protocol):
    """실행 중인 에이전트. 표준 출력을 줄 단위로 읽는다."""

    def lines(self) -> Iterable[str]: ...
    def finish(self) -> tuple[int, str]: ...  # (종료 코드, 표준 오류)
    def kill(self) -> None: ...


Spawner = Callable[[list[str], str, str], Proc]  # (명령, 표준 입력, 작업 폴더)


class _Popen:
    def __init__(self, args: list[str], stdin: str, cwd: str, timeout: float):
        env = {**os.environ, "CONTEXT_HOLDER_INTERNAL": "1"}
        self.timeout = timeout
        self.proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
        )
        assert self.proc.stdin is not None
        self.proc.stdin.write(stdin)
        self.proc.stdin.close()

    def lines(self) -> Iterable[str]:
        assert self.proc.stdout is not None
        yield from self.proc.stdout

    def finish(self) -> tuple[int, str]:
        code = self.proc.wait(timeout=self.timeout)
        assert self.proc.stderr is not None
        return code, self.proc.stderr.read()

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def popen_spawner(timeout: float = 300.0) -> Spawner:
    return lambda args, stdin, cwd: _Popen(args, stdin, cwd, timeout)


class ChatLlm(Protocol):
    tools: bool  # 원문 읽기 · 위키 검색 도구를 쓸 수 있다

    def stream(self, system: str, prompt: str) -> Iterator[Event]: ...


def mcp_config(command: Sequence[str]) -> dict[str, Any]:
    return {"mcpServers": {MCP_NAME: {"command": command[0], "args": list(command[1:])}}}


class ClaudeChat:
    """`claude -p --output-format stream-json --include-partial-messages`로 글자를 흘려 받는다.

    mcp_command를 주면 원문 읽기(WebFetch)와 이 위키의 읽기 도구만 켜고 미리 허용한다
    (-p는 승인 창을 띄울 수 없다). 셸 · 파일 쓰기 · 수집 후보 남기기(suggest_source)는 끈 채다.
    """

    def __init__(
        self,
        model: str = "sonnet",
        spawner: Spawner | None = None,
        executable: str | None = None,
        prefix: Sequence[str] = (),
        mcp_command: Sequence[str] | None = None,
    ):
        self.model = model
        self.spawner = spawner or popen_spawner()
        self.executable = executable or tool("claude")
        self.prefix = list(prefix)
        self.mcp_command = list(mcp_command) if mcp_command else None
        self.tools = self.mcp_command is not None

    def _tool_args(self) -> list[str]:
        if self.mcp_command is None:
            return ["--tools", "", "--strict-mcp-config"]
        allowed = ["WebFetch", *(f"mcp__{MCP_NAME}__{name}" for name in WIKI_TOOLS)]
        return [
            "--tools",
            "WebFetch",
            "--allowedTools",
            ",".join(allowed),
            "--mcp-config",
            json.dumps(mcp_config(self.mcp_command), ensure_ascii=False),
            "--strict-mcp-config",
        ]

    def stream(self, system: str, prompt: str) -> Iterator[Event]:
        args = [
            *self.prefix,
            self.executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model",
            self.model,
            *self._tool_args(),
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--system-prompt",
            system,
        ]
        with tempfile.TemporaryDirectory(prefix="ctx-chat-") as workdir:
            proc = _spawn(self.spawner, args, prompt, workdir, "Claude Code")
            try:
                failure = None
                wrote, new_block = False, False
                for line in proc.lines():
                    event = _event(line)
                    if event is None:
                        continue
                    block = _claude_block_start(event)
                    if block.get("type") == "tool_use":
                        yield tool_event(str(block.get("name") or ""))
                    elif block.get("type") == "text":
                        new_block = wrote  # 도구를 쓴 뒤 이어지는 답은 문단을 나눈다
                    text = _claude_delta(event)
                    if text:
                        if new_block:
                            yield Event("text", "\n\n")
                            new_block = False
                        wrote = True
                        yield Event("text", text)
                    elif event.get("type") == "result" and event.get("is_error"):
                        failure = str(event.get("result") or event.get("subtype") or "")
                code, stderr = proc.finish()
            except subprocess.TimeoutExpired as e:
                raise LlmError(ErrorKind.TIMEOUT, "답이 너무 오래 걸린다") from e
            finally:
                proc.kill()
        _raise_if_failed(code, failure, stderr, "Claude Code", bool(self.prefix))


class CodexChat:
    """`codex exec --json`. Codex는 글자 단위로 흘리지 않아 답을 한 번에 보낸다.

    mcp_command를 주면 라이브 웹 검색(원문 읽기)과 이 위키 MCP를 `-c` 덮어쓰기로 붙인다.
    사용자 config.toml은 여전히 읽지 않는다 (--ignore-user-config).
    """

    def __init__(
        self,
        model: str | None = None,
        spawner: Spawner | None = None,
        executable: str | None = None,
        prefix: Sequence[str] = (),
        mcp_command: Sequence[str] | None = None,
    ):
        self.model = model
        self.spawner = spawner or popen_spawner()
        self.executable = executable or tool("codex")
        self.prefix = list(prefix)
        self.mcp_command = list(mcp_command) if mcp_command else None
        self.tools = self.mcp_command is not None

    def _tool_args(self) -> list[str]:
        if self.mcp_command is None:
            return ["-c", 'web_search="disabled"']
        command, *rest = self.mcp_command
        key = f"mcp_servers.{MCP_NAME}"
        return [
            "-c",
            'web_search="live"',
            "-c",
            f"{key}.command={json.dumps(command, ensure_ascii=False)}",
            "-c",
            f"{key}.args={json.dumps(rest, ensure_ascii=False)}",
        ]

    def stream(self, system: str, prompt: str) -> Iterator[Event]:
        with tempfile.TemporaryDirectory(prefix="ctx-chat-") as workdir:
            args = [
                *self.prefix,
                self.executable,
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--cd",
                workdir,
                *self._tool_args(),
                *(["--model", self.model] if self.model else []),
                "-",
            ]
            proc = _spawn(self.spawner, args, f"{system}\n\n---\n\n{prompt}", workdir, "Codex CLI")
            try:
                failure = None
                wrote = False
                for line in proc.lines():
                    event = _event(line)
                    if event is None:
                        continue
                    kind = event.get("type")
                    item = event.get("item") or {}
                    if kind == "item.started" and item.get("type") == "mcp_tool_call":
                        yield tool_event(str(item.get("tool") or ""))
                    elif kind == "item.started" and item.get("type") == "web_search":
                        yield tool_event("web_search")
                    elif kind == "item.completed" and item.get("type") == "agent_message":
                        if wrote:
                            yield Event("text", "\n\n")
                        wrote = True
                        yield Event("text", str(item.get("text") or ""))
                    elif kind == "turn.failed":
                        failure = str((event.get("error") or {}).get("message") or "")
                    elif kind == "error" and failure is None:
                        failure = str(event.get("message") or "")
                code, stderr = proc.finish()
            except subprocess.TimeoutExpired as e:
                raise LlmError(ErrorKind.TIMEOUT, "답이 너무 오래 걸린다") from e
            finally:
                proc.kill()
        _raise_if_failed(code, failure, stderr, "Codex CLI", bool(self.prefix))


def make_chat(
    agent: str, prefix: Sequence[str] = (), mcp_command: Sequence[str] | None = None
) -> ChatLlm:
    """mcp_command(이 엔진의 `mcp` 명령)를 주면 원문 읽기 · 위키 검색 도구를 켠다."""
    if agent == "claude":
        return ClaudeChat(prefix=prefix, mcp_command=mcp_command)
    if agent == "codex":
        return CodexChat(prefix=prefix, mcp_command=mcp_command)
    raise ValueError(f"모르는 에이전트: {agent}")


TOOL_GUIDE = (
    "",
    "## 도구",
    "아래 글 정보로 답할 수 있으면 도구를 쓰지 말고 바로 답한다. 도구는 답을 늦춘다.",
    "- 원문 읽기(read_original, 이 글의 id): 요약에 없는 세부(코드, 수치, 구현 방법, 인용)를"
    " 물을 때만 한 번 읽는다. 글 안의 다른 링크를 봐야 할 때만 웹 도구를 쓴다.",
    "- 위키 검색(find_decision_cases → search_wiki → get_article):"
    " 다른 회사 사례나 비교를 물을 때만 쓴다. 찾은 사례는 회사 이름과 원문 URL을 함께 밝힌다.",
    "- 원문이나 위키에서 가져온 내용이면 어디서 봤는지 짧게 밝힌다.",
)


def system_prompt(article: StoredArticle, company: str, tools: bool = False) -> str:
    """에이전트가 이 글을 두고 답하게 하는 지시. 글의 저장된 내용을 그대로 넣는다."""
    unknown = (
        "글 정보에 없는 내용은 필요하면 도구로 확인하고, 일반 지식으로 답하면 그렇게 밝힌다."
        if tools
        else "글에 없는 내용은 일반 지식이라고 밝히고, 원문을 확인해야 하면 그렇게 말한다."
    )
    parts = [
        "너는 기술 블로그 글 하나를 두고 사용자와 이야기하는 조수다.",
        "아래 글 정보(요약 · 핵심 내용 · 결정 기록)를 바탕으로 한국어로 짧고 정확하게 답한다.",
        unknown,
        "제목(#)이나 표는 쓰지 않는다. 짧은 문단과 '- ' 목록, `코드`, **강조** 정도만 쓴다.",
        *(TOOL_GUIDE if tools else ()),
        "",
        "## 글",
        f"제목: {article.title}",
        f"회사: {company}",
        f"원문: {article.url}",
        f"위키 id: {article.id}",
    ]
    if article.published:
        parts.append(f"게시일: {article.published.date().isoformat()}")
    if article.tags:
        parts.append(f"태그: {', '.join(article.tags)}")
    if article.summary:
        parts += ["", "## 요약", *(f"- {line}" for line in article.summary)]
    if article.key_points:
        parts += ["", "## 핵심 내용", *(f"- {line}" for line in article.key_points)]
    if article.decision:
        parts += ["", "## 결정 기록", json.dumps(article.decision, ensure_ascii=False, indent=1)]
    return "\n".join(parts)


def conversation_prompt(history: Sequence[Turn], question: str) -> str:
    """지난 대화와 이번 질문을 한 프롬프트로 엮는다."""
    recent = list(history)[-MAX_TURNS:]
    if not recent:
        return question
    lines = ["지금까지의 대화:"]
    for turn in recent:
        who = "사용자" if turn.role == "user" else "조수"
        lines.append(f"[{who}] {turn.text}")
    lines += ["", "이번 질문에 답해:", question]
    return "\n".join(lines)


def parse_history(raw: Any) -> list[Turn]:
    """화면이 보낸 기록. 모양이 맞지 않는 항목은 버리고, 너무 긴 말은 자른다."""
    if not isinstance(raw, list):
        return []
    turns = []
    for item in raw[-MAX_TURNS:]:
        if not isinstance(item, dict):
            continue
        role, text = item.get("role"), item.get("text")
        if role in ("user", "assistant") and isinstance(text, str) and text.strip():
            turns.append(Turn(role, text.strip()[:MAX_CHARS]))
    return turns


def error_message(error: LlmError) -> str:
    return ERROR_MESSAGES.get(error.kind, FALLBACK_ERROR)


def _spawn(spawner: Spawner, args: list[str], stdin: str, cwd: str, name: str) -> Proc:
    try:
        return spawner(args, stdin, cwd)
    except FileNotFoundError as e:
        raise LlmError(ErrorKind.NOT_INSTALLED, f"{name}가 설치돼 있지 않다") from e


def _raise_if_failed(
    code: int, failure: str | None, stderr: str, name: str, prefixed: bool
) -> None:
    if prefixed and code == ENV_NOT_FOUND:
        raise LlmError(ErrorKind.NOT_INSTALLED, f"{name}가 설치돼 있지 않다")
    if failure is not None or code != 0:
        text = failure or stderr or f"종료 코드 {code}"
        raise LlmError(_kind_from_text(text), _snippet(text))


def _event(line: str) -> dict[str, Any] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _claude_block_start(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("type") != "stream_event":
        return {}
    inner = event.get("event") or {}
    if inner.get("type") != "content_block_start":
        return {}
    return inner.get("content_block") or {}


def _claude_delta(event: dict[str, Any]) -> str:
    if event.get("type") != "stream_event":
        return ""
    inner = event.get("event") or {}
    delta = inner.get("delta") or {}
    if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
        return str(delta.get("text") or "")
    return ""
