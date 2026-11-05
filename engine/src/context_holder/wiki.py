"""위키 레포(`context-wiki`)에 글 · 설정 · 상태를 읽고 쓴다 (plan 레포 구조).

글 파일은 한 번 쓰고 고치지 않는다. 요약 대기(pending) 글만 나중에 완료로 바꾼다.
원문 전문은 저장하지 않는다: 요약, 핵심 내용, Decision Record, 원문 링크만 쓴다.
"""

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

from context_holder.classify import Bucket, Decision, Profile, load_profile
from context_holder.collect import Article
from context_holder.enrich import DecisionRecord, Enrichment
from context_holder.sources import Source, load_sources

FOLDER = {Bucket.SAVE: "wiki", Bucket.INBOX: "inbox"}
SLUG_MAX = 60


@dataclass(frozen=True)
class PendingArticle:
    path: Path
    url: str
    source_id: str
    title: str
    published: datetime | None
    relevance: Decision


def article_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


class Wiki:
    def __init__(self, root: Path):
        self.root = root

    # ---------- 설정 ----------

    def ensure_config(self) -> None:
        """새 위키면 기본 설정을 넣는다. 이미 있으면 건드리지 않는다."""
        config = self.root / "config"
        config.mkdir(parents=True, exist_ok=True)
        for name in ("sources.yaml", "profile.yaml"):
            target = config / name
            if not target.exists():
                default = resources.files("context_holder.defaults").joinpath(name)
                target.write_text(default.read_text("utf-8"), encoding="utf-8")

    def sources(self) -> tuple[Source, ...]:
        return load_sources(self.root / "config/sources.yaml")

    def profile(self) -> Profile:
        return load_profile(self.root / "config/profile.yaml")

    # ---------- 본 글 기록 ----------

    def seen_urls(self) -> frozenset[str]:
        urls: set[str] = set()
        for path in sorted((self.root / "state/seen").glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    urls.add(json.loads(line)["url"])
        return frozenset(urls)

    def record_seen(self, decisions: Iterable[Decision], when: datetime) -> None:
        path = self.root / "state/seen" / f"{when:%Y-%m}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for d in decisions:
                row = {
                    "url": d.url,
                    "bucket": d.bucket.value,
                    "score": d.score,
                    "at": when.isoformat(),
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ---------- 글 ----------

    def write_article(
        self,
        article: Article,
        relevance: Decision,
        enrichment: Enrichment | None,
        collected: datetime,
        reason: str = "",
    ) -> Path:
        """저장 · 보류 글을 쓴다. enrichment가 없으면 요약 대기로 쓴다. 이미 있으면 그대로 둔다."""
        path = self._path_for(article, relevance.bucket, collected)
        if path.exists():
            return path
        meta = _meta(article.url, article.source_id, article.title, article.published, collected)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(meta, relevance, enrichment, reason), encoding="utf-8")
        return path

    def pending(self) -> list[PendingArticle]:
        found = []
        for folder in FOLDER.values():
            for path in sorted((self.root / folder).glob("*/*/*.md")):
                meta = _read_meta(path)
                if meta.get("status") != "pending":
                    continue
                rel = meta["relevance"]
                found.append(
                    PendingArticle(
                        path=path,
                        url=meta["url"],
                        source_id=meta["source"],
                        title=meta["title"],
                        published=_parse_time(meta.get("published")),
                        relevance=Decision(
                            meta["url"],
                            rel["score"],
                            Bucket(rel["bucket"]),
                            rel["topic"],
                            tuple(rel["plus"]),
                            tuple(rel["minus"]),
                        ),
                    )
                )
        return found

    def complete(
        self, pending: PendingArticle, enrichment: Enrichment, collected: datetime
    ) -> None:
        meta = _read_meta(pending.path)
        meta.setdefault("collected", collected.isoformat())
        keep = {k: meta[k] for k in ("id", "title", "url", "source", "published", "collected")}
        pending.path.write_text(_render(keep, pending.relevance, enrichment, ""), encoding="utf-8")

    def _path_for(self, article: Article, bucket: Bucket, collected: datetime) -> Path:
        if bucket not in FOLDER:
            raise ValueError(f"{bucket} 글은 위키에 쓰지 않는다")
        year = (article.published or collected).year
        folder = self.root / FOLDER[bucket] / article.source_id / str(year)
        slug = _slug(article.url) or article_id(article.url)
        path = folder / f"{slug}.md"
        if path.exists() and _read_meta(path).get("url") != article.url:
            path = folder / f"{slug}-{article_id(article.url)[:6]}.md"
        return path


def _slug(url: str) -> str:
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if not segments:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", unquote(segments[-1]).lower()).strip("-")
    return slug[:SLUG_MAX].rstrip("-") if re.search(r"[a-z]", slug) else ""


def _meta(
    url: str, source_id: str, title: str, published: datetime | None, collected: datetime
) -> dict[str, Any]:
    return {
        "id": article_id(url),
        "title": title,
        "url": url,
        "source": source_id,
        "published": published.isoformat() if published else None,
        "collected": collected.isoformat(),
    }


def _render(
    meta: dict[str, Any], relevance: Decision, enrichment: Enrichment | None, reason: str
) -> str:
    front = {
        **meta,
        "status": "done" if enrichment else "pending",
        "relevance": {
            "score": round(relevance.score, 3),
            "bucket": relevance.bucket.value,
            "topic": relevance.topic,
            "plus": list(relevance.plus),
            "minus": list(relevance.minus),
        },
    }
    if enrichment:
        front["tags"] = list(enrichment.tags)
        if enrichment.media:
            front["media"] = asdict(enrichment.media)
        front["decision"] = _decision_dict(enrichment.decision)
    header = yaml.safe_dump(front, allow_unicode=True, sort_keys=False)
    body = _body(enrichment, reason)
    return f"---\n{header}---\n\n# {meta['title']}\n\n{body}[원문 보기]({meta['url']})\n"


def _body(enrichment: Enrichment | None, reason: str) -> str:
    if enrichment is None:
        return f"요약 대기: 다음 수집 때 다시 만든다. ({reason})\n\n"
    parts = ["## 요약\n", *[f"- {s}\n" for s in enrichment.summary], "\n## 핵심 내용\n"]
    parts += [f"- {k}\n" for k in enrichment.key_points]
    d = enrichment.decision
    if d:
        parts += [
            f"\n## 결정 기록: {d.topic}\n",
            f"- 문제: {d.problem}\n",
            f"- 선택지: {', '.join(d.options)}\n",
            f"- 선택: {d.chosen}\n",
            *[f"- 트레이드오프: {t}\n" for t in d.tradeoffs],
            f"- 결과: {d.outcome}\n" if d.outcome else "",
            *[f"- 근거: > {e}\n" for e in d.evidence],
            f"- 신뢰도: {d.confidence.value}"
            + (f" (원문에서 찾지 못한 근거 {len(d.unverified)}건)" if d.unverified else "")
            + "\n",
        ]
    return "".join(parts) + "\n"


def _decision_dict(d: DecisionRecord | None) -> dict[str, Any] | None:
    if d is None:
        return None
    data = asdict(d)
    data["confidence"] = d.confidence.value
    return {k: list(v) if isinstance(v, tuple) else v for k, v in data.items()}


def _read_meta(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    _, header, _ = text.split("---\n", 2)
    return yaml.safe_load(header) or {}


def _parse_time(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None
