import json
import subprocess
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from context_holder import chat
from context_holder.chat import ClaudeChat, CodexChat, Event, Turn
from context_holder.llm import ErrorKind, LlmError
from context_holder.screens.app import COOKIE, create_app
from context_holder.wiki import Wiki
from tests.test_screens import SOURCES, TOKEN, add, context

HTMX = {"HX-Request": "true"}


class FakeProc:
    def __init__(self, lines, code=0, stderr="", timeout=False):
        self._lines, self.code, self.stderr, self.timeout = lines, code, stderr, timeout
        self.killed = False

    def lines(self):
        yield from self._lines

    def finish(self):
        if self.timeout:
            raise subprocess.TimeoutExpired("claude", 1)
        return self.code, self.stderr

    def kill(self):
        self.killed = True


class FakeSpawner:
    def __init__(self, proc):
        self.proc = proc
        self.calls = []

    def __call__(self, args, stdin, cwd):
        self.calls.append({"args": args, "stdin": stdin, "cwd": cwd})
        if isinstance(self.proc, Exception):
            raise self.proc
        return self.proc


def delta(text: str) -> str:
    event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
    return json.dumps({"type": "stream_event", "event": event})


def test_claude_chat_streams_text_deltas_isolated():
    start = json.dumps({"type": "stream_event", "event": {"type": "message_start"}})
    proc = FakeProc(
        ['{"type":"system"}', start, delta("안"), delta("녕"), "깨진 줄", '{"type":"result"}']
    )
    spawner = FakeSpawner(proc)

    out = list(ClaudeChat(spawner=spawner, executable="claude").stream("지시", "질문"))

    assert out == [Event("text", "안"), Event("text", "녕")]
    [call] = spawner.calls
    args = call["args"]
    assert args[:2] == ["claude", "-p"]
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert "--include-partial-messages" in args and "--verbose" in args
    assert args[args.index("--tools") + 1] == "" and "--strict-mcp-config" in args
    assert args[args.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in args
    assert args[args.index("--system-prompt") + 1] == "지시"
    assert call["stdin"] == "질문" and call["cwd"]
    assert proc.killed  # 끝나면 늘 정리한다


@pytest.mark.parametrize(
    ("proc", "kind"),
    [
        (FakeProc(['{"type":"result","is_error":true,"result":"Please run /login"}'], 1), "auth"),
        (FakeProc([], 1, "usage limit reached"), "limit"),
        (FakeProc([], 2, "boom"), "failed"),
        (FakeProc([delta("a")], timeout=True), "timeout"),
    ],
)
def test_claude_chat_errors(proc, kind):
    with pytest.raises(LlmError) as e:
        list(ClaudeChat(spawner=FakeSpawner(proc), executable="claude").stream("s", "p"))
    assert e.value.kind == kind


def test_chat_not_installed():
    spawner = FakeSpawner(FileNotFoundError())
    with pytest.raises(LlmError) as e:
        list(ClaudeChat(spawner=spawner, executable="claude").stream("s", "p"))
    assert e.value.kind == ErrorKind.NOT_INSTALLED

    prefixed = ClaudeChat(spawner=FakeSpawner(FakeProc([], 127)), executable="c", prefix=["env"])
    with pytest.raises(LlmError) as e:
        list(prefixed.stream("s", "p"))
    assert e.value.kind == ErrorKind.NOT_INSTALLED


def test_codex_chat_sends_whole_answer():
    done = {"type": "item.completed", "item": {"type": "agent_message", "text": "답"}}
    spawner = FakeSpawner(FakeProc(["진행 로그", json.dumps(done)]))

    out = list(CodexChat(spawner=spawner, executable="codex").stream("지시", "질문"))

    assert out == [Event("text", "답")]
    [call] = spawner.calls
    assert call["args"][:2] == ["codex", "exec"] and "--ignore-user-config" in call["args"]
    assert call["stdin"].startswith("지시") and call["stdin"].endswith("질문")


def test_prompts_and_history():
    assert chat.conversation_prompt([], "왜?") == "왜?"
    prompt = chat.conversation_prompt([Turn("user", "뭐야"), Turn("assistant", "이거")], "왜?")
    assert "[사용자] 뭐야" in prompt and "[조수] 이거" in prompt and prompt.endswith("왜?")

    raw = [
        {"role": "user", "text": " 질문 "},
        {"role": "system", "text": "무시"},
        {"role": "assistant", "text": ""},
        "깨짐",
        {"role": "assistant", "text": "x" * 5000},
    ]
    turns = chat.parse_history(raw)
    assert turns[0] == Turn("user", "질문") and len(turns) == 2
    assert len(turns[1].text) == chat.MAX_CHARS
    assert chat.parse_history("아님") == []


class FakeChat:
    tools = False

    def __init__(self, parts, error=None):
        self.parts, self.error = parts, error
        self.calls = []

    def stream(self, system, prompt):
        self.calls.append((system, prompt))
        yield from self.parts
        if self.error:
            raise self.error


@pytest.fixture
def wiki(tmp_path) -> Wiki:
    wiki = Wiki(tmp_path / "wiki")
    wiki.ensure_config()
    (wiki.root / "config/sources.yaml").write_text(SOURCES, encoding="utf-8")
    return wiki


def make_client(wiki, tmp_path, fake) -> TestClient:
    ctx = replace(context(wiki, tmp_path), chat_llm=lambda: fake)
    c = TestClient(create_app(ctx), follow_redirects=False)
    c.cookies.set(COOKIE, TOKEN)
    return c


def events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_article_modal_has_chat(wiki, tmp_path):
    a = add(wiki, "https://t/a", "사이드카 버전 관리", topic="사이드카 관리", source="toss")
    c = make_client(wiki, tmp_path, FakeChat([]))

    modal = c.get(f"/article/{a}").text

    assert f'data-chat="/article/{a}/chat"' in modal and "Claude에게 이 글 묻기" in modal
    assert "data-chat-form" in modal and "트레이드오프" in modal
    assert "data-chat-open" in modal and "data-chat-scroll" in modal  # 입구 · 오른쪽 대화 창
    assert "data-chat-close" in modal


def test_article_chat_streams_answer_with_article_context(wiki, tmp_path):
    a = add(wiki, "https://t/a", "사이드카 버전 관리", topic="사이드카 관리", source="toss")
    fake = FakeChat(
        [
            Event("text", "사이드카는 "),
            Event("status", "원문 읽는 중", "원문"),
            Event("text", "이래요"),
        ]
    )
    c = make_client(wiki, tmp_path, fake)

    body = {"question": "왜 바꿨어?", "history": [{"role": "user", "text": "요약해 줘"}]}
    r = c.post(f"/article/{a}/chat", json=body, headers=HTMX)

    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    assert events(r) == [
        {"text": "사이드카는 "},
        {"status": "원문 읽는 중", "used": "원문"},
        {"text": "이래요"},
        {"done": True},
    ]
    [(system, prompt)] = fake.calls
    assert "사이드카 버전 관리" in system and "https://t/a" in system and "Toss" in system
    assert "사이드카 버전 관리 요약 첫 줄" in system and "SidecarSet" in system
    assert "[사용자] 요약해 줘" in prompt and prompt.endswith("왜 바꿨어?")


def test_article_chat_reports_errors(wiki, tmp_path):
    a = add(wiki, "https://t/a", "글")
    fake = FakeChat([Event("text", "반쯤")], LlmError(ErrorKind.AUTH, "login"))
    c = make_client(wiki, tmp_path, fake)

    r = c.post(f"/article/{a}/chat", json={"question": "왜?"}, headers=HTMX)
    assert events(r) == [{"text": "반쯤"}, {"error": chat.ERROR_MESSAGES[ErrorKind.AUTH]}]

    assert c.post(f"/article/{a}/chat", json={"question": " "}, headers=HTMX).status_code == 400
    assert c.post(f"/article/{a}/chat", content="x", headers=HTMX).status_code == 400
    assert c.post("/article/none/chat", json={"question": "왜?"}, headers=HTMX).status_code == 404
    assert (
        c.post(f"/article/{a}/chat", json={"question": "왜?"}).status_code == 403
    )  # htmx 표시 필요


def test_codex_chat_errors_and_make_chat():
    failed = json.dumps({"type": "turn.failed", "error": {"message": "rate limit"}})
    error = json.dumps({"type": "error", "message": "boom"})
    for lines, kind in (([failed, error], "limit"), ([error], "failed")):
        with pytest.raises(LlmError) as e:
            list(CodexChat(spawner=FakeSpawner(FakeProc(lines)), executable="codex").stream("", ""))
        assert e.value.kind == kind
    slow = CodexChat(spawner=FakeSpawner(FakeProc([], timeout=True)), executable="c", model="m")
    with pytest.raises(LlmError) as e:
        list(slow.stream("", ""))
    assert e.value.kind == "timeout"

    assert isinstance(chat.make_chat("claude", ["env"]), ClaudeChat)
    assert isinstance(chat.make_chat("codex"), CodexChat)
    with pytest.raises(ValueError):
        chat.make_chat("gpt")
    assert chat.error_message(LlmError(ErrorKind.BAD_OUTPUT, "x")) == chat.FALLBACK_ERROR


def test_popen_spawner_reads_lines_and_kills(tmp_path):
    import sys

    script = "import sys; d = sys.stdin.read(); print(d); print('끝'); sys.stderr.write('e')"
    proc = chat.popen_spawner(10)([sys.executable, "-c", script], "입력", str(tmp_path))
    assert [line.strip() for line in proc.lines()] == ["입력", "끝"]
    assert proc.finish() == (0, "e")
    proc.kill()  # 끝난 뒤에는 아무 일도 하지 않는다

    hang = chat.popen_spawner(10)(
        [sys.executable, "-c", "input(); import time; time.sleep(30)"], "\n", str(tmp_path)
    )
    hang.kill()
    assert hang.proc.returncode is not None


def test_system_prompt_without_decision(wiki, tmp_path):
    from pathlib import Path

    from context_holder.index import Index

    aid = add(wiki, "https://t/p", "그냥 글")
    index = Index(tmp_path / "i.db")
    index.sync(wiki)
    stored = wiki.read(Path(index.get(aid).path))
    stored = replace(stored, published=None)
    text = chat.system_prompt(stored, "NAVER D2")
    assert "그냥 글" in text and "## 결정 기록" not in text and "게시일" not in text


def test_cli_chat_uses_app_settings():
    from context_holder import cli

    llm = cli._chat_llm()
    assert isinstance(llm, ClaudeChat) and llm.tools  # 설정이 없으면 Claude · 시스템 계정
    assert llm.mcp_command[-2:] == ["mcp", "--original"]  # 이 엔진의 위키 MCP + 원문 읽기


def tool_start(name: str) -> str:
    block = {"type": "content_block_start", "content_block": {"type": "tool_use", "name": name}}
    return json.dumps({"type": "stream_event", "event": block})


def text_start() -> str:
    block = {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}
    return json.dumps({"type": "stream_event", "event": block})


def test_claude_chat_with_tools_allows_only_read_tools_and_reports_status():
    lines = [
        text_start(),
        delta("볼게요"),
        tool_start("mcp__context-holder__read_original"),
        tool_start("mcp__context-holder__find_decision_cases"),
        tool_start("Mystery"),
        text_start(),
        delta("답"),
    ]
    spawner = FakeSpawner(FakeProc(lines))
    llm = ClaudeChat(spawner=spawner, executable="claude", mcp_command=["/app/ctx", "mcp"])

    out = list(llm.stream("지시", "질문"))

    assert llm.tools
    assert out == [
        Event("text", "볼게요"),
        Event("status", "원문 읽는 중", "원문"),
        Event("status", "비슷한 결정 사례 찾는 중", "위키 결정 사례"),
        Event("status", "도구 쓰는 중", "도구"),
        Event("text", "\n\n"),  # 도구를 쓴 뒤 이어지는 답은 문단을 나눈다
        Event("text", "답"),
    ]
    args = spawner.calls[0]["args"]
    assert args[args.index("--tools") + 1] == "WebFetch"
    allowed = args[args.index("--allowedTools") + 1].split(",")
    assert "WebFetch" in allowed and "mcp__context-holder__search_wiki" in allowed
    assert "mcp__context-holder__read_original" in allowed
    assert not any("suggest_source" in a for a in allowed)  # 쓰기 도구는 허용하지 않는다
    assert not any(a.startswith(("Bash", "Edit", "Write")) for a in allowed)
    config = json.loads(args[args.index("--mcp-config") + 1])
    assert config == {"mcpServers": {"context-holder": {"command": "/app/ctx", "args": ["mcp"]}}}
    assert "--strict-mcp-config" in args


def test_codex_chat_with_tools_adds_live_search_and_wiki_mcp():
    started = [
        {"type": "item.started", "item": {"type": "web_search", "query": "q"}},
        {"type": "item.started", "item": {"type": "mcp_tool_call", "tool": "search_wiki"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "앞"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "뒤"}},
    ]
    spawner = FakeSpawner(FakeProc([json.dumps(e) for e in started]))
    llm = CodexChat(spawner=spawner, executable="codex", mcp_command=["/A B/ctx", "mcp"])

    out = list(llm.stream("", ""))

    assert out == [
        Event("status", "웹에서 찾는 중", "웹 검색"),
        Event("status", "위키 검색 중", "위키 검색"),
        Event("text", "앞"),
        Event("text", "\n\n"),
        Event("text", "뒤"),
    ]
    args = spawner.calls[0]["args"]
    overrides = [args[i + 1] for i, a in enumerate(args) if a == "-c"]
    assert overrides == [
        'web_search="live"',
        'mcp_servers.context-holder.command="/A B/ctx"',
        'mcp_servers.context-holder.args=["mcp"]',
    ]
    plain = CodexChat(spawner=FakeSpawner(FakeProc([])), executable="codex")
    list(plain.stream("", ""))
    assert not plain.tools
    assert 'web_search="disabled"' in plain.spawner.calls[0]["args"]


def test_system_prompt_tool_guide(wiki, tmp_path):
    from pathlib import Path

    from context_holder.index import Index

    aid = add(wiki, "https://t/p", "그냥 글")
    index = Index(tmp_path / "i.db")
    index.sync(wiki)
    stored = wiki.read(Path(index.get(aid).path))
    assert "## 도구" not in chat.system_prompt(stored, "D2")
    guided = chat.system_prompt(stored, "D2", tools=True)
    assert "## 도구" in guided and "도구를 쓰지 말고 바로 답한다" in guided


def test_article_chat_uses_tool_prompt_when_llm_has_tools(wiki, tmp_path):
    a = add(wiki, "https://t/a", "글")
    fake = FakeChat([Event("text", "답")])
    fake.tools = True
    c = make_client(wiki, tmp_path, fake)

    c.post(f"/article/{a}/chat", json={"question": "왜?"}, headers=HTMX)

    [(system, _)] = fake.calls
    assert "## 도구" in system
