"""Codex CLI 상태 · 로그인 명령."""

import base64
import json

from context_holder import codexsetup
from context_holder.claudesetup import ClaudeStatus


class FakeRun:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        return self.results.pop(0) if self.results else (0, "")


def jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


def write_auth(folder, claims: dict | None) -> None:
    tokens = {"id_token": jwt(claims), "access_token": "secret"} if claims else {}
    (folder / "auth.json").write_text(json.dumps({"tokens": tokens}), encoding="utf-8")


def test_status_states(tmp_path):
    write_auth(
        tmp_path,
        {"email": "me@x.com", "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}},
    )
    run = FakeRun((0, "Logged in using ChatGPT\n"))

    status = codexsetup.status(run, lambda: "codex", tmp_path)

    assert status == ClaudeStatus(True, True, "plus", "me@x.com")
    assert status.label == "me@x.com (plus)"
    assert run.calls == [["codex", "login", "status"]]
    assert codexsetup.status(FakeRun((1, "Not logged in")), lambda: "codex", tmp_path) == (
        ClaudeStatus(True, False)
    )
    assert codexsetup.status(FakeRun((127, "")), lambda: "codex", tmp_path).installed is False


def test_api_key_login_has_no_account(tmp_path):
    (tmp_path / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-x"}), encoding="utf-8")

    status = codexsetup.status(
        FakeRun((0, "Logged in using an API key - sk-***")), lambda: "codex", tmp_path
    )

    assert status == ClaudeStatus(True, True, "API 키")


def test_broken_auth_file_still_reports_login(tmp_path):
    (tmp_path / "auth.json").write_text("{broken", encoding="utf-8")
    status = codexsetup.status(FakeRun((0, "Logged in using ChatGPT")), lambda: "codex", tmp_path)
    assert status == ClaudeStatus(True, True, "ChatGPT")

    write_auth(tmp_path, None)
    (tmp_path / "auth.json").write_text(
        json.dumps({"tokens": {"id_token": "not-a-jwt"}}), encoding="utf-8"
    )
    status = codexsetup.status(FakeRun((0, "Logged in using ChatGPT")), lambda: "codex", tmp_path)
    assert status == ClaudeStatus(True, True, "ChatGPT")


def test_codex_home_follows_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert codexsetup.codex_home() == tmp_path
    monkeypatch.delenv("CODEX_HOME")
    assert codexsetup.codex_home().name == ".codex"


def test_login_command():
    assert codexsetup.login_command("/bin/codex") == ["/bin/codex", "login"]


def test_install_command_uses_official_script():
    assert codexsetup.install_command() == [
        "/bin/bash",
        "-c",
        "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
    ]


def test_registered_command_reads_codex_config(tmp_path):
    assert codexsetup.registered_command(tmp_path) is None
    (tmp_path / "config.toml").write_text(
        'model = "o3"\n\n[mcp_servers.context-holder]\ncommand = "/a/ctx"\nargs = ["mcp"]\n',
        encoding="utf-8",
    )
    assert codexsetup.registered_command(tmp_path) == ["/a/ctx", "mcp"]
    (tmp_path / "config.toml").write_text("[mcp_servers.other]\ncommand = 'x'\n")
    assert codexsetup.registered_command(tmp_path) is None
    (tmp_path / "config.toml").write_text("not = toml = at all")
    assert codexsetup.registered_command(tmp_path) is None


def test_register_replaces_existing(monkeypatch):
    monkeypatch.setattr(codexsetup, "engine_command", lambda: ["/a/ctx", "mcp"])
    run = FakeRun((0, ""), (0, "Added"))

    ok, out = codexsetup.register_mcp(run, codex="/bin/codex")

    assert ok and out == "Added"
    assert run.calls == [
        ["/bin/codex", "mcp", "remove", "context-holder"],
        ["/bin/codex", "mcp", "add", "context-holder", "--", "/a/ctx", "mcp"],
    ]
    assert not codexsetup.register_mcp(FakeRun((0, ""), (1, "boom")), codex="c")[0]


def test_status_and_login_use_the_account_folder(tmp_path):
    write_auth(tmp_path, {"email": "app@x.com"})
    prefix = ["/usr/bin/env", f"CODEX_HOME={tmp_path}"]
    run = FakeRun((0, "Logged in using ChatGPT"))
    status = codexsetup.status(run, lambda: "codex", tmp_path, prefix)
    assert status.account == "app@x.com"
    assert run.calls == [[*prefix, "codex", "login", "status"]]
    assert codexsetup.login_command("codex", prefix) == [*prefix, "codex", "login"]
