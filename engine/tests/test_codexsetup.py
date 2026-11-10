"""Codex CLI 상태 · 로그인 명령."""

import base64
import json

from context_holder import codexsetup
from context_holder.claudesetup import ClaudeStatus

CURRENT_HELP = "--json --output-schema <FILE> --ignore-user-config"  # 요즘 codex exec --help


class FakeRun:
    """codex 명령 흉내. 결과를 차례로 돌려준다. exec --help · --version은 따로 답한다."""

    def __init__(self, *results, help_text=CURRENT_HELP, version="codex-cli 0.167.0"):
        self.results = list(results)
        self.help_text = help_text
        self.version = version
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        if args[-2:] == ["exec", "--help"]:
            return 0, self.help_text
        if args[-1] == "--version":
            return 0, self.version
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
    assert run.calls == [["codex", "login", "status"], ["codex", "exec", "--help"]]
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
    assert run.calls[0] == [*prefix, "codex", "login", "status"]
    assert codexsetup.login_command("codex", prefix) == [*prefix, "codex", "login"]


def test_old_codex_without_summary_options_is_outdated(tmp_path):
    old = FakeRun(
        (0, "Logged in using ChatGPT"),
        help_text="--json --skip-git-repo-check",
        version="codex-cli 0.22.0",
    )
    assert codexsetup.status(old, lambda: "codex", tmp_path).outdated == "0.22.0"
    logged_out = FakeRun((1, "Not logged in"), help_text="--json", version="?")
    assert codexsetup.status(logged_out, lambda: "codex", tmp_path).outdated == "알 수 없는 버전"
    current = FakeRun((0, "Logged in using ChatGPT"))
    assert codexsetup.status(current, lambda: "codex", tmp_path).outdated == ""


class PortRun:
    """lsof · ps 흉내. holders: 포트를 잡은 pid → (uid, 명령). 끄면 목록에서 빠진다."""

    def __init__(self, holders, stubborn=False):
        self.holders = dict(holders)
        self.stubborn = stubborn
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        if args[0].endswith("lsof"):
            return (0, "\n".join(map(str, self.holders))) if self.holders else (1, "")
        pid = int(args[-1])
        uid, command = self.holders.get(pid, (None, ""))
        return (0, f"{uid} {command}") if uid is not None else (1, "")

    def kill(self, pid):
        if not self.stubborn:
            self.holders.pop(pid)


def test_free_port_does_nothing_when_nobody_listens():
    run = PortRun({})
    assert codexsetup.free_login_port(run, run.kill, lambda s: None, uid=501) == ""
    assert run.calls == [["/usr/sbin/lsof", "-nP", "-iTCP:1455", "-sTCP:LISTEN", "-t"]]


def test_free_port_stops_a_leftover_codex_login():
    run = PortRun({52378: (501, "/Users/me/.nvm/.../bin/codex login")})
    killed = []

    def kill(pid):
        killed.append(pid)
        run.kill(pid)

    assert codexsetup.free_login_port(run, kill, lambda s: None, uid=501) == ""
    assert killed == [52378]


def test_free_port_leaves_other_programs_and_users_alone():
    for holder in ((501, "/usr/bin/python3 -m http.server 1455"), (0, "/bin/codex login")):
        run = PortRun({7: holder})
        reason = codexsetup.free_login_port(run, run.kill, lambda s: None, uid=501)
        assert "다른 프로그램" in reason and 7 in run.holders


def test_free_port_gives_up_if_the_login_will_not_stop():
    run = PortRun({9: (501, "codex login")}, stubborn=True)
    naps = []
    reason = codexsetup.free_login_port(run, run.kill, naps.append, uid=501)
    assert "닫지 못했어요" in reason and len(naps) == 20
