"""첫 실행(init): 소스마다 최근 글을 정해진 개수 · 기간만큼 가져와 위키를 채운다.

1. 목록 찾기: 소스별로 최신 글 주소를 모아 대기열에 넣는다 (Claude를 쓰지 않는다).
2. 처리: 대기열을 20건씩 가져와 분류 · 요약 · 위키 쓰기를 한다.

진행 상태는 위키의 `state/init.json`에 남긴다.
한도에 걸리거나 중간에 멈추면 다음 실행에서 이어서 한다.
원문 본문은 상태 파일에 넣지 않는다.
"""

import json
import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

from context_holder.archive import list_candidates
from context_holder.classify import Bucket, StructuredLlm, classify
from context_holder.collect import Article, article_from_entry, fetch_article
from context_holder.dates import parse_date
from context_holder.embed import Embedder
from context_holder.extract import canonical_url
from context_holder.feed import FeedEntry, FetchError, fetch_feed
from context_holder.pipeline import Budget
from context_holder.sources import Source
from context_holder.vectorize import try_write_vectors
from context_holder.wiki import Wiki

BATCH_SIZE = 20
LIST_ATTEMPTS = 3  # 목록 찾기가 계속 실패하는 소스는 이만큼 시도하고 넘어간다
STATE_FILE = "state/init.json"


@dataclass(frozen=True)
class InitSettings:
    per_source: int = 50
    max_age_days: int = 365


@dataclass(frozen=True)
class InitResult:
    finished: bool
    saved: int  # 이번 실행에서 저장한 글
    inboxed: int
    dropped: int
    stopped_reason: str | None
    cost_usd: float


@dataclass(frozen=True)
class Progress:
    total: int
    remaining: int
    counts: dict[str, int]  # save · inbox · drop · old · failed · seen
    by_source: dict[str, dict[str, Any]] = field(default_factory=dict)
    stopped_reason: str | None = None
    finished: bool = False


ProgressCallback = Callable[[Progress], None]
DEFAULT_SETTINGS = InitSettings()


def load_state(wiki: Wiki) -> dict[str, Any]:
    path = wiki.root / STATE_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(wiki: Wiki, state: dict[str, Any]) -> None:
    path = wiki.root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)  # 쓰다가 멈춰도 상태 파일이 깨지지 않게


def progress(state: dict[str, Any]) -> Progress:
    items = state.get("items", [])
    counts = Counter(i["status"] if i["status"] != "done" else i["bucket"] for i in items)
    remaining = counts.pop("queued", 0)
    by_source: dict[str, dict[str, Any]] = {}
    for source_id, listed in state.get("listed", {}).items():
        mine = [i for i in items if i["source"] == source_id]
        by_source[source_id] = {
            "listed": listed["count"],
            "error": listed.get("error"),
            "remaining": sum(i["status"] == "queued" for i in mine),
            **Counter(i["bucket"] for i in mine if i["status"] == "done"),
        }
    return Progress(
        total=len(items),
        remaining=remaining,
        counts=dict(counts),
        by_source=by_source,
        stopped_reason=state.get("stopped"),
        finished=bool(state.get("finished")),
    )


def run_init(
    wiki: Wiki,
    client: httpx.Client,
    llm: StructuredLlm,
    now: datetime,
    settings: InitSettings = DEFAULT_SETTINGS,
    on_progress: ProgressCallback | None = None,
    embedder: Embedder | None = None,
) -> InitResult:
    wiki.ensure_config()
    state = load_state(wiki) or {
        "per_source": settings.per_source,
        "max_age_days": settings.max_age_days,
        "started": now.isoformat(),
        "listed": {},
        "items": [],
    }
    if state.get("finished"):
        return InitResult(True, 0, 0, 0, None, 0.0)
    state["stopped"] = None
    cutoff = now - timedelta(days=state["max_age_days"])
    sources = {s.id: s for s in wiki.sources()}

    _discover(client, wiki, state, sources.values(), cutoff)
    budget = Budget()
    done = Counter[Bucket]()
    while not budget.stopped:
        batch = _next_batch(state)
        if not batch:
            break
        done.update(
            _process(client, llm, wiki, state, sources, batch, cutoff, now, budget, embedder)
        )
        state["stopped"] = budget.stopped
        save_state(wiki, state)
        if on_progress:
            on_progress(progress(state))

    finished = (
        not budget.stopped
        and not _next_batch(state)
        and not any(_needs_listing(v) for v in state["listed"].values())
    )
    if finished:
        state["finished"] = now.isoformat()
    save_state(wiki, state)
    return InitResult(
        finished,
        done[Bucket.SAVE],
        done[Bucket.INBOX],
        done[Bucket.DROP],
        budget.stopped,
        budget.cost,
    )


def preview(
    client: httpx.Client, wiki: Wiki, now: datetime, settings: InitSettings = DEFAULT_SETTINGS
) -> dict[str, tuple[int, int, str | None]]:
    """목록만 찾아 소스별 (가져올 글 수, 그중 날짜를 아는 글 수, 오류)를 돌려준다.

    아무것도 쓰지 않는다.
    """
    wiki.ensure_config()
    cutoff = now - timedelta(days=settings.max_age_days)
    seen, queued = wiki.seen_urls(), set[str]()
    found = {}
    for source in wiki.sources():
        items, error = _list_source(client, source, settings.per_source, cutoff, seen, queued)
        found[source.id] = (len(items), sum(bool(i["published"]) for i in items), error)
    return found


