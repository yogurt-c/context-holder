"""위키 레포(`context-wiki`)에 글 · 설정 · 상태를 읽고 쓴다 (plan 레포 구조).

글 파일은 한 번 쓰고 고치지 않는다. 예외는 둘이다.
- 요약 대기(pending) 글을 나중에 완료로 바꾼다.
- Inbox에서 사용자가 고른 보류 글: 저장이면 wiki/로 옮기고, 관련없음이면 지운다.
원문 전문은 저장하지 않는다: 요약, 핵심 내용, Decision Record, 원문 링크만 쓴다.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import numpy as np
import yaml

from context_holder import sourcesedit
from context_holder.classify import Bucket, Decision, Profile, ProfileError, load_profile
from context_holder.collect import Article
from context_holder.embed import DEFAULT_MODEL
from context_holder.enrich import DecisionRecord, Enrichment
from context_holder.roles import with_roles
from context_holder.sources import Region, Source, SourceConfigError, active, load_sources

FOLDER = {Bucket.SAVE: "wiki", Bucket.INBOX: "inbox"}
SLUG_MAX = 60


@dataclass(frozen=True)
class StoredArticle:
    """위키에 저장된 글. 검색 인덱스를 만들 때 읽는다."""

    id: str
    path: Path
    url: str
    title: str
    source_id: str
    published: datetime | None
    bucket: Bucket
    status: str
    origin: str | None
    tags: tuple[str, ...]
    summary: tuple[str, ...]
    key_points: tuple[str, ...]
    decision: dict[str, Any] | None
    signature: str  # 파일이 바뀌었는지 비교하는 값
    collected: datetime | None = None
    relevance: float | None = None
    media: dict[str, Any] | None = None  # 영상 글: url, provider, status
    reasons: dict[str, Any] | None = None  # Claude 판정: topic, plus, minus (Inbox "왜 보류됐나")


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

    def search_model(self) -> str:
        """모든 기기가 쓰는 검색 모델 (config/search.yaml)."""
        path = self.root / "config/search.yaml"
        if not path.exists():
            return DEFAULT_MODEL
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return str(data.get("model") or DEFAULT_MODEL)

    def set_search_model(self, model_id: str) -> None:
        path = self.root / "config/search.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# 모든 기기가 쓰는 검색 모델. 바꾸려면 ctx reembed --model\nmodel: {model_id}\n",
            encoding="utf-8",
        )

    # ---------- 원문 단락 벡터 (원문 텍스트는 저장하지 않는다) ----------

    def vector_path(self, model_id: str, article_id: str) -> Path:
        return self.root / "vectors" / model_id / f"{article_id}.f16"

    def write_vectors(
        self, model_id: str, article_id: str, vectors: np.ndarray, content_hash: str
    ) -> None:
        path = self.vector_path(model_id, article_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(np.asarray(vectors, dtype=np.float16).tobytes())
        meta = {
            "model": model_id,
            "count": int(vectors.shape[0]),
            "dim": int(vectors.shape[1]),
            "content_hash": content_hash,
        }
        path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")

    def read_vectors(self, model_id: str, article_id: str) -> np.ndarray | None:
        path = self.vector_path(model_id, article_id)
        if not path.exists():
            return None
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        data = np.frombuffer(path.read_bytes(), dtype=np.float16)
        return data.reshape(meta["count"], meta["dim"]).astype(np.float32)

    def remove_vectors(self, model_id: str) -> None:
        folder = self.root / "vectors" / model_id
        for path in folder.glob("*"):
            path.unlink()
        if folder.exists():
            folder.rmdir()

    def sources(self) -> tuple[Source, ...]:
        return load_sources(self.root / "config/sources.yaml")

    def add_source(self, name: str, region: Region, feed_url: str) -> Source:
        """수집할 소스를 sources.yaml에 넣는다. 다음 수집이 커밋 · push한다.

        같은 feed가 이미 있으면 SourceConfigError. 수집 대상에서 뺀 소스면 include_source를 쓴다.
        """
        path = self.root / "config/sources.yaml"
        text = path.read_text(encoding="utf-8")
        current = sourcesedit.checked(text, str(path))
        existing = sourcesedit.find_by_feed(current, feed_url)
        if existing:
            raise SourceConfigError(f"이미 있는 소스예요: {existing.name}")
        source_id = sourcesedit.new_id(feed_url, {s.id for s in current})
        updated = sourcesedit.with_source(text, source_id, name, region, feed_url)
        added = next(
            (s for s in sourcesedit.checked(updated, str(path)) if s.id == source_id), None
        )
        if added is None or added.feed_url != feed_url or added.excluded:
            raise SourceConfigError(f"{path}: 소스를 넣지 못했다")
        path.write_text(updated, encoding="utf-8")
        return added

    def exclude_source(self, source_id: str) -> None:
        """소스를 수집 대상에서 뺀다. 이미 모은 글과 소스 정보(이름)는 그대로 둔다."""
        self._set_excluded(source_id, True)

    def include_source(self, source_id: str) -> None:
        """수집 대상에서 뺀 소스를 다시 수집한다."""
        self._set_excluded(source_id, False)

    def _set_excluded(self, source_id: str, excluded: bool) -> None:
        path = self.root / "config/sources.yaml"
        text = path.read_text(encoding="utf-8")
        current = sourcesedit.checked(text, str(path))
        if source_id not in {s.id for s in current}:
            raise SourceConfigError(f"없는 소스예요: {source_id}")
        ids = {s.id for s in current if s.excluded} - {source_id}
        if excluded:
            ids.add(source_id)
        updated = sourcesedit.with_excluded(text, ids)
        after = {s.id for s in sourcesedit.checked(updated, str(path)) if s.excluded}
        if after != ids:  # excluded를 손으로 여러 줄 목록으로 고친 파일 등
            raise SourceConfigError(f"{path}: excluded 목록을 고치지 못했다")
        path.write_text(updated, encoding="utf-8")

    def profile(self) -> Profile:
        return load_profile(self.root / "config/profile.yaml")

    def active_sources(self) -> tuple[Source, ...]:
        """고른 직군으로 수집할 소스."""
        return active(self.sources(), self.profile().roles)

    def roles(self) -> tuple[str, ...]:
        """위키에 고른 직군. 아직 고르지 않았으면(설정이 없거나 예전 profile) 빈 값."""
        try:
            return self.profile().roles
        except ProfileError:
            return ()

    def set_roles(self, roles: Sequence[str]) -> None:
        """고른 직군을 profile.yaml에 적는다. 다음 수집이 커밋 · push한다.

        이미 저장한 글은 건드리지 않는다. 바뀐 기준은 다음 수집부터 쓴다.
        """
        self.ensure_config()
        path = self.root / "config/profile.yaml"
        text = with_roles(path.read_text(encoding="utf-8"), roles)
        path.write_text(text, encoding="utf-8")

    # ---------- 본 글 기록 ----------

    def seen_urls(self) -> frozenset[str]:
        return frozenset(row["url"] for row in self._seen_rows())

    def _seen_rows(self) -> list[dict[str, Any]]:
        rows = []
        for path in sorted((self.root / "state/seen").glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def seen_hashes(self) -> frozenset[str]:
        """본 글의 본문 해시. 같은 글이 다른 주소로 다시 올라와도 거른다."""
        return frozenset(row["hash"] for row in self._seen_rows() if row.get("hash"))

    def record_seen(
        self,
        decisions: Iterable[Decision],
        when: datetime,
        hashes: Mapping[str, str] | None = None,
    ) -> None:
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
                if hashes and d.url in hashes:
                    row["hash"] = hashes[d.url]
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ---------- 글 ----------

    def write_article(
        self,
        article: Article,
        relevance: Decision,
        enrichment: Enrichment | None,
        collected: datetime,
        reason: str = "",
        origin: str | None = None,
    ) -> Path:
        """저장 · 보류 글을 쓴다. enrichment가 없으면 요약 대기로 쓴다. 이미 있으면 그대로 둔다."""
        path = self._path_for(article, relevance.bucket, collected)
        if path.exists():
            return path
        meta = _meta(article.url, article.source_id, article.title, article.published, collected)
        if origin:
            meta["origin"] = origin  # init: 첫 실행에서 들어온 보류 글 (Inbox에서 따로 묶음)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(meta, relevance, enrichment, reason), encoding="utf-8")
        return path

    def articles(self) -> Iterator[StoredArticle]:
        for folder in FOLDER.values():
            for path in sorted((self.root / folder).glob("*/*/*.md")):
                yield _stored(path)

    def read(self, path: Path) -> StoredArticle | None:
        """글 하나를 읽는다. 다른 기기의 pull로 지워졌으면 None."""
        return _stored(path) if path.exists() else None

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
        keys = ("id", "title", "url", "source", "published", "collected", "origin")
        keep = {k: meta[k] for k in keys if k in meta}
        pending.path.write_text(_render(keep, pending.relevance, enrichment, ""), encoding="utf-8")

    def save_from_inbox(
        self, path: Path, collected: datetime | None = None, origin: str | None = None
    ) -> Path:
        """보류 글을 저장으로 바꿔 wiki/로 옮긴다. 새 경로를 돌려준다."""
        text = path.read_text(encoding="utf-8")
        _, header, body = text.split("---\n", 2)
        meta = yaml.safe_load(header) or {}
        meta["relevance"]["bucket"] = Bucket.SAVE.value
        if collected:
            meta["collected"] = collected.isoformat()
        if origin:
            meta["origin"] = origin
        target = self.root / FOLDER[Bucket.SAVE] / path.parent.relative_to(self.root / "inbox")
        target = target / path.name
        if target.exists():
            target = target.with_name(f"{path.stem}-{meta['id'][:6]}.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        dumped = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
        target.write_text(f"---\n{dumped}---\n{body}", encoding="utf-8")
        path.unlink()
        return target

    def remove_article(self, path: Path, article_id: str) -> None:
        """글 파일과 원문 단락 벡터를 지운다 (Inbox 관련없음 · 글 상세 위키에서 제거).

        본 글 기록(state/seen)은 남겨서 다음 수집 때 다시 들이지 않는다.
        """
        path.unlink(missing_ok=True)
        for vec in (self.root / "vectors").glob(f"*/{article_id}.*"):
            vec.unlink()

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


def _stored(path: Path) -> StoredArticle:
    text = path.read_text(encoding="utf-8")
    _, header, body = text.split("---\n", 2)
    meta = yaml.safe_load(header) or {}
    stat = path.stat()
    return StoredArticle(
        id=meta["id"],
        path=path,
        url=meta["url"],
        title=meta["title"],
        source_id=meta["source"],
        published=_parse_time(meta.get("published")),
        bucket=Bucket(meta["relevance"]["bucket"]),
        status=meta.get("status", "done"),
        origin=meta.get("origin"),
        tags=tuple(meta.get("tags") or ()),
        summary=_section(body, "요약"),
        key_points=_section(body, "핵심 내용"),
        decision=meta.get("decision"),
        signature=f"{stat.st_mtime_ns}:{stat.st_size}",
        collected=_parse_time(meta.get("collected")),
        relevance=meta["relevance"].get("score"),
        media=meta.get("media"),
        reasons={k: meta["relevance"].get(k) for k in ("topic", "plus", "minus")},
    )


def _section(body: str, name: str) -> tuple[str, ...]:
    match = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z|^\[원문 보기\])", body, re.M | re.S)
    if not match:
        return ()
    return tuple(line[2:].strip() for line in match.group(1).splitlines() if line.startswith("- "))
