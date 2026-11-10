"""에이전트 계정: 시스템 기본값과 이 앱에서 추가한 계정들."""

from datetime import UTC, datetime

from context_holder import agentaccounts
from context_holder.agentaccounts import Account

NOW = datetime(2026, 11, 10, 9, 5, tzinfo=UTC)


def test_added_account_gets_its_own_config_folder(tmp_path):
    account = agentaccounts.create("codex", NOW, tmp_path)

    folder = tmp_path / "codex" / account
    assert folder.is_dir() and agentaccounts.valid(account)
    assert agentaccounts.command_prefix("codex", account, tmp_path) == [
        "/usr/bin/env",
        f"CODEX_HOME={folder}",
    ]
    claude = agentaccounts.create("claude", NOW, tmp_path)
    assert agentaccounts.command_prefix("claude", claude, tmp_path)[1].startswith(
        "CLAUDE_CONFIG_DIR="
    )


def test_system_and_missing_accounts_use_the_terminal_login(tmp_path):
    assert agentaccounts.command_prefix("codex", "system", tmp_path) == []
    assert agentaccounts.command_prefix("codex", "0badc0de", tmp_path) == []


def test_list_remember_and_remove(tmp_path):
    assert agentaccounts.list_accounts("claude", tmp_path) == []
    first = agentaccounts.create("claude", NOW, tmp_path)
    second = agentaccounts.create("claude", NOW.replace(hour=10), tmp_path)
    (tmp_path / "claude" / "not-an-account").mkdir()
    agentaccounts.remember("claude", first, "a@x.com", "max", "회사", tmp_path)

    listed = agentaccounts.list_accounts("claude", tmp_path)

    assert [a.id for a in listed] == [first, second]
    assert (listed[0].email, listed[0].plan, listed[0].org) == ("a@x.com", "max", "회사")
    agentaccounts.remove("claude", first, tmp_path)
    agentaccounts.remove("claude", "../../etc", tmp_path)  # 계정 id가 아니면 무시한다
    assert [a.id for a in agentaccounts.list_accounts("claude", tmp_path)] == [second]


def test_broken_meta_and_labels(tmp_path):
    account = agentaccounts.create("codex", NOW, tmp_path)
    (tmp_path / "codex" / account / "account.json").write_text("{broken")
    assert agentaccounts.list_accounts("codex", tmp_path) == [Account(account)]
    assert agentaccounts.added_label(Account("x", added="")) == ""
    label = agentaccounts.added_label(Account("x", added=NOW.isoformat()))
    assert label.startswith("11월 10일 ") and ("오전" in label or "오후" in label)


def test_valid_ids():
    assert agentaccounts.valid("system") and agentaccounts.valid("a1b2c3d4")
    assert not agentaccounts.valid("app") and not agentaccounts.valid("../x")


def test_default_root_is_under_app_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_HOLDER_HOME", str(tmp_path))
    assert agentaccounts.account_dir("claude", "a1b2c3d4") == (
        tmp_path / "accounts" / "claude" / "a1b2c3d4"
    )
