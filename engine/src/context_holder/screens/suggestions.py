"""검색창 추천 (Search · Decisions). 무엇을 찾을 수 있는지 결정 주제 문장으로 보여 준다.

후보는 넓은 주제 이름, 넓은 주제의 질문, 사례마다 붙은 원래 주제다. 모두 문장이라
짧은 검색어와도 검색 모델(bge-m3)로 뜻을 잴 수 있다. 글자로 맞는 문장을 먼저 두고,
뜻으로 가까운 문장을 뒤에 붙인다. 문장 임베딩은 처음 쓸 때 백그라운드에서 한 번 만들고
메모리에 둔다. 주제를 다시 정리하면 새 문장만 더 만든다.
"""

import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

import numpy as np

from context_holder import topics as topicmod
from context_holder.embed import Embedder
from context_holder.index import Index, normalize_topic
from context_holder.wiki import Wiki

LIMIT = 6
MIN_SIMILARITY = 0.5  # bge-m3: 맞는 추천은 대개 0.5 넘고, 잡음(냉장고 등)은 0.48 아래
MAX_GAP = 0.1  # 뜻으로 찾은 1등보다 이만큼 넘게 낮은 문장은 뺀다 (결제 → 채팅 클라이언트 같은 것)
MIN_MEANING_LEN = 2  # 입력 중인 한 글자로는 뜻을 찾지 않는다
BATCH = 16  # 한 번에 임베딩할 문장 수. 검색 요청이 오래 기다리지 않게 나눠 만든다

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Suggestion:
    text: str
    topic: str  # 문장이 속한 넓은 주제 (Decisions에서 고르면 이 비교표를 연다)
    kind: str  # topic | question | case


@dataclass
class Suggester:
    wiki: Wiki
    index: Index
    embedder: Callable[[], Embedder | None]
    background: Callable[[Callable[[], None]], None] = field(
        default=lambda work: threading.Thread(target=work, daemon=True).start()
    )
    # 검색 요청과 모델 · 인덱스를 나눠 쓴다. 요청 안에서 다시 잡으므로 RLock이어야 한다
    model_lock: AbstractContextManager = field(default_factory=threading.RLock)
    min_similarity: float = MIN_SIMILARITY
    max_gap: float = MAX_GAP
    limit: int = LIMIT
    _vectors: dict[str, np.ndarray] = field(default_factory=dict, init=False)
    _building: bool = field(default=False, init=False)
    _broken: bool = field(default=False, init=False)

    def warm(self) -> None:
        """화면 서버가 뜰 때 문장 임베딩을 미리 만든다. 첫 입력이 모델 로드를 기다리지 않게."""

        def work() -> None:
            with self.model_lock:
                candidates = self._candidates()
            self._build([c.text for c in candidates])

        self.background(work)

    def suggest(self, q: str) -> list[Suggestion]:
        candidates = self._candidates()
        q = q.strip()
        if not q:
            return _starters(candidates)[: self.limit]
        key = normalize_topic(q)
        found = [c for c in candidates if key in normalize_topic(c.text)]
        if len(q) >= MIN_MEANING_LEN:
            found += self._meaning(q, candidates)
        return _unique(found)[: self.limit]

    # ---------- 내부 ----------

    def _candidates(self) -> list[Suggestion]:
        """사례가 있는 넓은 주제의 문장. 사례가 많은 주제 먼저, 안에서는 이름 · 질문 · 사례 순."""
        topics = topicmod.load(self.wiki)
        counts: dict[str, int] = {}
        present: set[str] = set()
        for a in self.index.select("WHERE decision_topic IS NOT NULL"):
            present.add(normalize_topic(a.decision_topic or ""))
            group = topics.group_of(a.decision_topic)
            if group is not None:
                counts[group] = counts.get(group, 0) + 1
        out: list[Suggestion] = []
        for g in sorted(topics.groups, key=lambda g: (-counts.get(g.name, 0), g.name)):
            if not counts.get(g.name):
                continue
            out.append(Suggestion(g.name, g.name, "topic"))
            if g.question:
                out.append(Suggestion(g.question, g.name, "question"))
            out += [
                Suggestion(t, g.name, "case") for t in g.topics if normalize_topic(t) in present
            ]
        return out

    def _meaning(self, q: str, candidates: list[Suggestion]) -> list[Suggestion]:
        self._build([c.text for c in candidates])
        ready = [c for c in candidates if c.text in self._vectors]  # 만드는 중이면 된 것만
        if not ready:
            return []
        embedder = self._model()
        if embedder is None:
            return []
        with self.model_lock:
            query = embedder.query(q)
        scores = np.stack([self._vectors[c.text] for c in ready]) @ query
        floor = max(self.min_similarity, float(scores.max()) - self.max_gap)
        return [ready[i] for i in np.argsort(-scores) if scores[i] >= floor]

    def _build(self, texts: list[str]) -> None:
        """없는 문장만 백그라운드에서 임베딩한다. 모델 로드도 백그라운드에서 한다."""
        missing = [t for t in texts if t not in self._vectors]
        if not missing or self._building or self._broken:
            return
        self._building = True

        def work() -> None:
            try:
                embedder = self._model()
                for start in range(0, len(missing) if embedder else 0, BATCH):
                    part = missing[start : start + BATCH]
                    with self.model_lock:
                        vectors = embedder.passages(part)
                    self._vectors.update(zip(part, vectors, strict=True))
            except Exception as e:  # 모델이 깨졌으면 글자로만 추천한다
                log.warning("추천 문장을 임베딩하지 못했다: %s", e)
                self._broken = True
            finally:
                self._building = False

        self.background(work)

    def _model(self) -> Embedder | None:
        try:
            return self.embedder()
        except Exception:  # 모델이 이 기기에 없다
            return None


def _starters(candidates: list[Suggestion]) -> list[Suggestion]:
    """빈 검색창: 넓은 주제마다 질문 하나 (없으면 이름). 무엇을 찾을 수 있는지 보여 준다."""
    by_topic: dict[str, Suggestion] = {}
    for c in candidates:
        if c.kind == "question" or (c.kind == "topic" and c.topic not in by_topic):
            by_topic[c.topic] = c
    return list(by_topic.values())


def _unique(items: list[Suggestion]) -> list[Suggestion]:
    seen: set[str] = set()
    out = []
    for s in items:
        key = normalize_topic(s.text)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out
