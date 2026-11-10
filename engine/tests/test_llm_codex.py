import json
import subprocess
from pathlib import Path

import pytest

from context_holder.llm import (
    AGENTS,
    ClaudeCli,
    CodexCli,
    ErrorKind,
    LlmError,
    Run,
    make_llm,
    strict_schema,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
        "decision": {
            "anyOf": [
                {"type": "null"},
                {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
            ]
        },
    },
    "required": ["items", "decision"],
}


def events(*lines: dict) -> str:
    return "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n"


def success(text: str) -> str:
    return events(
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "생각"}},
        {"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    )


class FakeRunner:
    def __init__(self, run: Run | Exception):
        self.run = run
        self.calls: list[dict] = []

    def __call__(self, args, stdin, timeout, cwd):
        schema_path = args[args.index("--output-schema") + 1]
        self.calls.append(
            {
                "args": args,
                "stdin": stdin,
                "timeout": timeout,
                "cwd": cwd,
                "schema": json.loads(Path(schema_path).read_text(encoding="utf-8")),
            }
        )
        if isinstance(self.run, Exception):
            raise self.run
        return self.run


def test_structured_call_isolates_codex_and_returns_data():
    runner = FakeRunner(Run(0, success('{"items": [{"id": "a"}], "decision": null}'), "noise"))
    cli = CodexCli(model="gpt-5-codex", runner=runner, executable="codex", timeout=90)

    result = cli.structured(prompt="글 목록", system="분류기", schema=SCHEMA)

    assert result.data == {"items": [{"id": "a"}], "decision": None}
    assert result.cost_usd == 0.0
    [call] = runner.calls
    args = call["args"]
    assert args[:2] == ["codex", "exec"]
    assert "--json" in args and "--ephemeral" in args and "--skip-git-repo-check" in args
    assert "--ignore-user-config" in args and "--ignore-rules" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[args.index("--cd") + 1] == call["cwd"]
    assert args[args.index("--model") + 1] == "gpt-5-codex"
    assert args[-1] == "-"  # 프롬프트는 stdin으로
    assert call["stdin"].startswith("분류기")
    assert call["stdin"].endswith("글 목록")
    assert call["schema"] == strict_schema(SCHEMA)
    assert call["timeout"] == 90


def test_model_flag_is_omitted_without_model():
    runner = FakeRunner(Run(0, success('{"items": [], "decision": null}'), ""))

    CodexCli(runner=runner, executable="codex").structured("p", "s", SCHEMA)

    assert "--model" not in runner.calls[0]["args"]


def test_strict_schema_closes_every_object_without_touching_the_original():
    original = json.loads(json.dumps(SCHEMA))

    strict = strict_schema(SCHEMA)

    assert original == SCHEMA
    assert strict["additionalProperties"] is False
    assert strict["properties"]["items"]["items"]["additionalProperties"] is False
    assert strict["properties"]["decision"]["anyOf"][1]["additionalProperties"] is False
    assert "additionalProperties" not in strict["properties"]["decision"]["anyOf"][0]


@pytest.mark.parametrize(
    ("run", "kind"),
    [
        (FileNotFoundError("codex"), ErrorKind.NOT_INSTALLED),
        (subprocess.TimeoutExpired("codex", 90), ErrorKind.TIMEOUT),
        (
            Run(
                1,
                events(
                    {"type": "turn.started"},
                    {
                        "type": "turn.failed",
                        "error": {
                            "message": "Your access token could not be refreshed. "
                            "Please log out and sign in again."
                        },
                    },
                ),
                "",
            ),
            ErrorKind.AUTH,
        ),
        (
            Run(1, events({"type": "error", "message": "You've hit your usage limit."}), ""),
            ErrorKind.LIMIT,
        ),
        (Run(1, "", "Not logged in. Run codex login"), ErrorKind.AUTH),
        (Run(1, "", "something exploded"), ErrorKind.FAILED),
        (Run(0, events({"type": "turn.completed", "usage": {}}), ""), ErrorKind.BAD_OUTPUT),
        (Run(0, success("not json"), ""), ErrorKind.BAD_OUTPUT),
        (Run(0, success("[1, 2]"), ""), ErrorKind.BAD_OUTPUT),
    ],
)
def test_failures_are_classified(run, kind):
    cli = CodexCli(runner=FakeRunner(run), executable="codex")

    with pytest.raises(LlmError) as excinfo:
        cli.structured(prompt="p", system="s", schema=SCHEMA)

    assert excinfo.value.kind is kind


def test_not_installed_message_names_codex():
    cli = CodexCli(runner=FakeRunner(FileNotFoundError("codex")), executable="codex")

    with pytest.raises(LlmError, match="Codex"):
        cli.structured("p", "s", SCHEMA)


def test_last_agent_message_wins():
    out = events(
        {"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": "{}"}},
        {
            "type": "item.completed",
            "item": {"id": "2", "type": "agent_message", "text": '{"items": [], "decision": null}'},
        },
        "not json line",
    )
    cli = CodexCli(runner=FakeRunner(Run(0, out, "")), executable="codex")

    assert cli.structured("p", "s", SCHEMA).data == {"items": [], "decision": None}


def test_make_llm_picks_the_agent():
    assert set(AGENTS) == {"claude", "codex"}
    claude = make_llm("claude")
    assert isinstance(claude, ClaudeCli) and claude.model == "sonnet"
    assert make_llm("claude", "opus").model == "opus"
    codex = make_llm("codex")
    assert isinstance(codex, CodexCli) and codex.model is None
    assert make_llm("codex", "o4").model == "o4"
    with pytest.raises(ValueError):
        make_llm("gemini")


def test_account_prefix_runs_agents_with_their_config_folder():
    runner = FakeRunner(Run(0, success('{"items": [], "decision": null}'), ""))
    prefix = ["/usr/bin/env", "CODEX_HOME=/acc/codex"]

    CodexCli(runner=runner, executable="codex", prefix=prefix).structured("p", "s", SCHEMA)

    assert runner.calls[0]["args"][:4] == [*prefix, "codex", "exec"]


@pytest.mark.parametrize("cls", [ClaudeCli, CodexCli])
def test_missing_command_behind_env_is_not_installed(cls):
    run = Run(127, "", "env: codex: No such file or directory")
    runner = FakeRunner(run) if cls is CodexCli else (lambda args, stdin, timeout, cwd: run)
    cli = cls(runner=runner, executable="x", prefix=["/usr/bin/env", "A=b"])

    with pytest.raises(LlmError) as excinfo:
        cli.structured("p", "s", SCHEMA)

    assert excinfo.value.kind is ErrorKind.NOT_INSTALLED


def test_make_llm_passes_the_account_prefix():
    prefix = ["/usr/bin/env", "CLAUDE_CONFIG_DIR=/acc/claude"]
    assert make_llm("claude", prefix=prefix).prefix == prefix
    assert make_llm("codex", prefix=prefix).prefix == prefix
    assert make_llm("codex").prefix == []
