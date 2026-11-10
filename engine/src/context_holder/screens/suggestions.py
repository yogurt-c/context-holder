"""검색창 추천 (Search · Decisions). 무엇을 찾을 수 있는지 결정 주제 문장으로 보여 준다.

후보는 넓은 주제 이름, 넓은 주제의 질문, 글마다 붙은 결정 주제(넓은 주제에 아직 묶이지
않은 것도)다. 모두 문장이라 짧은 검색어와도 검색 모델(bge-m3)로 뜻을 잴 수 있다.
글자로 맞는 문장을 먼저 두고, 뜻으로 가까운 문장을 뒤에 붙인다. 뜻은 두 가지로 잰다.
문장과 직접 비교하고, Search가 쓰는 글 조각 벡터로 가까운 글을 찾아 그 글의 결정 주제를 쓴다.
그래서 주제 문장에 없는 말(기술 · 제품 이름 등)로도 추천이 나온다.
문장 임베딩은 처음 쓸 때 백그라운드에서 한 번 만들고 메모리에 둔다. 새 문장만 더 만든다.
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
# 1등이 이보다 낮으면 뚜렷한 주제가 없는 말이다 (Kubernetes → 0.51~0.52로 고르게 붙는 문장들)
MIN_BEST = 0.55
MAX_GAP = 0.08  # 뜻으로 찾은 1등보다 이만큼 넘게 낮은 문장은 뺀다 (결제 → 채팅 클라이언트 같은 것)
MIN_MEANING_LEN = 2  # 입력 중인 한 글자로는 뜻을 찾지 않는다
NEIGHBORS = 4  # 뜻으로 가까운 글을 몇 개까지 보고 그 결정 주제를 추천할까
# 글 조각 점수는 주제 문장 점수보다 쉽게 높아서 (결제 → 팀 규칙 전달 같은 잡음) 더 엄하게 거른다
NEIGHBOR_SIMILARITY = 0.52
NEIGHBOR_GAP = 0.05
# 글에서는 무엇을 다루는지 드러나는 조각만 본다. 원문 단락은 우연히 말이 겹쳐 잡음이 된다
NEIGHBOR_PIECES = ("제목", "결정 기록")
BATCH = 16  # 한 번에 임베딩할 문장 수. 검색 요청이 오래 기다리지 않게 나눠 만든다

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Suggestion:
    text: str
    topic: str  # 문장이 속한 넓은 주제 (Decisions에서 고르면 이 비교표를 연다). 아직 없으면 ""
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
    min_best: float = MIN_BEST
    max_gap: float = MAX_GAP
    neighbors: int = NEIGHBORS
    neighbor_similarity: float = NEIGHBOR_SIMILARITY
    neighbor_gap: float = NEIGHBOR_GAP
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

    def suggest(self, q: str, grouped_only: bool = False) -> list[Suggestion]:
        """grouped_only: 넓은 주제에 묶인 문장만 (Decisions는 넓은 주제 비교표만 열 수 있다)."""
        candidates = self._candidates()
        if grouped_only:
            candidates = [c for c in candidates if c.topic]
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
        """사례가 있는 넓은 주제의 문장 (사례가 많은 주제 먼저, 안에서는 이름 · 질문 · 사례 순),
        그 뒤에 넓은 주제에 아직 묶이지 않은 결정 주제 (사례가 많은 순)."""
        topics = topicmod.load(self.wiki)
        counts: dict[str, int] = {}
        present: set[str] = set()
        loose: dict[str, tuple[str, int]] = {}  # 정규화한 주제 → (처음 본 표기, 사례 수)
        for a in self.index.select("WHERE decision_topic IS NOT NULL"):
            key = normalize_topic(a.decision_topic or "")
            present.add(key)
            group = topics.group_of(a.decision_topic)
            if group is not None:
                counts[group] = counts.get(group, 0) + 1
            elif key:
                text, n = loose.get(key, (a.decision_topic or "", 0))
                loose[key] = (text, n + 1)
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
        out += [
            Suggestion(text, "", "case")
            for text, _ in sorted(loose.values(), key=lambda tn: (-tn[1], tn[0]))
        ]
        return out

    def _meaning(self, q: str, candidates: list[Suggestion]) -> list[Suggestion]:
        """주제 문장 비교와 가까운 글의 결정 주제를 각자 기준으로 거른 뒤 점수 순으로 합친다."""
        self._build([c.text for c in candidates])
        embedder = self._model()
        if embedder is None or not candidates:
            return []
        with self.model_lock:
            query = embedder.query(q)
            near = [
                (self.index.get(h.article_id), h.score)
                for h in self.index.vector(query, limit=self.neighbors, labels=NEIGHBOR_PIECES)
            ]
        by_sentence = self._by_sentence(query, candidates)
        if by_sentence and max(by_sentence.values()) < self.min_best:
            by_sentence = {}
        scores = _passing(by_sentence, self.min_similarity, self.max_gap)
        by_topic = {normalize_topic(c.text): c for c in candidates if c.kind == "case"}
        by_article: dict[Suggestion, float] = {}
        for article, score in near:
            c = by_topic.get(normalize_topic(article.decision_topic or "")) if article else None
            if c is not None:
                by_article[c] = max(by_article.get(c, score), score)
        for c, score in _passing(by_article, self.neighbor_similarity, self.neighbor_gap).items():
            scores[c] = max(scores.get(c, score), score)
        return [c for c, _ in sorted(scores.items(), key=lambda cs: -cs[1])]

    def _by_sentence(
        self, query: np.ndarray, candidates: list[Suggestion]
    ) -> dict[Suggestion, float]:
        ready = [c for c in candidates if c.text in self._vectors]  # 만드는 중이면 된 것만
        if not ready:
            return {}
        sentence = np.stack([self._vectors[c.text] for c in ready]) @ query
        return {c: float(v) for c, v in zip(ready, sentence, strict=True)}

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


def _passing(scores: dict[Suggestion, float], floor: float, gap: float) -> dict[Suggestion, float]:
    """floor 이상이고, 1등보다 gap 넘게 낮지 않은 것만."""
    if not scores:
        return {}
    cut = max(floor, max(scores.values()) - gap)
    return {c: v for c, v in scores.items() if v >= cut}


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
