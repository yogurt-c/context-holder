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


def _write_jsonl(path, rows):
    import json

    # 빈 줄이 섞여 있어도 읽어야 한다
    path.write_text("\n\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path


def _labels(tmp_path):
    return _write_jsonl(
        tmp_path / "labels.jsonl",
        [
            {"url": "https://a/1", "title": "글 1", "source_id": "s", "label": "save"},
            {"url": "https://a/2", "title": "글 2", "source_id": "s", "label": "save"},
            {"url": "https://a/3", "title": "글 3", "source_id": "s", "label": "drop"},
        ],
    )


def test_eval_classify_scores_existing_decisions(tmp_path):
    decisions = _write_jsonl(
        tmp_path / "decisions.jsonl",
        [
            {
                "url": "https://a/1",
                "score": 0.9,
                "bucket": "save",
                "topic": "",
                "plus": [],
                "minus": [],
            },
            {
                "url": "https://a/2",
                "score": 0.2,
                "bucket": "drop",
                "topic": "",
                "plus": [],
                "minus": [],
            },
            {"url": "https://a/3", "bucket": "pending", "reason": "auth"},
        ],
    )

    result = runner.invoke(
        app, ["eval-classify", str(_labels(tmp_path)), "--decisions", str(decisions)]
    )

    assert result.exit_code == 0, result.output
    assert "저장 정밀도 100%" in result.output
    assert "놓친 글 1" in result.output and "글 2" in result.output
    assert "판정 없음 1" in result.output
    assert "추천 경계값" in result.output


def test_eval_classify_runs_classifier_on_collected_articles(monkeypatch, tmp_path):
    from context_holder import cli
    from context_holder.classify import Bucket, ClassifyResult, Decision

    articles = _write_jsonl(
        tmp_path / "articles.jsonl",
        [
            {
                "source_id": "s",
                "url": f"https://a/{i}",
                "title": f"글 {i}",
                "published": None,
                "markdown": "본문",
                "content_hash": f"h{i}",
            }
            for i in (1, 2, 3, 4)
        ],
    )
    seen = {}

    def fake_classify(llm, arts, profile, **_):
        seen["urls"] = [a.url for a in arts]
        return ClassifyResult(
            decisions=tuple(Decision(a.url, 0.9, Bucket.SAVE, "", (), ()) for a in arts),
            pending=(),
            cost_usd=0.0,
        )

    monkeypatch.setattr(cli, "classify", fake_classify)

    result = runner.invoke(
        app, ["eval-classify", str(_labels(tmp_path)), "--articles", str(articles)]
    )

    assert result.exit_code == 0, result.output
    assert seen["urls"] == ["https://a/1", "https://a/2", "https://a/3"]  # 라벨 있는 글만
    assert "저장 정밀도 67%" in result.output


def test_eval_classify_needs_articles_or_decisions(tmp_path):
    result = runner.invoke(app, ["eval-classify", str(_labels(tmp_path))])

    assert result.exit_code == 2
    assert "--articles" in result.output


def test_eval_classify_reports_missing_articles(tmp_path):
    articles = _write_jsonl(tmp_path / "articles.jsonl", [])

    result = runner.invoke(
        app, ["eval-classify", str(_labels(tmp_path)), "--articles", str(articles)]
    )

    assert result.exit_code == 2
    assert "본문이 없는 라벨 3건" in result.output


def test_eval_classify_reports_bad_labels(tmp_path):
    bad = _write_jsonl(tmp_path / "labels.jsonl", [{"url": "https://a/1"}])

    result = runner.invoke(app, ["eval-classify", str(bad), "--decisions", str(bad)])

    assert result.exit_code == 2
    assert "title" in result.output


def test_sync_runs_on_wiki_and_keeps_etags_on_this_device(monkeypatch, tmp_path):
    import json

    from context_holder import cli
    from context_holder.collect import SourceReport
    from context_holder.sync import SyncReport

    home = tmp_path / "home"
    monkeypatch.setenv("CONTEXT_HOLDER_HOME", str(home))
    home.mkdir()
    (home / "etag-cache.json").write_text('{"toss": "\\"old\\""}', encoding="utf-8")
    seen = {}

    def fake_run_sync(wiki, client, llm, now, days, etags=None):
        seen.update(root=wiki.root, days=days, etags=dict(etags), model=llm.model)
        return SyncReport(
            sources=(SourceReport("toss", found=3, new=2),),
            saved=1,
            inboxed=1,
            dropped=0,
            pending_classify=0,
            pending_enrich=1,
            completed=2,
            still_pending=0,
            cost_usd=0.1,
            etags={"toss": '"new"'},
            stopped_reason="limit: 한도",
        )

    monkeypatch.setattr(cli, "run_sync", fake_run_sync)
    wiki = tmp_path / "wiki"

    result = runner.invoke(app, ["sync", "--wiki", str(wiki), "--days", "3", "--model", "haiku"])

    assert result.exit_code == 0, result.output
    assert seen == {"root": wiki, "days": 3, "etags": {"toss": '"old"'}, "model": "haiku"}
    assert "저장 1 · 보류 1 · 버림 0" in result.output
    assert "새 글 0건" not in result.output
    assert "요약 대기 1" in result.output and "이전 대기 완료 2" in result.output
    assert "Claude 호출을 멈췄다: limit: 한도" in result.output
    assert json.loads((home / "etag-cache.json").read_text()) == {"toss": '"new"'}


def test_sync_defaults_to_wiki_in_home_and_first_run_window(monkeypatch, tmp_path):
    from context_holder import cli
    from context_holder.sync import FIRST_SYNC_DAYS, SyncReport

    monkeypatch.setenv("CONTEXT_HOLDER_HOME", str(tmp_path))
    seen = {}

    def fake_run_sync(wiki, client, llm, now, days, etags=None):
        seen.update(root=wiki.root, days=days)
        return SyncReport((), 0, 0, 0, 0, 0, 0, 0, 0.0, {}, None)

    monkeypatch.setattr(cli, "run_sync", fake_run_sync)

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0, result.output
    assert seen == {"root": tmp_path / "wiki", "days": FIRST_SYNC_DAYS}


def _init_state(tmp_path):
    from context_holder.init import save_state
    from context_holder.wiki import Wiki

    wiki = Wiki(tmp_path / "wiki")
    save_state(
        wiki,
        {
            "per_source": 50,
            "max_age_days": 365,
            "started": "2026-11-05T00:00:00+00:00",
            "listed": {
                "toss": {"count": 3, "error": None},
                "kakao": {"count": 0, "error": "HTTP 500"},
            },
            "items": [
                {"url": "a", "source": "toss", "status": "done", "bucket": "save"},
                {"url": "b", "source": "toss", "status": "done", "bucket": "drop"},
                {"url": "c", "source": "toss", "status": "queued"},
            ],
            "stopped": "limit: 한도",
        },
    )
    return wiki


def test_init_status_shows_progress_by_source(tmp_path):
    _init_state(tmp_path)

    result = runner.invoke(app, ["init", "--wiki", str(tmp_path / "wiki"), "--status"])

    assert result.exit_code == 0, result.output
    assert "2 / 3" in result.output
    assert "toss" in result.output and "HTTP 500" in result.output
    assert "멈춘 이유: limit: 한도" in result.output


def test_init_runs_and_prints_progress(monkeypatch, tmp_path):
    from context_holder import cli
    from context_holder.init import InitResult, Progress

    seen = {}

    def fake_run_init(wiki, client, llm, now, settings, on_progress):
        seen.update(per_source=settings.per_source, days=settings.max_age_days, model=llm.model)
        on_progress(Progress(total=40, remaining=20, counts={"save": 12, "drop": 8}))
        return InitResult(False, 12, 0, 8, "limit: 한도", 0.4)

    monkeypatch.setattr(cli, "run_init", fake_run_init)
    monkeypatch.setenv("CONTEXT_HOLDER_HOME", str(tmp_path))

    result = runner.invoke(app, ["init", "--per-source", "30", "--days", "180", "--model", "haiku"])

    assert result.exit_code == 0, result.output
    assert seen == {"per_source": 30, "days": 180, "model": "haiku"}
    assert "20 / 40" in result.output and "저장 12" in result.output and "남은 20" in result.output
    assert "멈췄다: limit: 한도" in result.output


def test_init_reports_completion(monkeypatch, tmp_path):
    from context_holder import cli
    from context_holder.init import InitResult

    monkeypatch.setattr(cli, "run_init", lambda *a, **k: InitResult(True, 0, 0, 0, None, 0.0))

    result = runner.invoke(app, ["init", "--wiki", str(tmp_path / "w")])

    assert result.exit_code == 0, result.output
    assert "초기 수집을 마쳤다" in result.output


def test_init_status_without_state(tmp_path):
    result = runner.invoke(app, ["init", "--wiki", str(tmp_path / "w"), "--status"])

    assert result.exit_code == 0
    assert "아직 시작하지 않았다" in result.output


def test_init_dry_run_prints_counts(monkeypatch, tmp_path):
    from context_holder import cli

    monkeypatch.setattr(
        cli,
        "preview",
        lambda client, wiki, now, settings: {"toss": (50, 0, None), "kakao": (0, 0, "HTTP 500")},
    )

    result = runner.invoke(app, ["init", "--wiki", str(tmp_path / "w"), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "toss" in result.output and "50" in result.output
    assert "HTTP 500" in result.output and "합계 50" in result.output
