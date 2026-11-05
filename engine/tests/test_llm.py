import json
import subprocess

import pytest

from context_holder.llm import ClaudeCli, ErrorKind, LlmError, Run

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def envelope(**overrides) -> str:
    body = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": {"ok": True},
        "total_cost_usd": 0.0123,
        "duration_ms": 4567,
    }
    body.update(overrides)
    return json.dumps(body)


class FakeRunner:
    def __init__(self, run: Run | Exception):
        self.run = run
        self.calls: list[dict] = []

    def __call__(self, args, stdin, timeout, cwd):
        self.calls.append({"args": args, "stdin": stdin, "timeout": timeout, "cwd": cwd})
        if isinstance(self.run, Exception):
            raise self.run
        return self.run


def test_structured_call_isolates_claude_and_returns_data():
    runner = FakeRunner(Run(0, envelope(), ""))
    cli = ClaudeCli(model="sonnet", runner=runner, timeout=90)

    result = cli.structured(prompt="글 목록", system="분류기", schema=SCHEMA)

    assert result.data == {"ok": True}
    assert result.cost_usd == pytest.approx(0.0123)
    [call] = runner.calls
    args = call["args"]
    assert args[:2] == ["claude", "-p"]
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    assert args[args.index("--system-prompt") + 1] == "분류기"
    assert call["stdin"] == "글 목록"
    assert call["timeout"] == 90
    assert call["cwd"]  # 프로젝트 CLAUDE.md를 읽지 않도록 빈 임시 폴더에서 실행


@pytest.mark.parametrize(
    ("run", "kind"),
    [
        (FileNotFoundError("claude"), ErrorKind.NOT_INSTALLED),
        (subprocess.TimeoutExpired("claude", 90), ErrorKind.TIMEOUT),
        (Run(1, "", "Invalid API key · Please run /login"), ErrorKind.AUTH),
        (Run(1, "", "something exploded"), ErrorKind.FAILED),
        (Run(0, "not json", ""), ErrorKind.BAD_OUTPUT),
        (Run(0, "[1, 2]", ""), ErrorKind.BAD_OUTPUT),
        (Run(0, envelope(is_error=True, subtype="error_max_turns"), ""), ErrorKind.FAILED),
        (Run(0, envelope(is_error=True, result="Usage limit reached"), ""), ErrorKind.LIMIT),
        (Run(0, envelope(structured_output=None), ""), ErrorKind.BAD_OUTPUT),
    ],
)
def test_failures_are_classified(run, kind):
    cli = ClaudeCli(runner=FakeRunner(run))

    with pytest.raises(LlmError) as excinfo:
        cli.structured(prompt="p", system="s", schema=SCHEMA)

    assert excinfo.value.kind is kind


def test_default_runner_executes_a_real_process(tmp_path):
    from context_holder.llm import subprocess_runner

    run = subprocess_runner(["cat"], stdin="안녕", timeout=5, cwd=str(tmp_path))

    assert run == Run(0, "안녕", "")
