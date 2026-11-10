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


def test_codex_is_only_the_official_install(monkeypatch, tmp_path):
    # Homebrew · npm으로 깐 다른 codex가 PATH에 있어도 공식 설치 프로그램의 위치만 쓴다
    tools.tool.cache_clear()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(tools, "find_tool", lambda name: "/opt/homebrew/bin/codex")
    assert tools.tool("codex") == str(tmp_path / ".local" / "bin" / "codex")
    tools.tool.cache_clear()
