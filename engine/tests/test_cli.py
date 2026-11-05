from typer.testing import CliRunner

from context_holder import __version__
from context_holder.cli import app

runner = CliRunner()


def test_help_lists_program_name():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "ctx" in result.output


def test_version_prints_package_version():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"ctx {__version__}"


def test_collect_prints_report_and_writes_jsonl(monkeypatch, tmp_path):
    import json
    from datetime import UTC, datetime

    from context_holder import cli
    from context_holder.collect import Article, CollectResult, SourceReport

    captured = {}

    def fake_collect(client, sources, since, **_):
        captured["since"] = since
        captured["ids"] = [s.id for s in sources]
        return CollectResult(
            articles=(
                Article(
                    "toss",
                    "https://toss.tech/a",
                    "글",
                    datetime(2026, 11, 5, tzinfo=UTC),
                    "본문",
                    "h",
                ),
            ),
            reports=(
                SourceReport("toss", found=3, new=1, skipped=2),
                SourceReport("kakao", error="HTTP 500"),
            ),
            etags={},
        )

    monkeypatch.setattr(cli, "collect", fake_collect)
    out = tmp_path / "articles.jsonl"

    result = runner.invoke(app, ["collect", "--days", "7", "--out", str(out)])

    assert result.exit_code == 0
    assert "toss" in result.output and "HTTP 500" in result.output
    assert len(captured["ids"]) == 13
    assert (datetime.now(UTC) - captured["since"]).days == 7
    [row] = [json.loads(line) for line in out.read_text().splitlines()]
    assert row["url"] == "https://toss.tech/a"
    assert row["published"] == "2026-11-05T00:00:00+00:00"


def test_collect_reports_bad_sources_file(tmp_path):
    bad = tmp_path / "sources.yaml"
    bad.write_text("nothing: here\n", encoding="utf-8")

    result = runner.invoke(app, ["collect", "--sources", str(bad)])

    assert result.exit_code == 2
    assert "sources" in result.output
