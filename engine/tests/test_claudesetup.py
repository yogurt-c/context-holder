"""Claude Code 상태 · 설치 · 로그인 · MCP 등록 명령."""

import json
import sys

from context_holder import claudesetup


class FakeRun:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        return self.results.pop(0) if self.results else (0, "")


def test_status_states():
    logged = json.dumps({"loggedIn": True, "subscriptionType": "max", "email": "me@x"})
    assert claudesetup.status(FakeRun((0, logged)), lambda: "claude") == claudesetup.ClaudeStatus(
        True, True, "max"
    )
    out = claudesetup.status(FakeRun((1, json.dumps({"loggedIn": False}))), lambda: "claude")
    assert out == claudesetup.ClaudeStatus(True, False, "")
    assert claudesetup.status(FakeRun((127, "")), lambda: "claude").installed is False
    assert claudesetup.status(FakeRun((0, "oops")), lambda: "claude").logged_in is False


def test_install_and_login_commands():
    assert claudesetup.install_command() == [
        "/bin/bash",
        "-c",
        "curl -fsSL https://claude.ai/install.sh | bash",
    ]
    assert claudesetup.login_command("/x/claude") == ["/x/claude", "auth", "login", "--claudeai"]


def test_engine_command_dev_and_frozen(monkeypatch, tmp_path):
    exe = tmp_path / "python"
    monkeypatch.setattr(sys, "executable", str(exe))
    assert claudesetup.engine_command() == [str(exe), "-m", "context_holder", "mcp"]
    (tmp_path / "ctx").write_text("")
    assert claudesetup.engine_command() == [str(tmp_path / "ctx"), "mcp"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert claudesetup.engine_command() == [str(exe), "mcp"]


def test_registered_command_parses_mcp_get():
    out = "context-holder:\n  Scope: User config\n  Command: /a/ctx\n  Args: mcp\n  Environment:\n"
    assert claudesetup.registered_command(FakeRun((0, out)), "claude") == ["/a/ctx", "mcp"]
    assert claudesetup.registered_command(FakeRun((1, "No MCP server")), "claude") is None
    assert claudesetup.registered_command(FakeRun((0, "nothing")), "claude") is None


def test_register_replaces_existing(monkeypatch):
    monkeypatch.setattr(claudesetup, "engine_command", lambda: ["/a/ctx", "mcp"])
    run = FakeRun((0, ""), (0, "Added stdio MCP server\n"))
    assert claudesetup.register_mcp(run, "claude") == (True, "Added stdio MCP server")
    assert run.calls == [
        ["claude", "mcp", "remove", "context-holder", "--scope", "user"],
        ["claude", "mcp", "add", "context-holder", "--scope", "user", "--", "/a/ctx", "mcp"],
    ]
    assert claudesetup.mcp_preview() == "claude mcp add context-holder --scope user -- /a/ctx mcp"


def test_defaults_use_found_claude(monkeypatch):
    monkeypatch.setattr(claudesetup, "tool", lambda name: "/found/claude")
    run = FakeRun((1, ""), (0, ""), (0, ""))
    claudesetup.registered_command(run)
    claudesetup.register_mcp(run)
    assert all(call[0] == "/found/claude" for call in run.calls)
    assert claudesetup.status(FakeRun((127, ""))).installed is False


def test_translocated_engine_path(monkeypatch):
    monkeypatch.setattr(claudesetup, "engine_command", lambda: ["/a/ctx", "mcp"])
    assert not claudesetup.translocated()
    moved = "/private/var/folders/x/T/AppTranslocation/ABC/d/context-holder.app/Contents/MacOS/x"
    monkeypatch.setattr(claudesetup, "engine_command", lambda: [moved, "mcp"])
    assert claudesetup.translocated()
