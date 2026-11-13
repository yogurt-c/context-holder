"""결정 주제 정리 (plan 5.2 · 7장). 구체적인 결정 주제를 넓은 주제로 묶어 회사끼리 비교한다.

- 위키 `decisions/topics.yaml`: 넓은 주제 → 분야, 한 줄 질문, 원래 주제들. 글 파일은 고치지 않는다.
- 수집 · 글 추가(assign)는 글을 요약하는 같은 호출에서 기존 넓은 주제 중 하나를 고르게 하고
  (enrich), 고른 것만 붙인다.
- 그래도 남은 사례는 첫 수집 · 매일 수집 · 글 추가가 끝날 때 정리(regroup)로 묶는다.
  Claude에 주제와 문제 한 줄만 보낸다 (원문 없음). 처음에는 모든 사례를, 그 뒤에는
  아직 분류하지 않은 사례만 기존 주제 목록과 함께 보낸다. 맞는 주제가 없으면 새로 만든다.
  남은 사례가 없으면 Claude를 부르지 않는다.
- 정리할 때 위키에서 사라진 원래 주제는 지도에서 뺀다.
- Decisions 화면에서 사례를 다른 주제로 옮기고(move), 주제의 이름 · 분야 · 질문을 고친다(edit).
  분류된 사례는 정리가 다시 옮기지 않으므로 손으로 고친 것이 남는다.
- 파일은 다음 수집이 커밋 · push한다.
  같은 기기의 두 프로세스(화면 · 수집)가 동시에 고치지 않게 잠근다.
"""

import fcntl
import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any

import yaml

from context_holder.classify import StructuredLlm
from context_holder.index import normalize_topic
from context_holder.wiki import Wiki

log = logging.getLogger(__name__)

TOPICS_FILE = "decisions/topics.yaml"
BATCH = 200  # Claude 한 번에 보낼 사례 수
PROBLEM_CHARS = 120
NAME_CHARS = 40
QUESTION_CHARS = 120
AREAS = (
    "백엔드",
    "인프라",
    "데이터",
    "프론트엔드",
    "모바일",
    "AI 엔지니어링",
    "보안",
    "개발 생산성",
    "조직 · 프로세스",
)

SYSTEM = (
    "너는 빅테크 기술 블로그의 기술 결정 사례를 넓은 주제로 분류한다. "
    "같은 문제를 두고 여러 회사의 선택을 나란히 비교할 수 있게 묶는 것이 목적이다."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "area": {"type": "string"},
                    "question": {"type": "string"},
                    "cases": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name", "area", "question", "cases"],
            },
        }
    },
    "required": ["groups"],
}


@dataclass(frozen=True)
class Group:
    name: str  # 넓은 주제 (예: "LLM 에이전트 설계")
    area: str = ""  # 분야 (예: "AI 엔지니어링")
    question: str = ""  # 이 주제가 답하려는 질문 한 줄
    topics: tuple[str, ...] = ()  # 묶인 원래 주제들


@dataclass(frozen=True)
class TopicMap:
    groups: tuple[Group, ...] = ()
    _by_topic: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        for g in self.groups:
            for t in g.topics:
                self._by_topic.setdefault(normalize_topic(t), g.name)

    def group_of(self, topic: str | None) -> str | None:
        return self._by_topic.get(normalize_topic(topic)) if topic else None

    def group(self, name: str) -> Group | None:
        key = normalize_topic(name)
        return next((g for g in self.groups if normalize_topic(g.name) == key), None)

    def add(self, additions: dict[str, list[str]], new: Sequence[Group] = ()) -> "TopicMap":
        """주제마다 원래 주제를 더한 새 지도. 이미 분류된 원래 주제는 옮기지 않는다."""
        groups = [*self.groups, *(g for g in new if self.group(g.name) is None)]
        taken = set(self._by_topic)
        out = []
        for g in groups:
            fresh = []
            for t in additions.get(g.name, []):
                if normalize_topic(t) not in taken:
                    taken.add(normalize_topic(t))
                    fresh.append(t)
            out.append(replace(g, topics=(*g.topics, *fresh)))
        return TopicMap(tuple(g for g in out if g.topics))

    def keep(self, present: set[str]) -> "TopicMap":
        """present(정규화한 원래 주제)에 있는 것만 남긴 새 지도. 빈 주제는 뺀다."""
        groups = (
            replace(g, topics=tuple(t for t in g.topics if normalize_topic(t) in present))
            for g in self.groups
        )
        return TopicMap(tuple(g for g in groups if g.topics))


@dataclass(frozen=True)
class Case:
    topic: str
    problem: str


@dataclass(frozen=True)
class RegroupResult:
    groups: int  # 정리 뒤 넓은 주제 수
    assigned: int  # 이번에 분류한 원래 주제 수
    left: int  # 아직 분류하지 않은 원래 주제 수
    cost_usd: float


# ---------- 파일 ----------


