"""기기 로컬 검색 인덱스(`index.db`, SQLite). 위키에서 언제든 다시 만들 수 있다 (plan 6장).

키워드 검색은 FTS5로 한다. 필드 가중치: decision > title · tags > 핵심 내용 > 요약.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from context_holder.classify import Bucket
from context_holder.embed import Embedder
from context_holder.textproc import search_tokens
from context_holder.wiki import StoredArticle, Wiki

# bm25 가중치: id(색인 안 함), title, decision, tags, key_points, summary
WEIGHTS = (0.0, 3.0, 4.0, 3.0, 2.0, 1.0)

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    url TEXT, title TEXT, source TEXT, published TEXT,
    bucket TEXT, status TEXT, origin TEXT, decision_topic TEXT,
    path TEXT, signature TEXT
);
CREATE TABLE IF NOT EXISTS chunks (article_id TEXT, label TEXT, vec BLOB);
CREATE INDEX IF NOT EXISTS chunks_article ON chunks(article_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    id UNINDEXED, title, decision, tags, key_points, summary, tokenize = 'unicode61'
);
"""


@dataclass(frozen=True)
class IndexedArticle:
    id: str
    url: str
    title: str
    source_id: str
    published: str | None
    bucket: Bucket
    status: str
    origin: str | None
    decision_topic: str | None
    path: str


@dataclass(frozen=True)
class VectorHit:
    article_id: str
    score: float  # 코사인 유사도 (글의 조각 중 가장 가까운 것)
    label: str  # 어느 조각이 맞았나: "핵심 내용 3번째 줄", "원문 5번째 단락" 등


@dataclass(frozen=True)
class KeywordHit:
    article_id: str
    score: float  # 클수록 잘 맞는다


