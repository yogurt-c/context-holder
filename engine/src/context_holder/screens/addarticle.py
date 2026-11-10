"""URL 하나로 글을 추가한다. 구독과 수집 이력은 중복 판단에 쓰지 않는다."""

import logging
import re
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from context_holder.classify import Bucket, Decision
from context_holder.collect import fetch_article
from context_holder.enrich import enrich
from context_holder.extract import canonical_url
from context_holder.feed import FetchError
from context_holder.feedback import Kind, record_feedback
from context_holder.llm import AGENT_TOOLS, AGENTS, LlmError
from context_holder.screens.decisionsview import LLM_ERROR_DEFAULT, LLM_ERRORS
from context_holder.sources import Source
from context_holder.vectorize import try_write_vectors
from context_holder.wiki import Wiki

if TYPE_CHECKING:
    from context_holder.screens.app import ScreenContext

log = logging.getLogger(__name__)


def normalize_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
        if (
            parts.scheme not in ("https", "http")
            or not parts.hostname
            or parts.username
            or parts.password
            or any(c.isspace() for c in value.strip())
        ):
            raise ValueError
        _ = parts.port
        return canonical_url(value)
    except ValueError:
        raise ValueError("게시글의 http(s) URL을 입력해 주세요.") from None


def source_for(wiki: Wiki, url: str) -> Source:
    host = urlsplit(url).hostname
    candidates = [s for s in wiki.sources() if urlsplit(s.feed_url).hostname == host]
    if len(candidates) == 1:
        return candidates[0]
    for source in candidates:
        publication = urlsplit(source.feed_url).path.removeprefix("/feed/").strip("/")
        if publication and urlsplit(url).path.startswith(f"/{publication}/"):
            return source
    # 출처 표시에만 쓴다. sources.yaml에 구독을 추가하지 않는다.
    source_id = re.sub(r"[^a-z0-9.-]", "-", host or "manual").strip(".-") or "manual"
    return Source(source_id, host or "직접 추가", "overseas", "")


@dataclass
class AddArticleJob:
    ctx: "ScreenContext"
    write_lock: AbstractContextManager
    search_lock: AbstractContextManager
    agent: Callable[[], str]
    background: Callable = field(
        default=lambda work: threading.Thread(target=work, daemon=True).start()
    )
    state: str = "idle"
    url: str = ""
    message: str = ""
    article_id: str = ""
    _lock: AbstractContextManager = field(default_factory=threading.Lock)

    def start(self, value: str, busy: bool) -> None:
        with self._lock:
            if self.state == "running":
                return
            self.url, self.article_id = value, ""
            try:
                url = normalize_url(value)
            except ValueError as e:
                self.state, self.message = "error", str(e)
                return
            if busy:
                self.state, self.message = (
                    "error",
                    "수집이나 주제 정리가 끝난 뒤 다시 시도해 주세요.",
                )
                return
            self.url = url
            self.state, self.message = "running", "본문을 가져오는 중…"
        self.background(self._run)

    def _existing(self):
        return next((a for a in self.ctx.wiki.articles() if canonical_url(a.url) == self.url), None)

    def _keep_existing(self, article) -> None:
        if article.bucket is Bucket.INBOX:
            self.ctx.wiki.save_from_inbox(article.path, collected=self.ctx.clock(), origin="manual")
            record_feedback(
                self.ctx.wiki, self.ctx.device, Kind.SAVE, article.url, self.ctx.clock()
            )
            self.message = "위키에 추가했어요."
        else:
            self.message = "이미 위키에 있는 글이에요."
        self.article_id = article.id

    def _run(self) -> None:
        ctx = self.ctx
        article = None
        try:
            with self.write_lock:
                existing = self._existing()
                if existing:
                    self._keep_existing(existing)
            if not existing:
                ctx.wiki.ensure_config()
                with httpx.Client() as client:
                    article = fetch_article(client, source_for(ctx.wiki, self.url), self.url)
                self.message = "요약하는 중…"
                summary = enrich(ctx.regroup_llm(), article)
                self.message = "위키에 저장하는 중…"
                with self.write_lock:
                    existing = self._existing()
                    if existing:
                        self._keep_existing(existing)
                    else:
                        decision = Decision(self.url, 1.0, Bucket.SAVE, "직접 추가", (), ())
                        path = ctx.wiki.write_article(
                            article, decision, summary, ctx.clock(), origin="manual"
                        )
                        self.article_id = ctx.wiki.read(path).id
                        # 자동 수집에서는 재수집을 막되, 수동 추가는 이 기록을 읽지 않는다.
                        ctx.wiki.record_seen([decision], ctx.clock())
                        record_feedback(ctx.wiki, ctx.device, Kind.SAVE, self.url, ctx.clock())
                        self.message = "위키에 추가했어요."
            with self.search_lock:
                try:
                    embedder = ctx.embedder()
                except Exception:
                    embedder = None
                if article is not None:
                    try_write_vectors(ctx.wiki, embedder, article)
                ctx.index.sync(ctx.wiki, embedder)
            self.state = "done"
        except LlmError as e:
            name = self.agent()
            self.message = LLM_ERRORS.get(e.kind, LLM_ERROR_DEFAULT).format(
                name=AGENTS[name], tool=AGENT_TOOLS[name]
            )
            self.state = "error"
        except (FetchError, httpx.HTTPError):
            self.state, self.message = (
                "error",
                "본문을 가져오지 못했어요. URL과 원문 접근 여부를 확인해 주세요.",
            )
        except Exception:
            log.exception("글 추가 실패")
            self.state, self.message = "error", "글을 추가하지 못했어요. 다시 시도해 주세요."
