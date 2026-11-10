"""`ctx` 명령의 진입점."""

import json
import logging
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import typer

from context_holder import __version__, appsettings
from context_holder.auth import TokenMissing, github_token
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
from context_holder.collectrun import Checkpoint, LockedRun, Work, run_locked
from context_holder.connect import ConnectError, connect_wiki
from context_holder.device import device_name
from context_holder.embed import Embedder, FastEmbedder, model_spec
from context_holder.evaluate import LabelError, Report, evaluate, load_labels, sweep_thresholds
from context_holder.feedback import Kind, record_feedback
from context_holder.gitsync import GitSyncError, WikiRepo
from context_holder.index import Index
from context_holder.init import (
    InitResult,
    InitSettings,
    Progress,
    load_state,
    preview,
    progress,
    run_init,
)
from context_holder.llm import ClaudeCli
from context_holder.mcpeval import evaluate_mcp, load_decision_queries
from context_holder.mcpserver import build_server
from context_holder.mcptools import WikiTools
from context_holder.paths import default_wiki, etag_cache, home
from context_holder.reembed import run_reembed
from context_holder.runlock import INIT_LOCK, Outcome, RunLock, daily_lock
from context_holder.scheduler import Scheduler, run_forever, spawn_sync
from context_holder.screens.app import ScreenContext, new_token
from context_holder.screens.serve import serve
from context_holder.search import search
from context_holder.searcheval import evaluate_search, load_queries
from context_holder.setupflow import SetupFlow, model_downloaded
from context_holder.sources import SourceConfigError, load_default_sources, load_sources
from context_holder.sync import SyncReport, default_days, run_sync
from context_holder.tray import AppBridge, NoticeLog
from context_holder.updates import Updates
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
    events: bool = typer.Option(
        False, "--events", help="진행률을 JSON 한 줄씩 쓴다 (앱의 스케줄러가 읽는다)."
    ),
) -> None:
    """요약 대기 글을 다시 처리하고, 새 글을 모아 분류 · 요약해 위키에 쓴다."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("trafilatura").setLevel(logging.CRITICAL)
    wiki = Wiki(wiki_path or default_wiki())
    cache = etag_cache()
    etags = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}

    def work(checkpoint: Checkpoint | None = None) -> Work[SyncReport]:
        with httpx.Client() as client:
            report = run_sync(
                wiki,
                client,
                ClaudeCli(model=model),
                now=datetime.now(UTC),
                days=days or default_days(wiki),
                etags=etags,
                embedder=make_embedder(wiki.search_model()),
                checkpoint=checkpoint.tick if checkpoint else lambda: None,
                progress=_progress_line if events else lambda stage, done, total: None,
            )
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(report.etags, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_sync(report)
        return Work(report, _sync_summary(report), finished=report.stopped_reason is None)

    repo = _open_repo(wiki.root)
    if repo is None:  # git 레포가 아닌 로컬 폴더 (개발용)
        work()
        return
    now = datetime.now(UTC)
    _announce_model(wiki, events)
    _pull_and_index(repo, wiki)
    lock = RunLock(repo, daily_lock(now), device_name(home()), lambda: datetime.now(UTC))
    run = _run_locked(lock, work, blockers=[INIT_LOCK])
    if run.work:
        _index().sync(wiki, make_embedder(wiki.search_model()))


def _announce_model(wiki: Wiki, events: bool) -> None:
    """검색 모델을 처음 내려받을 차례면 앱에 알린다 (약 570MB라 몇 분 걸린다)."""
    if events and not model_downloaded(home() / "models", wiki.search_model()):
        _progress_line("model", 0, 0)


def _progress_line(stage: str, done: int, total: int) -> None:
    line = {"event": "progress", "stage": stage, "done": done, "total": total}
    typer.echo(json.dumps(line))


def _sync_summary(report: SyncReport) -> dict:
    return {
        "saved": report.saved,
        "inboxed": report.inboxed,
        "dropped": report.dropped,
        "pending": report.pending_enrich + report.still_pending,
        "sources": len(report.sources),  # 확인한 소스
        "failed_sources": sum(1 for s in report.sources if s.error),
        "cost_usd": round(report.cost_usd, 4),  # Claude 사용량 환산
        "by_source": {  # Sources 화면: 소스별 확인 결과
            s.source_id: {
                "found": s.found,
                "new": s.new,
                "failed": s.failed,
                "saved": report.saved_by_source.get(s.source_id, 0),
                "error": s.error,
            }
            for s in report.sources
        },
        # 멈춘 까닭의 종류만 남긴다 (auth · limit · not_installed). 자세한 메시지는 로그에 있다
        "stopped": report.stopped_reason.split(":")[0] if report.stopped_reason else None,
    }


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


STATUS_LABEL = {
    "save": "저장",
    "inbox": "보류",
    "drop": "버림",
    "old": "오래됨",
    "failed": "실패",
    "seen": "이미 수집",
    "duplicate": "중복",
}


@app.command("init")
def init_command(
    wiki_path: Path | None = typer.Option(
        None, "--wiki", help="위키 레포 폴더. 없으면 ~/.context-holder/wiki."
    ),
    per_source: int = typer.Option(50, "--per-source", min=1, help="소스마다 가져올 최대 글 수."),
    days: int = typer.Option(365, min=1, help="이 기간보다 오래된 글은 가져오지 않는다."),
    model: str = typer.Option("sonnet", help="분류 · 요약에 쓸 Claude 모델."),
    status: bool = typer.Option(False, "--status", help="진행 상황만 보여 준다."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Claude 없이 소스별로 가져올 글 수만 본다."
    ),
    events: bool = typer.Option(
        False, "--events", help="진행률을 JSON 한 줄씩 쓴다 (앱의 스케줄러가 읽는다)."
    ),
) -> None:
    """첫 실행: 소스마다 최근 글을 가져와 위키를 채운다. 멈추면 다시 실행해 이어서 한다."""
    wiki = Wiki(wiki_path or default_wiki())
    if status:
        _print_init_status(wiki)
        return
    settings = InitSettings(per_source=per_source, max_age_days=days)
    if dry_run:
        with httpx.Client() as client:
            found = preview(client, wiki, now=datetime.now(UTC), settings=settings)
        typer.echo(f"{'소스':<14}{'가져올 글':>8}{'날짜 앎':>8}  비고")
        for source_id, (count, dated, error) in found.items():
            typer.echo(f"{source_id:<14}{count:>8}{dated:>8}  {error or ''}")
        typer.echo(f"\n합계 {sum(c for c, _, _ in found.values())}")
        return

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("trafilatura").setLevel(logging.CRITICAL)
    before = progress(load_state(wiki)) if load_state(wiki) else None
    baseline = before.total - before.remaining if before else 0
    started = time.monotonic()

    def on_progress(p: Progress) -> None:
        processed = p.total - p.remaining
        if events:
            _progress_line("init", processed, p.total)
        line = f"[{processed} / {p.total}] {_counts(p.counts)} · 남은 {p.remaining}"
        done_now = processed - baseline
        if p.remaining and done_now > 0:
            eta = (time.monotonic() - started) / done_now * p.remaining
            line += f" · 예상 {eta / 60:.0f}분"
        typer.echo(line)

    def work(checkpoint: Checkpoint | None = None) -> Work[InitResult]:
        def on_batch(p: Progress) -> None:
            on_progress(p)
            if checkpoint:
                checkpoint.save()

        with httpx.Client() as client:
            result = run_init(
                wiki,
                client,
                ClaudeCli(model=model),
                now=datetime.now(UTC),
                settings=settings,
                on_progress=on_batch,
                embedder=make_embedder(wiki.search_model()),
            )
        if result.finished:
            typer.echo("초기 수집을 마쳤다.")
        elif result.stopped_reason:
            typer.echo(f"Claude 호출을 멈췄다: {result.stopped_reason}. 다시 실행하면 이어서 한다.")
        summary = {"saved": result.saved, "inboxed": result.inboxed, "dropped": result.dropped}
        return Work(result, summary, finished=result.finished)

    repo = _open_repo(wiki.root)
    if repo is None:
        work()
        return
    _announce_model(wiki, events)
    _pull_and_index(repo, wiki)
    lock = RunLock(repo, INIT_LOCK, device_name(home()), lambda: datetime.now(UTC))
    run = _run_locked(lock, work, blockers=[daily_lock(datetime.now(UTC))])
    if run.work:
        _index().sync(wiki, make_embedder(wiki.search_model()))


def _counts(counts: dict[str, int]) -> str:
    return " · ".join(f"{STATUS_LABEL[k]} {v}" for k, v in counts.items() if k in STATUS_LABEL)


def _print_init_status(wiki: Wiki) -> None:
    state = load_state(wiki)
    if not state:
        typer.echo("초기 수집을 아직 시작하지 않았다.")
        return
    p = progress(state)
    head = "완료" if p.finished else "진행 중"
    typer.echo(f"초기 수집 {head}: {p.total - p.remaining} / {p.total} · {_counts(p.counts)}")
    typer.echo(f"\n{'소스':<14}{'목록':>5}{'남음':>5}{'저장':>5}{'보류':>5}{'버림':>5}  비고")
    for source_id, row in p.by_source.items():
        typer.echo(
            f"{source_id:<14}{row['listed']:>5}{row['remaining']:>5}{row.get('save', 0):>5}"
            f"{row.get('inbox', 0):>5}{row.get('drop', 0):>5}  {row['error'] or ''}"
        )
    if p.stopped_reason:
        typer.echo(f"\n멈춘 이유: {p.stopped_reason}")


# ---------- Git 동기화 ----------

LOCK_MESSAGE = {
    Outcome.DONE: "오늘 수집은 이미 끝났다 ({device}). 받아 온 내용으로 인덱스만 갱신했다.",
    Outcome.BUSY: "다른 기기({device})가 수집하는 중이다. 받아 온 내용으로 인덱스만 갱신했다.",
}


def _open_repo(path: Path) -> WikiRepo | None:
    repo = WikiRepo.open(path)
    if repo is not None and repo.remote_url.startswith("https://"):
        _token()  # 로그인이 없으면 여기서 멈춘다
        repo.token = github_token  # push마다 다시 받는다 (만료 전 갱신)
    return repo


def _token() -> str:
    try:
        return github_token()
    except TokenMissing as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=2) from e


def _pull_and_index(repo: WikiRepo, wiki: Wiki) -> None:
    """이전 실행에서 남은 변경을 커밋하고 pull한 뒤, 바뀐 글만 다시 인덱싱한다."""
    repo.commit_all("이전 실행에서 남은 변경")
    try:
        result = repo.pull()
        if repo.ahead():
            repo.push()  # 오프라인일 때 쌓인 커밋
    except GitSyncError as e:
        typer.echo(f"위키를 동기화하지 못했다: {e}", err=True)
        raise typer.Exit(code=1) from e
    if result.dropped:
        typer.echo(f"원격과 겹친 로컬 변경 {result.dropped}건은 원격 내용을 따랐다.")
    updated, removed = _index().sync(wiki, make_embedder(wiki.search_model()))
    if updated or removed:
        typer.echo(f"인덱스 갱신 {updated}건, 삭제 {removed}건")


def _run_locked(lock: RunLock, work, blockers) -> LockedRun:
    try:
        run = run_locked(lock, work, blockers)
    except GitSyncError as e:
        typer.echo(f"수집 잠금을 잡지 못했다: {e}", err=True)
        raise typer.Exit(code=1) from e
    if run.status.outcome is not Outcome.ACQUIRED:
        holder = run.status.holder or {}
        typer.echo(LOCK_MESSAGE[run.status.outcome].format(device=holder.get("device", "?")))
    elif run.status.took_over:
        typer.echo(f"멈춘 수집({run.status.took_over})을 이어받았다.")
    if run.push_error:
        typer.echo(f"push하지 못했다. 다음 실행에서 다시 올린다: {run.push_error}", err=True)
    return run


@app.command("connect")
def connect_command(
    wiki_path: Path | None = typer.Option(None, "--wiki", help="위키를 받을 폴더."),
) -> None:
    """GitHub 계정에서 context-wiki를 찾아 받는다. 없으면 private으로 만든다."""
    path = wiki_path or default_wiki()
    _token()  # 로그인이 없으면 여기서 멈춘다
    try:
        done = connect_wiki(path, github_token)
    except ConnectError as e:
        typer.echo(str(e), err=True)
        code = 2 if "폴더" in str(e) or "다른 레포" in str(e) else 1
        raise typer.Exit(code=code) from e
    made = "만들었다" if done.created else "찾았다"
    typer.echo(f"{done.login}/context-wiki를 {made} (private). 위키 폴더: {path}")


@app.command("pull")
def pull_command(
    wiki_path: Path | None = typer.Option(None, "--wiki", help="위키 레포 폴더."),
) -> None:
    """다른 기기의 변경을 받아 바뀐 글만 다시 인덱싱한다. 수집은 하지 않는다."""
    wiki = Wiki(wiki_path or default_wiki())
    repo = _open_repo(wiki.root)
    if repo is None:
        typer.echo(f"{wiki.root}는 git 레포가 아니다. 먼저 ctx connect를 실행한다.", err=True)
        raise typer.Exit(code=2)
    _pull_and_index(repo, wiki)


@app.command("feedback")
def feedback_command(
    kind: Kind = typer.Argument(..., help="not_relevant(관련 없음) 또는 bookmark(북마크)."),
    url: str = typer.Argument(..., help="글 원문 주소."),
    wiki_path: Path | None = typer.Option(None, "--wiki", help="위키 레포 폴더."),
) -> None:
    """피드백을 이 기기의 피드백 파일에 남긴다."""
    wiki = Wiki(wiki_path or default_wiki())
    record_feedback(wiki, device_name(home()), kind, url, datetime.now(UTC))
    repo = _open_repo(wiki.root)
    if repo is None:
        return
    repo.commit_all(f"피드백: {kind.value}")
    try:
        repo.push()
    except GitSyncError as e:
        typer.echo(f"push하지 못했다. 다음 실행에서 다시 올린다: {e}", err=True)


@app.command("mcp")
def mcp_command(
    wiki_path: Path | None = typer.Option(None, "--wiki", help="위키 레포 폴더."),
) -> None:
    """Claude Code용 MCP 서버를 stdio로 띄운다 (claude mcp add context-holder -- ctx mcp)."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)  # stdout은 MCP 프로토콜 전용
    wiki = Wiki(wiki_path or default_wiki())
    tools = WikiTools(
        index=_index(),
        wiki=wiki,
        embedder=lambda: make_embedder(wiki.search_model(), local_only=True),
        device=device_name(home()),
        log_path=home() / "mcp-queries.jsonl",
        clock=lambda: datetime.now(UTC),
    )
    build_server(tools).run("stdio")  # pragma: no cover - stdio를 붙잡는다