class Index:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")  # 엔진이 쓰는 동안 ctx mcp가 읽을 수 있게
        self.db.executescript(SCHEMA)
        self._matrix: tuple[np.ndarray, list[str], list[str]] | None = None

    def sync(self, wiki: Wiki, embedder: Embedder | None = None) -> tuple[int, int]:
        """바뀐 글만 다시 색인하고 사라진 글을 지운다. (갱신 수, 삭제 수)

        embedder가 있으면 위키 필드와 원문 단락 벡터도 넣는다. 모델이 바뀌면 전부 다시 계산한다.
        """
        known = dict(self.db.execute("SELECT id, signature FROM articles"))
        if embedder and self._meta("model") != embedder.model_id:
            known = {}
        current = {a.id: a for a in wiki.articles()}
        signature = {a.id: self._signature(wiki, a, embedder) for a in current.values()}
        changed = [a for a in current.values() if known.get(a.id) != signature[a.id]]
        removed = [
            aid
            for aid in dict(self.db.execute("SELECT id, signature FROM articles"))
            if aid not in current
        ]
        with self.db:
            if embedder and self._meta("model") != embedder.model_id:
                self.db.execute("DELETE FROM chunks")
                self.db.execute(
                    "INSERT OR REPLACE INTO meta VALUES ('model', ?)", (embedder.model_id,)
                )
            for aid in removed + [a.id for a in changed]:
                for table, key in (("articles", "id"), ("fts", "id"), ("chunks", "article_id")):
                    self.db.execute(f"DELETE FROM {table} WHERE {key} = ?", (aid,))
            for article in changed:
                self._insert(article, signature[article.id])
                if embedder:
                    self._insert_vectors(wiki, article, embedder)
        self._matrix = None
        return len(changed), len(removed)

    def vector(
        self,
        query_vector: np.ndarray,
        limit: int = 50,
        buckets: tuple[Bucket, ...] = (Bucket.SAVE, Bucket.INBOX),
    ) -> list[VectorHit]:
        matrix, ids, labels = self._load_matrix()
        if not ids:
            return []
        allowed = {
            aid
            for (aid,) in self.db.execute(
                f"SELECT id FROM articles WHERE bucket IN ({','.join('?' * len(buckets))})",
                [b.value for b in buckets],
            )
        }
        scores = matrix @ query_vector.astype(np.float32)
        best: dict[str, tuple[float, str]] = {}
        for i in np.argsort(-scores):
            aid = ids[i]
            if aid in allowed and aid not in best:
                best[aid] = (float(scores[i]), labels[i])
        ranked = sorted(best.items(), key=lambda kv: -kv[1][0])[:limit]
        return [VectorHit(aid, score, label) for aid, (score, label) in ranked]

    def keyword(
        self,
        query: str,
        limit: int = 50,
        buckets: tuple[Bucket, ...] = (Bucket.SAVE, Bucket.INBOX),
    ) -> list[KeywordHit]:
        tokens = sorted(set(search_tokens(query)))
        if not tokens:
            return []
        match = " OR ".join(f'"{t}"' for t in tokens)
        marks = ",".join("?" * len(buckets))
        rows = self.db.execute(
            f"""SELECT fts.id, bm25(fts, {", ".join(map(str, WEIGHTS))}) AS rank
                FROM fts JOIN articles a ON a.id = fts.id
                WHERE fts MATCH ? AND a.bucket IN ({marks})
                ORDER BY rank LIMIT ?""",
            (match, *[b.value for b in buckets], limit),
        )
        return [KeywordHit(aid, -rank) for aid, rank in rows]

    def get(self, article_id: str) -> IndexedArticle | None:
        row = self.db.execute(
            """SELECT id, url, title, source, published, bucket, status, origin,
                      decision_topic, path
               FROM articles WHERE id = ?""",
            (article_id,),
        ).fetchone()
        if row is None:
            return None
        return IndexedArticle(*row[:5], Bucket(row[5]), *row[6:])

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _signature(wiki: Wiki, a: StoredArticle, embedder: Embedder | None) -> str:
        if embedder is None:
            return a.signature
        path = wiki.vector_path(embedder.model_id, a.id)
        vec = f"{path.stat().st_mtime_ns}" if path.exists() else "-"
        return f"{a.signature}|{vec}"

    def _insert_vectors(self, wiki: Wiki, a: StoredArticle, embedder: Embedder) -> None:
        pieces = [("제목", a.title)]
        pieces += [(f"요약 {n}번째 줄", t) for n, t in enumerate(a.summary, 1)]
        pieces += [(f"핵심 내용 {n}번째 줄", t) for n, t in enumerate(a.key_points, 1)]
        if a.decision:
            pieces.append(("결정 기록", _decision_text(a.decision)))
        vectors = embedder.passages([text for _, text in pieces])
        rows = [
            (a.id, label, v.astype(np.float32).tobytes())
            for (label, _), v in zip(pieces, vectors, strict=True)
        ]
        paragraphs = wiki.read_vectors(embedder.model_id, a.id)
        if paragraphs is not None:
            rows += [(a.id, f"원문 {n}번째 단락", v.tobytes()) for n, v in enumerate(paragraphs, 1)]
        self.db.executemany("INSERT INTO chunks VALUES (?,?,?)", rows)

    def _load_matrix(self) -> tuple[np.ndarray, list[str], list[str]]:
        if self._matrix is None:
            rows = self.db.execute("SELECT article_id, label, vec FROM chunks").fetchall()
            vectors = [np.frombuffer(v, dtype=np.float32) for _, _, v in rows]
            matrix = np.vstack(vectors) if vectors else np.zeros((0, 1), dtype=np.float32)
            self._matrix = (matrix, [r[0] for r in rows], [r[1] for r in rows])
        return self._matrix

    def _insert(self, a: StoredArticle, signature: str) -> None:
        d = a.decision or {}
        decision_text = _decision_text(d)
        self.db.execute(
            "INSERT INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                a.id,
                a.url,
                a.title,
                a.source_id,
                a.published.isoformat() if a.published else None,
                a.bucket.value,
                a.status,
                a.origin,
                d.get("topic"),
                str(a.path),
                signature,
            ),
        )
        fields = (
            a.title,
            decision_text,
            " ".join(a.tags),
            " ".join(a.key_points),
            " ".join(a.summary),
        )
        self.db.execute(
            "INSERT INTO fts VALUES (?,?,?,?,?,?)",
            (a.id, *(" ".join(search_tokens(f)) for f in fields)),
        )


def _decision_text(d: dict) -> str:
    return " ".join(
        [
            d.get("topic") or "",
            d.get("problem") or "",
            d.get("chosen") or "",
            *(d.get("options") or []),
            *(d.get("tradeoffs") or []),
        ]
    )