def _discover(client, wiki: Wiki, state, sources, cutoff: datetime) -> None:
    seen = wiki.seen_urls()
    queued = {i["url"] for i in state["items"]}
    for source in sources:
        listed = state["listed"].get(source.id, {"count": 0, "error": None, "attempts": 0})
        if source.id in state["listed"] and not _needs_listing(listed):
            continue
        items, error = _list_source(client, source, state["per_source"], cutoff, seen, queued)
        state["items"].extend(items)
        state["listed"][source.id] = {
            "count": listed["count"] + len(items),
            "error": error,
            "attempts": listed.get("attempts", 0) + 1,
        }
        save_state(wiki, state)


def _list_source(client, source: Source, limit: int, cutoff: datetime, seen, queued: set[str]):
    items: list[dict[str, Any]] = []
    try:
        for candidate in list_candidates(client, source):
            if len(items) >= limit:
                break
            if candidate.published and candidate.published < cutoff:
                break  # 목록은 최신순이라 여기부터는 모두 오래된 글
            if candidate.url in seen or candidate.url in queued:
                continue
            queued.add(candidate.url)
            items.append(
                {
                    "url": candidate.url,
                    "source": source.id,
                    "title": candidate.title,
                    "published": candidate.published.isoformat() if candidate.published else None,
                    "status": "queued",
                }
            )
    except FetchError as e:
        return items, str(e)
    return items, None


def _needs_listing(listed: dict[str, Any]) -> bool:
    """목록 찾기가 실패했던 소스는 다음 실행에서 다시 시도한다."""
    return bool(listed["error"]) and listed.get("attempts", 0) < LIST_ATTEMPTS


def _next_batch(state) -> list[dict[str, Any]]:
    """소스를 번갈아 가며 고른다. 한 소스가 처리 시간을 독차지하지 않게."""
    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in state["items"]:
        if item["status"] == "queued":
            by_source.setdefault(item["source"], []).append(item)
    batch: list[dict[str, Any]] = []
    while len(batch) < BATCH_SIZE and any(by_source.values()):
        for queue in by_source.values():
            if queue and len(batch) < BATCH_SIZE:
                batch.append(queue.pop(0))
    return batch


def _process(client, llm, wiki: Wiki, state, sources, batch, cutoff, now, budget: Budget, embedder):
    seen = wiki.seen_urls()
    bodies = set(wiki.seen_hashes())
    feeds: dict[str, dict[str, FeedEntry]] = {}
    articles: dict[str, tuple[dict[str, Any], Article]] = {}
    for item in batch:
        source: Source | None = sources.get(item["source"])
        if item["url"] in seen:
            item["status"] = "seen"  # 매일 수집이 먼저 처리했다
            continue
        if source is None:
            item.update(status="failed", reason="소스 설정에서 빠졌다")
            continue
        try:
            article = _fetch(client, source, item, feeds)
        except FetchError as e:
            item.update(status="failed", reason=str(e))
            continue
        if article.published and article.published < cutoff:
            item["status"] = "old"
            continue
        if article.content_hash in bodies:
            item["status"] = (
                "duplicate"  # 같은 글이 다른 주소로 올라와 있다 (Toss 숫자 · 영문 주소 등)
            )
            continue
        bodies.add(article.content_hash)
        articles[article.url] = (item, article)
    if not articles:
        return []

    result = classify(llm, [a for _, a in articles.values()], wiki.profile())
    budget.note_classify(result)
    finished: list[Bucket] = []
    for decision in result.decisions:
        item, article = articles[decision.url]
        if decision.bucket is not Bucket.DROP:
            enrichment, reason = budget.try_enrich(llm, article)
            if enrichment is None and budget.stopped:
                continue  # 한도 · 로그인 문제면 대기열에 남겨 다음 실행에서 다시 한다
            origin = "init" if decision.bucket is Bucket.INBOX else None
            wiki.write_article(article, decision, enrichment, now, reason=reason, origin=origin)
            if enrichment:
                try_write_vectors(wiki, embedder, article)
        wiki.record_seen([decision], when=now, hashes={article.url: article.content_hash})
        item.update(status="done", bucket=decision.bucket.value)
        finished.append(decision.bucket)
    return finished


def _fetch(client, source: Source, item: dict[str, Any], feeds) -> Article:
    """feed만 쓰는 소스는 feed 본문을 쓴다. 원문 페이지를 막는 곳(Medium 등)이 있어서다."""
    if source.archive is None:
        if source.id not in feeds:
            entries = fetch_feed(client, source, etag=None).entries
            feeds[source.id] = {canonical_url(e.url): e for e in entries}
        entry = feeds[source.id].get(canonical_url(item["url"]))
        if entry is not None:
            return article_from_entry(client, source, entry)
    return fetch_article(client, source, item["url"], item["title"], parse_date(item["published"]))
