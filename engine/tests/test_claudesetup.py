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
    status = claudesetup.status(FakeRun((0, logged)), lambda: "claude")
    assert status == claudesetup.ClaudeStatus(True, True, "max", "me@x")
    assert status.label == "me@x (max)"
    team = json.dumps(
        {"loggedIn": True, "subscriptionType": "team", "email": "a@b", "orgName": "회사"}
    )
    assert claudesetup.status(FakeRun((0, team)), lambda: "claude").label == "a@b · 회사 (team)"
    assert claudesetup.ClaudeStatus(True, True, "max").label == "max"  # 이메일을 모르면 요금제만
    assert claudesetup.ClaudeStatus(True, True).label == ""
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


def test_registered_command_reads_claude_config(tmp_path, monkeypatch):
    config = tmp_path / ".claude.json"
    servers = {"mcpServers": {"context-holder": {"command": "/a/ctx", "args": ["mcp"]}}}
    config.write_text(json.dumps(servers))
    assert claudesetup.registered_command(config) == ["/a/ctx", "mcp"]
    config.write_text(json.dumps({"mcpServers": {"other": {}}}))
    assert claudesetup.registered_command(config) is None
    config.write_text("{broken")
    assert claudesetup.registered_command(config) is None
    assert claudesetup.registered_command(tmp_path / "none.json") is None


def test_claude_config_location(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert claudesetup.claude_config() == tmp_path / ".claude.json"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert claudesetup.claude_config() == tmp_path / "home" / ".claude.json"
    assert claudesetup.registered_command() is None  # 기본 위치를 읽는다


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
    run = FakeRun((0, ""), (0, ""))
    claudesetup.register_mcp(run)
    assert all(call[0] == "/found/claude" for call in run.calls)
    assert claudesetup.status(FakeRun((127, ""))).installed is False


def test_translocated_engine_path(monkeypatch):
    monkeypatch.setattr(claudesetup, "engine_command", lambda: ["/a/ctx", "mcp"])
    assert not claudesetup.translocated()
    moved = "/private/var/folders/x/T/AppTranslocation/ABC/d/context-holder.app/Contents/MacOS/x"
    monkeypatch.setattr(claudesetup, "engine_command", lambda: [moved, "mcp"])
    assert claudesetup.translocated()


def test_status_and_login_use_the_account_folder():
    prefix = ["/usr/bin/env", "CLAUDE_CONFIG_DIR=/acc/claude"]
    run = FakeRun((0, json.dumps({"loggedIn": True})))
    assert claudesetup.status(run, lambda: "claude", prefix).logged_in
    assert run.calls == [[*prefix, "claude", "auth", "status", "--json"]]
    assert claudesetup.login_command("claude", prefix) == [
        *prefix,
        "claude",
        "auth",
        "login",
        "--claudeai",
    ]
