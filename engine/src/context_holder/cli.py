"""`ctx` 명령의 진입점."""

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import typer

from context_holder import __version__
from context_holder.collect import CollectResult, collect
from context_holder.sources import SourceConfigError, load_default_sources, load_sources

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
    typer.echo(f"{'소스':<14}{'feed':>6}{'새 글':>6}{'건너뜀':>7}{'실패':>6}  비고")
    for r in result.reports:
        note = r.error or ("변경 없음" if r.not_modified else "")
        typer.echo(f"{r.source_id:<14}{r.found:>6}{r.new:>6}{r.skipped:>7}{r.failed:>6}  {note}")
    typer.echo(f"\n새 글 {len(result.articles)}건")


def _write_jsonl(result: CollectResult, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for article in result.articles:
            row = asdict(article)
            row["published"] = article.published.isoformat() if article.published else None
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
