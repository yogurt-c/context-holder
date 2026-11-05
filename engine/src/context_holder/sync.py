"""`ctx sync`: 요약 대기 글 재처리 → 수집 → 분류 → 요약 → 위키 쓰기 (plan 5장).

Git 동기화(pull · 수집 잠금 · push)는 이 단계 밖에서 감싼다.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from context_holder.classify import Bucket, StructuredLlm, classify
from context_holder.collect import SourceReport, collect, fetch_article
from context_holder.feed import FetchError
from context_holder.pipeline import Budget
from context_holder.wiki import Wiki

log = logging.getLogger(__name__)

FIRST_SYNC_DAYS = 30  # 처음에는 한 달치를 모아 첫 브리프를 채운다
DAILY_SYNC_DAYS = 7  # 며칠 수집을 못 해도 따라잡을 수 있게 넉넉히 본다


@dataclass(frozen=True)
class SyncReport:
    sources: tuple[SourceReport, ...]
    saved: int
    inboxed: int
    dropped: int
    pending_classify: int  # 분류 실패. 본 글로 기록하지 않아 다음 수집 때 다시 모은다
    pending_enrich: int  # 요약 실패. 요약 대기로 써 두고 다음 실행에서 다시 만든다
    completed: int  # 이전 요약 대기 글 중 이번에 완료한 글
    still_pending: int  # 이전 요약 대기 글 중 이번에도 못 한 글
    cost_usd: float
    etags: dict[str, str]
    stopped_reason: str | None  # Claude 호출을 멈춘 이유 (로그인 · 한도 · 미설치)


def default_days(wiki: Wiki) -> int:
    return DAILY_SYNC_DAYS if wiki.seen_urls() else FIRST_SYNC_DAYS


def run_sync(
    wiki: Wiki,
    client: httpx.Client,
    llm: StructuredLlm,
    now: datetime,
    days: int,
    etags: Mapping[str, str] | None = None,
) -> SyncReport:
    wiki.ensure_config()
    sources = {s.id: s for s in wiki.sources()}
    profile = wiki.profile()
    state = Budget()

    completed, still_pending = _retry_pending(wiki, client, llm, sources, now, state)

    pending_urls = {p.url for p in wiki.pending()}
    collected = collect(
        client,
        sources.values(),
        since=now - timedelta(days=days),
        seen_urls=wiki.seen_urls() | pending_urls,
        etags=etags,
    )
    by_url = {a.url: a for a in collected.articles}

    if state.stopped:
        decisions, pending_classify = (), len(collected.articles)
    else:
        result = classify(llm, collected.articles, profile)
        state.note_classify(result)
        decisions, pending_classify = result.decisions, len(result.pending)

    pending_enrich = 0
    for decision in decisions:
        if decision.bucket is Bucket.DROP:
            continue
        article = by_url[decision.url]
        enrichment, reason = state.try_enrich(llm, article)
        wiki.write_article(article, decision, enrichment, collected=now, reason=reason)
        pending_enrich += enrichment is None
    wiki.record_seen(decisions, when=now)

    counts = {b: sum(d.bucket is b for d in decisions) for b in Bucket}
    return SyncReport(
        sources=collected.reports,
        saved=counts[Bucket.SAVE],
        inboxed=counts[Bucket.INBOX],
        dropped=counts[Bucket.DROP],
        pending_classify=pending_classify,
        pending_enrich=pending_enrich,
        completed=completed,
        still_pending=still_pending,
        cost_usd=state.cost,
        etags={**dict(etags or {}), **collected.etags},
        stopped_reason=state.stopped,
    )


def _retry_pending(wiki, client, llm, sources, now, state) -> tuple[int, int]:
    completed = still = 0
    for pending in wiki.pending():
        source = sources.get(pending.source_id)
        if source is None or state.stopped:
            still += 1
            continue
        try:
            article = fetch_article(client, source, pending.url, pending.title, pending.published)
        except FetchError as e:
            log.warning("요약 대기 글 원문 실패 %s: %s", pending.url, e)
            still += 1
            continue
        enrichment, _ = state.try_enrich(llm, article)
        if enrichment is None:
            still += 1
            continue
        wiki.complete(pending, enrichment, collected=now)
        completed += 1
    return completed, still