def make_embedder(model_id: str, local_only: bool = False) -> Embedder:
    """검색 모델. 처음 쓸 때 ~/.context-holder/models에 내려받는다."""
    return FastEmbedder(model_id, home() / "models", local_only)  # pragma: no cover - 실제 모델


def _index() -> Index:
    return Index(home() / "index.db")


@app.command("serve")
def serve_command(
    wiki_path: Path | None = typer.Option(None, "--wiki", help="위키 레포 폴더."),
    open_browser: bool = typer.Option(False, "--open", help="토큰이 붙은 주소로 브라우저를 연다."),
    exit_on_stdin_eof: bool = typer.Option(
        False, "--exit-on-stdin-eof", help="stdin이 닫히면 끝낸다 (앱이 띄울 때)."
    ),
    app_mode: bool = typer.Option(
        False,
        "--app",
        help="앱이 띄울 때: 수집 스케줄러를 켜고 메뉴 · 알림 이벤트를 쓴다. stdin 명령을 받는다.",
    ),
) -> None:
    """화면 서버를 127.0.0.1의 임의 포트로 띄운다. 준비되면 주소와 토큰을 JSON 한 줄로 쓴다."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    wiki = Wiki(wiki_path or default_wiki())
    token = new_token()
    embedder = _shared_embedder(wiki)  # 인덱스 맞추기와 화면 검색이 모델 하나를 나눠 쓴다
    settings_path = home() / "app-state.json"
    flow = SetupFlow(wiki.root, settings_path)
    bridge, scheduler = _app_bridge(wiki, flow) if app_mode else (None, None)

    def make_context(port: int) -> ScreenContext:
        return ScreenContext(
            wiki=wiki,
            index=_index(),
            device=device_name(home()),
            mcp_log=home() / "mcp-queries.jsonl",
            clock=lambda: datetime.now(UTC),
            token=token,
            hosts=frozenset({f"127.0.0.1:{port}"}),
            embedder=embedder,
            scheduler=scheduler,
            flow=flow,
            settings_path=settings_path,
            app_mode=app_mode,
            model=lambda: (home() / "models", wiki.search_model()),
            download_model=lambda: _download_model(wiki),
            on_settings=bridge.apply_settings if bridge else lambda settings: None,
            updates=bridge.updates if bridge else None,
            regroup_llm=lambda: ClaudeCli(),
        )

    serve(
        make_context,
        out=sys.stdout,
        stdin=sys.stdin if exit_on_stdin_eof or app_mode else None,
        on_ready=_open_url if open_browser else lambda url: None,
        warm=lambda: _warm_index(wiki, embedder),
        on_line=bridge.command if bridge else lambda _: None,
        background=(
            (lambda: run_forever(scheduler, threading.Event(), bridge.tick))
            if bridge and scheduler
            else None
        ),
    )


def _app_bridge(wiki: Wiki, flow: SetupFlow) -> tuple[AppBridge, Scheduler]:
    """앱 모드: 메뉴 · 알림 이벤트를 stdout에 쓰고 매일 수집을 맡는다."""
    sync_log = home() / "logs" / "sync.log"
    sync_log.parent.mkdir(parents=True, exist_ok=True)
    sched_log = logging.getLogger("context_holder.scheduler")
    if not sched_log.handlers:  # 수집 기록은 ~/.context-holder/logs/sync.log에 남긴다
        handler = logging.FileHandler(sync_log, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        sched_log.addHandler(handler)
    sched_log.setLevel(logging.INFO)
    settings_path = home() / "app-state.json"
    bridge = AppBridge(
        sys.stdout,
        _index(),
        NoticeLog(home() / "notices.json"),
        settings=lambda: appsettings.load(settings_path),
    )
    scheduler = Scheduler(
        wiki,
        settings_path,
        device_name(home()),
        lambda: datetime.now(UTC),
        spawn=lambda: spawn_sync(wiki.root, sync_log),
        on_change=bridge.refresh,
        on_finish=bridge.check_notices,
        settings=lambda: appsettings.load(settings_path),
        ready=flow.ready,
    )
    bridge.scheduler = scheduler
    bridge.updates = Updates(
        bridge.emit, settings_path, lambda: datetime.now(UTC), on_change=bridge.refresh
    )
    flow.on_ready = lambda: scheduler.trigger("setup")  # 설정을 마치면 바로 첫 수집
    return bridge, scheduler


def _download_model(wiki: Wiki) -> None:  # pragma: no cover - 실제 모델 (약 570MB)
    make_embedder(wiki.search_model()).query("warm up")


def _shared_embedder(wiki: Wiki):
    """이 기기에 있는 검색 모델을 처음 부를 때 한 번만 불러온다. 내려받지 않는다."""
    lock = threading.Lock()
    loaded: list[Embedder] = []

    def get() -> Embedder:
        with lock:
            if not loaded:
                loaded.append(make_embedder(wiki.search_model(), local_only=True))
            return loaded[0]

    return get


def _warm_index(wiki: Wiki, embedder_of=None) -> None:
    """시작할 때 인덱스를 위키에 맞춘다. 검색 모델이 이 기기에 없으면 건드리지 않는다.

    모델 없이 sync하면 글마다 다시 색인하며 벡터를 지운다.
    """
    try:
        embedder = (embedder_of or _shared_embedder(wiki))()
        embedder.query("warm up")  # 모델 파일이 없으면 여기서 실패한다
    except Exception as e:  # 모델이 없거나 깨졌다: 화면은 지금 인덱스로 보여 준다
        logging.warning("검색 모델이 없어 인덱스를 맞추지 않았다: %s", e)
        return
    _index().sync(wiki, embedder)  # 화면 요청과 다른 연결로 쓴다 (WAL)


def _open_url(url: str) -> None:  # pragma: no cover - 브라우저를 연다
    import webbrowser

    webbrowser.open(url)


@app.command("search")
def search_command(
    query: str = typer.Argument(..., help="찾을 말."),
    wiki_path: Path | None = typer.Option(None, "--wiki", help="위키 레포 폴더."),
    limit: int = typer.Option(10, min=1, help="보여 줄 결과 수."),
    keyword_only: bool = typer.Option(False, "--keyword-only", help="의미 검색 없이 키워드로만."),
    saved_only: bool = typer.Option(False, "--saved-only", help="보류 글은 빼고 저장 글만."),
) -> None:
    """위키를 키워드와 의미로 함께 검색한다."""
    wiki = Wiki(wiki_path or default_wiki())
    embedder = None if keyword_only else make_embedder(wiki.search_model())
    index = _index()
    index.sync(wiki, embedder)
    buckets = (Bucket.SAVE,) if saved_only else (Bucket.SAVE, Bucket.INBOX)
    hits = search(index, query, embedder, limit=limit, buckets=buckets)
    if not hits:
        typer.echo("결과 없음")
        return
    for n, hit in enumerate(hits, 1):
        a = hit.article
        mark = "" if a.bucket is Bucket.SAVE else " [보류]"
        typer.echo(f"{n:>2}. {a.title}{mark}")
        typer.echo(f"    {a.source_id} · {hit.matched} · {a.url}")


@app.command("reindex")
def reindex_command(
    wiki_path: Path | None = typer.Option(None, "--wiki", help="위키 레포 폴더."),
    keyword_only: bool = typer.Option(False, "--keyword-only", help="벡터 없이 키워드 인덱스만."),
) -> None:
    """index.db를 지우고 위키에서 다시 만든다."""
    wiki = Wiki(wiki_path or default_wiki())
    path = home() / "index.db"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    embedder = None if keyword_only else make_embedder(wiki.search_model())
    updated, _ = _index().sync(wiki, embedder)
    typer.echo(f"글 {updated}건을 다시 색인했다.")


@app.command("reembed")
def reembed_command(
    wiki_path: Path | None = typer.Option(None, "--wiki", help="위키 레포 폴더."),
    models: list[str] = typer.Option(
        [], "--model", help="검색 모델 (여러 번 줄 수 있다). 없으면 지금 모델."
    ),
    no_switch: bool = typer.Option(
        False, "--no-switch", help="모델 설정을 바꾸지 않는다 (모델 비교용)."
    ),
) -> None:
    """원문을 다시 가져와 원문 단락 벡터를 만든다. 모델을 바꿀 때 쓴다."""
    wiki = Wiki(wiki_path or default_wiki())
    chosen = models or [wiki.search_model()]
    try:
        for model_id in chosen:
            model_spec(model_id)
    except KeyError as e:
        typer.echo(str(e.args[0]), err=True)
        raise typer.Exit(code=2) from e
    logging.getLogger("trafilatura").setLevel(logging.CRITICAL)

    def on_progress(p: tuple[int, int]) -> None:
        done, total = p
        if done % 20 == 0 or done == total:
            typer.echo(f"[{done} / {total}]")

    with httpx.Client() as client:
        result = run_reembed(
            wiki, client, [make_embedder(m) for m in chosen], on_progress, switch=not no_switch
        )
    typer.echo(f"벡터를 만든 글 {result.written}건, 원문 없음 {len(result.failed)}건")
    for f in result.failed:
        typer.echo(f"  원문 없음: {f.title} ({f.reason})")


@app.command("eval-search")
def eval_search_command(
    queries_path: Path = typer.Argument(..., exists=True, dir_okay=False, help="검색 평가셋."),
    wiki_path: Path | None = typer.Option(None, "--wiki", help="위키 레포 폴더."),
    models: str = typer.Option("minilm,e5-small,bge-m3", help="비교할 검색 모델 (쉼표로)."),
) -> None:
    """키워드만 쓸 때와 모델별 혼합 검색의 recall@5를 비교한다.

    원문 단락 벡터는 미리 ctx reembed --no-switch로 만들어 둔다.
    """

    wiki = Wiki(wiki_path or default_wiki())
    queries = load_queries(queries_path)
    kinds = sorted({q.kind for q in queries if q.expected})
    typer.echo(
        f"{'방식':<12}{'전체':>8}  "
        + "  ".join(f"{k:>10}" for k in kinds)
        + "  답있음1위  답없음1위"
    )
    for name in ["키워드만", *[m for m in models.split(",") if m]]:
        embedder = None if name == "키워드만" else make_embedder(name)
        with tempfile.TemporaryDirectory() as tmp:
            index = Index(Path(tmp) / "eval.db")
            index.sync(wiki, embedder)
            r = evaluate_search(index, queries, embedder)
        cols = "  ".join(f"{_ratio(*r.by_kind.get(k, (0, 0))):>10}" for k in kinds)
        typer.echo(
            f"{name:<12}{_ratio(r.hits, r.total):>8}  {cols}  {_median(r.answered_top):>9}  "
            f"{_max(r.unanswered_top):>9}"
        )


@app.command("eval-mcp")
def eval_mcp_command(
    queries_path: Path = typer.Argument(..., exists=True, dir_okay=False, help="결정 질문 평가셋."),
    wiki_path: Path | None = typer.Option(None, "--wiki", help="위키 레포 폴더."),
) -> None:
    """결정 질문을 MCP tool 경로 그대로 넣어 recall@5를 잰다. 위키 내용은 출력하지 않는다."""
    wiki = Wiki(wiki_path or default_wiki())
    queries = load_decision_queries(queries_path)
    embedder = make_embedder(wiki.search_model())
    index = _index()  # MCP가 실제로 읽는 인덱스. 바뀐 글만 다시 색인한다
    index.sync(wiki, embedder)
    with tempfile.TemporaryDirectory() as tmp:
        tools = WikiTools(
            index=index,
            wiki=wiki,
            embedder=lambda: embedder,
            device="eval",
            log_path=Path(tmp) / "queries.jsonl",  # 실제 질의 로그에 섞지 않는다
            clock=lambda: datetime.now(UTC),
        )
        scores = evaluate_mcp(tools, queries)
    for name, score in scores.items():
        typer.echo(f"{name:<34}{_ratio(score.hits, score.total):>8}")
        for miss in score.misses:
            typer.echo(f"  놓침: {miss}")


def _ratio(hits: int, total: int) -> str:
    return f"{hits}/{total}"


def _median(values: tuple[float, ...]) -> str:
    return f"{sorted(values)[len(values) // 2]:.2f}" if values else "-"


def _max(values: tuple[float, ...]) -> str:
    return f"{max(values):.2f}" if values else "-"
