"""`ctx` 명령의 진입점."""

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import typer

from context_holder import __version__
from context_holder.classify import (
    Bucket,
    ClassifyResult,
    Decision,
    Profile,
    ProfileError,
    classify,
    load_default_profile,
    load_profile,
)
from context_holder.collect import Article, CollectResult, SourceReport, collect
from context_holder.evaluate import LabelError, Report, evaluate, load_labels, sweep_thresholds
from context_holder.llm import ClaudeCli
from context_holder.paths import default_wiki, etag_cache
from context_holder.sources import SourceConfigError, load_default_sources, load_sources
from context_holder.sync import SyncReport, default_days, run_sync
from context_holder.wiki import Wiki

app = typer.Typer(
    name="ctx",
    help="ctx: 빅테크 기술 결정 사례 위키를 수집하고 검색한다.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"ctx {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="버전을 출력하고 끝낸다.",
        callback=_print_version,
        is_eager=True,
    ),
) -> None:
    """ctx: 빅테크 기술 결정 사례 위키를 수집하고 검색한다."""


@app.command("collect")
def collect_command(
    sources_path: Path | None = typer.Option(
        None, "--sources", help="sources.yaml 경로. 없으면 기본 13곳."
    ),
    days: int = typer.Option(30, min=1, help="최근 며칠 안의 글만 모은다."),
    out: Path | None = typer.Option(None, help="모은 글을 JSONL로 저장할 파일."),
) -> None:
    """소스에서 새 글을 모아 소스별 결과를 보여 준다. 위키에는 쓰지 않는다."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    # trafilatura는 추출 못 한 페이지마다 ERROR를 남긴다. 실패는 소스별 결과로 보여 준다
    logging.getLogger("trafilatura").setLevel(logging.CRITICAL)
    try:
        sources = load_sources(sources_path) if sources_path else load_default_sources()
    except SourceConfigError as e:
        typer.echo(f"설정 오류: {e}", err=True)
        raise typer.Exit(code=2) from e

    since = datetime.now(UTC) - timedelta(days=days)
    with httpx.Client() as client:
        result = collect(client, sources, since=since)

    _print_report(result)
    if out:
        _write_jsonl(result, out)
        typer.echo(f"\n{len(result.articles)}건을 {out}에 저장했다.")


def _print_report(result: CollectResult) -> None:
    _print_sources(result.reports)
    typer.echo(f"\n새 글 {len(result.articles)}건")


def _print_sources(reports: tuple[SourceReport, ...]) -> None:
    typer.echo(f"{'소스':<14}{'feed':>6}{'새 글':>6}{'건너뜀':>7}{'실패':>6}  비고")
    for r in reports:
        note = r.error or ("변경 없음" if r.not_modified else "")
        typer.echo(f"{r.source_id:<14}{r.found:>6}{r.new:>6}{r.skipped:>7}{r.failed:>6}  {note}")


def _write_jsonl(result: CollectResult, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for article in result.articles:
            row = asdict(article)
            row["published"] = article.published.isoformat() if article.published else None
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


BUCKET_LABEL = {Bucket.SAVE: "저장", Bucket.INBOX: "보류", Bucket.DROP: "버림"}


@app.command("classify")
def classify_command(
    articles_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, help="ctx collect --out으로 저장한 JSONL."
    ),
    profile_path: Path | None = typer.Option(
        None, "--profile", help="profile.yaml 경로. 없으면 기본 기준."
    ),
    model: str = typer.Option("sonnet", help="분류에 쓸 Claude 모델."),
    out: Path | None = typer.Option(None, help="판정 결과를 JSONL로 저장할 파일."),
) -> None:
    """모은 글의 관련도를 Claude로 매겨 저장 · 보류 · 버림으로 나눈다."""
    profile = _profile_or_exit(profile_path)
    articles = _read_articles(articles_path)
    result = classify(ClaudeCli(model=model), articles, profile)
    _print_decisions(result, articles)
    if out:
        _write_decisions(result, articles, out)
        typer.echo(f"\n판정 결과를 {out}에 저장했다.")


def _read_articles(path: Path) -> list[Article]:
    articles = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        published = datetime.fromisoformat(row["published"]) if row.get("published") else None
        articles.append(Article(**{**row, "published": published}))
    return articles


def _print_decisions(result: ClassifyResult, articles: list[Article]) -> None:
    titles = {a.url: (a.source_id, a.title) for a in articles}
    for d in sorted(result.decisions, key=lambda d: -d.score):
        source, title = titles[d.url]
        typer.echo(f"{d.score:.2f}  {BUCKET_LABEL[d.bucket]}  {source:<12} {title[:60]}")
    for p in result.pending:
        source, title = titles[p.url]
        typer.echo(f"  -   대기  {source:<12} {title[:60]}  ({p.reason})")
    counts = {b: sum(d.bucket is b for d in result.decisions) for b in Bucket}
    typer.echo(
        f"\n저장 {counts[Bucket.SAVE]} · 보류 {counts[Bucket.INBOX]} · "
        f"버림 {counts[Bucket.DROP]} · 대기 {len(result.pending)}  "
        f"(사용량 환산 ${result.cost_usd:.3f})"
    )


def _write_decisions(result: ClassifyResult, articles: list[Article], out: Path) -> None:
    titles = {a.url: (a.source_id, a.title) for a in articles}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for d in result.decisions:
            source, title = titles[d.url]
            row = {**asdict(d), "source_id": source, "title": title}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        for p in result.pending:
            source, title = titles[p.url]
            row = {
                "url": p.url,
                "bucket": "pending",
                "reason": p.reason,
                "source_id": source,
                "title": title,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _profile_or_exit(path: Path | None) -> Profile:
    try:
        return load_profile(path) if path else load_default_profile()
    except ProfileError as e:
        typer.echo(f"설정 오류: {e}", err=True)
        raise typer.Exit(code=2) from e


@app.command("eval-classify")
def eval_classify_command(
    labels_path: Path = typer.Argument(..., exists=True, dir_okay=False, help="평가셋 JSONL."),
    articles_path: Path | None = typer.Option(
        None,
        "--articles",
        exists=True,
        dir_okay=False,
        help="ctx collect --out 파일. 새로 분류한다.",
    ),
    decisions_path: Path | None = typer.Option(
        None,
        "--decisions",
        exists=True,
        dir_okay=False,
        help="ctx classify --out 파일. 다시 분류하지 않는다.",
    ),
    profile_path: Path | None = typer.Option(None, "--profile", help="profile.yaml 경로."),
    model: str = typer.Option("sonnet", help="분류에 쓸 Claude 모델."),
) -> None:
    """평가셋 라벨과 분류기 판정을 비교해 정밀도, 놓친 글, 추천 경계값을 보여 준다."""
    try:
        labels = load_labels(labels_path)
    except LabelError as e:
        typer.echo(f"평가셋 오류: {e}", err=True)
        raise typer.Exit(code=2) from e

    if decisions_path:
        decisions = _read_decisions(decisions_path)
    elif articles_path:
        wanted = {lb.url for lb in labels}
        articles = [a for a in _read_articles(articles_path) if a.url in wanted]
        missing = wanted - {a.url for a in articles}
        if missing:
            typer.echo(
                f"본문이 없는 라벨 {len(missing)}건. 먼저 ctx collect로 받아 두세요.", err=True
            )
            raise typer.Exit(code=2)
        order = {lb.url: i for i, lb in enumerate(labels)}
        articles.sort(key=lambda a: order[a.url])
        result = classify(ClaudeCli(model=model), articles, _profile_or_exit(profile_path))
        decisions = list(result.decisions)
    else:
        typer.echo("--articles 또는 --decisions 중 하나가 필요하다.", err=True)
        raise typer.Exit(code=2)

    _print_eval_report(evaluate(labels, decisions))
    best = sweep_thresholds(labels, decisions)
    precision = f"{best.save_precision:.0%}" if best.save_precision is not None else "-"
    typer.echo(
        f"\n추천 경계값: 보류 {best.inbox_at:.2f}, 저장 {best.save_at:.2f} "
        f"(일치율 {best.exact:.0%}, 저장 정밀도 {precision}, 놓친 글 {best.missed})"
    )


def _read_decisions(path: Path) -> list[Decision]:
    decisions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("bucket") == "pending":
            continue
        decisions.append(
            Decision(
                url=row["url"],
                score=float(row["score"]),
                bucket=Bucket(row["bucket"]),
                topic=row.get("topic", ""),
                plus=tuple(row.get("plus", [])),
                minus=tuple(row.get("minus", [])),
            )
        )
    return decisions


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def _print_eval_report(report: Report) -> None:
    typer.echo(
        f"평가 {report.total}건 · 일치율 {_pct(report.exact)} · "
        f"저장 정밀도 {_pct(report.save_precision)} · 저장 재현율 {_pct(report.save_recall)} · "
        f"놓친 글 {len(report.missed)} · 판정 없음 {len(report.unjudged)}"
    )
    typer.echo("\n정답 \\ 판정     저장  보류  버림")
    for expected in Bucket:
        row = report.confusion[expected]
        typer.echo(
            f"{BUCKET_LABEL[expected]:<12}{row[Bucket.SAVE]:>5}{row[Bucket.INBOX]:>6}{row[Bucket.DROP]:>6}"
        )
    if report.mismatches:
        typer.echo("\n어긋난 글")
        for m in sorted(report.mismatches, key=lambda m: (m.expected, -m.score)):
            mark = "  ← 놓침" if m in report.missed else ""
            typer.echo(
                f"  정답 {BUCKET_LABEL[m.expected]} · 판정 {BUCKET_LABEL[m.got]} "
                f"{m.score:.2f}  {m.title[:50]}{mark}"
            )


@app.command("sync")
def sync_command(
    wiki_path: Path | None = typer.Option(
        None, "--wiki", help="위키 레포 폴더. 없으면 ~/.context-holder/wiki."
    ),
    days: int | None = typer.Option(
        None, min=1, help="최근 며칠의 글을 볼지. 없으면 처음 30일, 이후 7일."
    ),
    model: str = typer.Option("sonnet", help="분류 · 요약에 쓸 Claude 모델."),
) -> None:
    """요약 대기 글을 다시 처리하고, 새 글을 모아 분류 · 요약해 위키에 쓴다."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("trafilatura").setLevel(logging.CRITICAL)
    wiki = Wiki(wiki_path or default_wiki())
    cache = etag_cache()
    etags = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}

    with httpx.Client() as client:
        report = run_sync(
            wiki,
            client,
            ClaudeCli(model=model),
            now=datetime.now(UTC),
            days=days or default_days(wiki),
            etags=etags,
        )

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(report.etags, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_sync(report)


def _print_sync(report: SyncReport) -> None:
    _print_sources(report.sources)
    typer.echo("")
    typer.echo(
        f"저장 {report.saved} · 보류 {report.inboxed} · 버림 {report.dropped} · "
        f"분류 대기 {report.pending_classify} · 요약 대기 {report.pending_enrich} · "
        f"이전 대기 완료 {report.completed} · 남은 대기 {report.still_pending}  "
        f"(사용량 환산 ${report.cost_usd:.3f})"
    )
    if report.stopped_reason:
        typer.echo(f"Claude 호출을 멈췄다: {report.stopped_reason}. 다음 실행에서 이어서 한다.")