def load(wiki: Wiki) -> TopicMap:
    try:
        data = yaml.safe_load((wiki.root / TOPICS_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return TopicMap()
    groups = []
    for raw in data.get("groups") or [] if isinstance(data, dict) else []:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        topics = tuple(str(t) for t in raw.get("topics") or [] if t)
        groups.append(
            Group(
                str(raw["name"]), str(raw.get("area") or ""), str(raw.get("question") or ""), topics
            )
        )
    return TopicMap(tuple(groups))


def save(wiki: Wiki, topics: TopicMap) -> None:
    path = wiki.root / TOPICS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "groups": [
            {"name": g.name, "area": g.area, "question": g.question, "topics": list(g.topics)}
            for g in topics.groups
        ]
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


@contextmanager
def locked(wiki: Wiki) -> Iterator[None]:
    """같은 기기에서 topics.yaml을 읽고 고치는 동안 다른 프로세스를 기다리게 한다."""
    lock = wiki.root / ".git" / "topics.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---------- 사례 ----------


def cases(wiki: Wiki) -> list[Case]:
    """위키의 결정 사례. 원래 주제가 같으면 하나로 본다."""
    found: dict[str, Case] = {}
    for article in wiki.articles():
        d = article.decision or {}
        topic = " ".join(str(d.get("topic") or "").split())
        if topic:
            found.setdefault(normalize_topic(topic), Case(topic, str(d.get("problem") or "")))
    return list(found.values())


def unassigned(wiki: Wiki, topics: TopicMap) -> list[Case]:
    return [c for c in cases(wiki) if topics.group_of(c.topic) is None]


# ---------- 수집 · 글 추가: 요약하며 고른 주제 붙이기 (추가 호출 없음) ----------


def choices(wiki: Wiki) -> dict[str, str]:
    """요약할 때 고를 넓은 주제 (이름 → 질문). 아직 정리하지 않았으면 비어 있다."""
    with locked(wiki):
        return {g.name: g.question for g in load(wiki).groups}


def assign(wiki: Wiki, chosen: Mapping[str, str]) -> int:
    """chosen(원래 주제 → 넓은 주제)을 지도에 더한다. 더한 수.

    이미 분류된 원래 주제와 지도에 없는 넓은 주제는 건너뛴다.
    """
    if not chosen:
        return 0
    with locked(wiki):
        topics = load(wiki)
        additions: dict[str, list[str]] = {}
        for topic, name in chosen.items():
            group = topics.group(name)
            if group is not None:
                additions.setdefault(group.name, []).append(topic)
        updated = topics.add(additions)
        added = _count(updated) - _count(topics)
        if added:
            save(wiki, updated)
        return added


def _count(topics: TopicMap) -> int:
    return sum(len(g.topics) for g in topics.groups)


# ---------- 화면: 손으로 옮기기 · 고치기 (Claude 없음) ----------


def _clean(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def move(wiki: Wiki, topic: str, to: str, area: str | None = None, question: str = "") -> str:
    """원래 주제 topic을 넓은 주제 to로 옮긴다. area를 주면 to를 새 주제로 만든다.

    옮긴 넓은 주제 이름을 준다. 사례가 모두 빠진 주제는 지도에서 사라진다.
    """
    name = _clean(to, NAME_CHARS)
    if not name:
        raise ValueError("옮길 주제를 골라 주세요.")
    with locked(wiki):
        topics = load(wiki)
        current = topics.group_of(topic)
        if current is None:
            raise ValueError("이미 옮겼거나 없는 사례예요.")
        target = topics.group(name)
        if area is not None:
            if target is not None:
                raise ValueError("이미 있는 주제예요. 기존 주제에서 골라 주세요.")
            if area not in AREAS:
                raise ValueError("새 주제의 분야를 골라 주세요.")
            target = Group(name, area, _clean(question, QUESTION_CHARS))
        elif target is None:
            raise ValueError("없는 주제예요. 화면을 새로 고쳐 주세요.")
        if target.name == current:
            return current
        key = normalize_topic(topic)
        rest = TopicMap(
            tuple(
                replace(g, topics=tuple(t for t in g.topics if normalize_topic(t) != key))
                for g in topics.groups
            )
        )
        save(wiki, rest.add({target.name: [topic]}, (target,)))
        return target.name


def edit(wiki: Wiki, name: str, new_name: str, area: str, question: str) -> str:
    """넓은 주제 name의 이름 · 분야 · 질문을 고친다. 고친 이름을 준다."""
    new_name = _clean(new_name, NAME_CHARS)
    if not new_name:
        raise ValueError("주제 이름을 적어 주세요.")
    if area not in AREAS:
        raise ValueError("분야를 골라 주세요.")
    with locked(wiki):
        topics = load(wiki)
        group = topics.group(name)
        if group is None:
            raise ValueError("없는 주제예요. 화면을 새로 고쳐 주세요.")
        other = topics.group(new_name)
        if other is not None and other is not group:
            raise ValueError("이미 있는 주제예요. 다른 이름을 적어 주세요.")
        changed = replace(
            group, name=new_name, area=area, question=_clean(question, QUESTION_CHARS)
        )
        save(wiki, TopicMap(tuple(changed if g is group else g for g in topics.groups)))
        return new_name


# ---------- 수집 끝: Claude로 정리 ----------


def regroup(
    wiki: Wiki, llm: StructuredLlm, progress: Callable[[int, int], None] = lambda done, total: None
) -> RegroupResult:
    """분류하지 않은 사례를 Claude로 넓은 주제에 나눈다. 처음이면 모든 사례가 대상이다."""
    present = {normalize_topic(c.topic) for c in cases(wiki)}
    with locked(wiki):  # 위키에서 사라진 원래 주제를 먼저 뺀다
        loaded = load(wiki)
        topics = loaded.keep(present)
        if topics != loaded:
            save(wiki, topics)
    todo = unassigned(wiki, topics)
    cost = 0.0
    batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
    for n, batch in enumerate(batches, 1):
        result = llm.structured(_prompt(batch, topics), SYSTEM, SCHEMA)
        cost += result.cost_usd
        additions, new = _parse(result.data, batch, topics)
        with locked(wiki):  # 그사이 수집이 붙인 것을 잃지 않게 다시 읽고 더한다
            topics = load(wiki).add(additions, new)
            save(wiki, topics)
        progress(n, len(batches))
    left = len(unassigned(wiki, topics))
    return RegroupResult(len(topics.groups), len(todo) - left, left, cost)


def settle(wiki: Wiki, llm: StructuredLlm) -> float:
    """수집 · 글 추가 끝에 남은 사례를 묶는다. 쓴 비용.

    남은 사례가 없으면 Claude를 부르지 않는다. 실패해도 예외를 올리지 않는다
    (묶지 못한 사례는 다음 수집 때 다시 묶는다).
    """
    try:
        result = regroup(wiki, llm)
    except Exception as e:
        log.warning("결정 주제를 정리하지 못했다: %s", e)
        return 0.0
    if result.assigned:
        log.info("결정 사례 %d건을 주제 %d개로 정리했다", result.assigned, result.groups)
    return result.cost_usd


def _prompt(batch: Sequence[Case], topics: TopicMap) -> str:
    lines = [f"{i}. {c.topic} — {c.problem[:PROBLEM_CHARS]}" for i, c in enumerate(batch, 1)]
    if topics.groups:
        existing = "\n".join(f"- {g.name} ({g.area})" for g in topics.groups)
        guide = (
            "아래 사례를 기존 주제에 넣어라. 기존 주제의 이름(name)과 분야(area)를 그대로 쓴다. "
            "어디에도 맞지 않는 사례만 새 주제를 만든다.\n\n"
            f"기존 주제:\n{existing}"
        )
    else:
        guide = (
            "아래 사례를 20~40개의 넓은 주제로 나눠라. "
            "한 주제에 여러 회사의 사례가 모이도록, 기술 결정의 종류로 묶는다 "
            "(예: 'LLM 에이전트 설계', '테스트 자동화', '모노레포 운영', '캐시 전략'). "
            "정말 다른 사례만 혼자 둔다."
        )
    rules = (
        "규칙: 모든 사례 번호를 정확히 한 주제에 넣는다. "
        f"name은 {NAME_CHARS}자 이내 한국어 명사구, "
        "area는 " + ", ".join(f"'{a}'" for a in AREAS) + " 중 하나, "
        "question은 이 주제가 답하려는 질문 한 문장"
        "(예: 'DB 변경과 메시지 발행을 어떻게 함께 보장할까')."
    )
    return f"{guide}\n\n{rules}\n\n사례:\n" + "\n".join(lines)


def _parse(
    data: dict[str, Any], batch: Sequence[Case], topics: TopicMap
) -> tuple[dict[str, list[str]], list[Group]]:
    """Claude 답을 지도에 더할 꼴로. 없는 번호 · 두 번 나온 번호는 버린다 (다음 정리 때 다시)."""
    additions: dict[str, list[str]] = {}
    new: list[Group] = []
    used: set[int] = set()
    for raw in data.get("groups") or []:
        name = " ".join(str(raw.get("name") or "").split())[:NAME_CHARS]
        if not name:
            continue
        existing = topics.group(name)
        if existing is None and not any(
            normalize_topic(g.name) == normalize_topic(name) for g in new
        ):
            area = " ".join(str(raw.get("area") or "").split())
            new.append(Group(name, area, " ".join(str(raw.get("question") or "").split())))
        key = (
            existing.name
            if existing
            else next(g.name for g in new if normalize_topic(g.name) == normalize_topic(name))
        )
        for number in raw.get("cases") or []:
            if isinstance(number, int) and 1 <= number <= len(batch) and number not in used:
                used.add(number)
                additions.setdefault(key, []).append(batch[number - 1].topic)
    return additions, new
