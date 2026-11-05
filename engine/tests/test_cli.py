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


def test_classify_reads_jsonl_and_writes_decisions(monkeypatch, tmp_path):
    import json

    from context_holder import cli
    from context_holder.classify import Bucket, ClassifyResult, Decision, Pending

    src = tmp_path / "articles.jsonl"
    rows = [
        {
            "source_id": "toss",
            "url": f"https://toss.tech/{i}",
            "title": f"글 {i}",
            "published": "2026-11-05T00:00:00+00:00",
            "markdown": "본문",
            "content_hash": f"h{i}",
        }
        for i in (1, 2, 3)
    ]
    src.write_text("\n\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    seen = {}

    def fake_classify(llm, articles, profile, **_):
        seen["model"] = llm.model
        seen["titles"] = [a.title for a in articles]
        return ClassifyResult(
            decisions=(
                Decision("https://toss.tech/1", 0.9, Bucket.SAVE, "백엔드", ("DB",), ()),
                Decision("https://toss.tech/2", 0.5, Bucket.INBOX, "AI", (), ("임베딩",)),
            ),
            pending=(Pending("https://toss.tech/3", "auth: 로그인 만료"),),
            cost_usd=0.02,
        )

    monkeypatch.setattr(cli, "classify", fake_classify)
    out = tmp_path / "classified.jsonl"

    result = runner.invoke(app, ["classify", str(src), "--out", str(out), "--model", "haiku"])

    assert result.exit_code == 0, result.output
    assert seen == {"model": "haiku", "titles": ["글 1", "글 2", "글 3"]}
    assert "저장 1" in result.output and "보류 1" in result.output and "대기 1" in result.output
    written = [json.loads(line) for line in out.read_text().splitlines()]
    assert [(w["url"], w["bucket"]) for w in written] == [
        ("https://toss.tech/1", "save"),
        ("https://toss.tech/2", "inbox"),
        ("https://toss.tech/3", "pending"),
    ]
    assert written[0]["title"] == "글 1"


def test_classify_rejects_missing_input(tmp_path):
    result = runner.invoke(app, ["classify", str(tmp_path / "none.jsonl")])

    assert result.exit_code == 2


def test_classify_reports_bad_profile(tmp_path):
    articles = tmp_path / "a.jsonl"
    articles.write_text("\n", encoding="utf-8")
    profile = tmp_path / "profile.yaml"
    profile.write_text("include: []\n", encoding="utf-8")

    result = runner.invoke(app, ["classify", str(articles), "--profile", str(profile)])

    assert result.exit_code == 2
    assert "include" in result.output
