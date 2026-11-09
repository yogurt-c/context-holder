"""외부 명령 위치 찾기 (앱은 PATH가 짧다)."""

from context_holder import tools


def test_path_first(tmp_path):
    assert tools.find_tool("claude", which=lambda n: "/usr/bin/claude") == "/usr/bin/claude"


def test_common_dirs_then_login_shell(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    (tmp_path / "gh").write_text("not executable")
    asked = []

    def shell(args):
        asked.append(args)
        return "zsh 안내문\n/Users/me/bin/gh"

    monkeypatch.setenv("SHELL", "/bin/zsh")
    found = tools.find_tool("claude", which=lambda n: None, dirs=(str(tmp_path),), shell=shell)
    assert found == str(exe) and asked == []
    found = tools.find_tool("gh", which=lambda n: None, dirs=(str(tmp_path),), shell=shell)
    assert found == "/Users/me/bin/gh"
    assert asked == [["/bin/zsh", "-lc", "command -v gh"]]


def test_not_found(monkeypatch):
    monkeypatch.delenv("SHELL", raising=False)
    seen = []

    def shell(args):
        seen.append(args[0])
        return "gh not found"

    assert tools.find_tool("gh", which=lambda n: None, dirs=(), shell=shell) is None
    assert tools.find_tool("gh", which=lambda n: None, dirs=(), shell=lambda a: None) is None
    assert seen == ["/bin/zsh"]


def test_tool_falls_back_to_name(monkeypatch):
    tools.tool.cache_clear()
    monkeypatch.setattr(tools, "find_tool", lambda name: None)
    assert tools.tool("nothing-here") == "nothing-here"
    tools.tool.cache_clear()
