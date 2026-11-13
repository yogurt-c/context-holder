"""Decisions 화면 (plan 7장, docs/ui/decisions.html). 넓은 주제마다 회사별 선택을 나란히 비교한다.

왼쪽은 분야 선택칸과 그 분야의 주제 목록, 오른쪽은 고른 주제의 비교표
(문제 · 결정 · 트레이드오프 · 결과 · 신뢰도 · 출처).
주제 목록은 사례가 많은 순으로 TOPIC_PAGE개씩 커서("더 보기")로 붙인다. 커서는 마지막 주제 이름이다.
주제 찾기(q)는 분야를 가리지 않고 모든 주제에서 찾는다.
주제 묶기는 수집이 끝날 때 한다 (topics.settle). 이 화면에서는 사례를 다른 주제로 옮기고
(사례 옮기기 창), 주제의 이름 · 분야 · 질문을 고친다 (비교표 머리).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from context_holder import topics as topicmod
from context_holder.index import Index, IndexedArticle, normalize_topic
from context_holder.llm import ErrorKind
from context_holder.screens.views import Page, after_cursor
from context_holder.wiki import Wiki

AREA_ORDER = topicmod.AREAS
OTHER_AREA = "기타"
MIN_SHOWN = 2  # Search 첫 화면에는 사례가 2건 이상인 주제만 건다 (비교할 수 있는 것)
TOPIC_PAGE = 10
CONFIDENCE = {"high": 3, "medium": 2, "low": 1}
LLM_ERRORS = {  # {name}: Claude · Codex, {tool}: Claude Code · Codex CLI
    ErrorKind.NOT_INSTALLED: "{tool}가 없어요. 설정에서 확인하세요.",
    ErrorKind.AUTH: "{name} 로그인이 필요해요. 설정에서 다시 연결하세요.",
    ErrorKind.LIMIT: "{name} 사용 한도에 걸렸어요. 나중에 다시 시도해 주세요.",
}
LLM_ERROR_DEFAULT = "{name}가 답하지 않았어요. 다시 시도해 주세요."


@dataclass(frozen=True)
class TopicItem:
    name: str
    on: bool


@dataclass(frozen=True)
class Column:
    id: str
    topic: str  # 원래 주제 (사례 옮기기에 쓴다)
    company: str
    date: str  # 2026.09
    title: str
    problem: str
    chosen: str
    alternatives: tuple[str, ...]  # 고르지 않은 선택지
    tradeoffs: tuple[str, ...]
    outcome: str
    confidence: int  # 1~3


@dataclass(frozen=True)
class DecisionsView:
    q: str = ""
    area: str = ""  # 고른 분야. 주제 찾기 중이면 비운다 ("모든 분야")
    areas: list[str] = field(default_factory=list)  # 주제가 있는 분야 (선택칸)
    topics: list[TopicItem] = field(default_factory=list)  # 주제 목록 첫 쪽
    after: str | None = None  # 다음 쪽 커서
    selected: topicmod.Group | None = None
    columns: list[Column] = field(default_factory=list)
    group_total: int = 0  # 넓은 주제 수
    case_total: int = 0  # 결정 사례 수
    unassigned: int = 0  # 아직 분류하지 않은 원래 주제 수
    markdown: str = ""  # "비교표 복사"로 복사할 내용
    area_choices: tuple[str, ...] = AREA_ORDER  # 주제 편집에서 고르는 분야

    def url(self, **change: Any) -> str:
        values = {"area": self.area, "sel": self.selected.name if self.selected else ""}
        values |= {"q": self.q} | change
        pairs = [(k, v) for k, v in values.items() if v]
        return "/decisions" + (f"?{urlencode(pairs)}" if pairs else "")

    def more_url(self, after: str) -> str:
        """주제 목록 다음 쪽. 분야 · 찾는 말 · 고른 주제(선택 표시)를 그대로 넘긴다."""
        return "/decisions/more" + self.url(after=after).removeprefix("/decisions")


@dataclass(frozen=True)
class _Catalog:
    """결정 사례를 넓은 주제로 묶은 것. 화면 첫 쪽과 "더 보기"가 같은 순서를 쓴다."""

    topics: topicmod.TopicMap
    members: dict[str, list[IndexedArticle]]
    by_area: dict[str, list[topicmod.Group]]  # 분야마다 사례가 많은 순
    case_total: int
    unassigned: int

    def candidates(self, area: str, q: str) -> list[topicmod.Group]:
        if not q:
            return self.by_area.get(area, [])
        key = normalize_topic(q)
        found = [g for groups in self.by_area.values() for g in groups]
        return sorted(
            (g for g in found if key in normalize_topic(g.name)),
            key=lambda g: (-len(self.members[g.name]), g.name),
        )


def build_decisions(
    wiki: Wiki,
    index: Index,
    names: dict[str, str],
    sel: str | None = None,
    q: str = "",
    area: str = "",
) -> DecisionsView:
    catalog = _catalog(wiki, index)
    q = q.strip()[:60]
    picked = catalog.topics.group(sel) if sel else None
    if picked is not None and picked.name not in catalog.members:
        picked = None
    areas = list(catalog.by_area)
    if q:
        area = ""
    elif area not in catalog.by_area:  # 분야를 안 골랐으면 고른 주제의 분야, 그것도 없으면 첫 분야
        area = _area_of(picked) if picked else (areas[0] if areas else "")
    candidates = catalog.candidates(area, q)
    selected = picked if picked in candidates else (candidates[0] if candidates else None)
    # 고른 주제가 첫 쪽 밖에 있으면 그 주제까지 보인다
    shown = TOPIC_PAGE
    if selected is not None:
        shown = max(shown, candidates.index(selected) + 1)
    page = candidates[:shown]
    columns = _columns(wiki, catalog.members.get(selected.name, []), names) if selected else []
    return DecisionsView(
        q=q,
        area=area,
        areas=areas,
        topics=[TopicItem(g.name, g == selected) for g in page],
        after=page[-1].name if len(candidates) > shown else None,
        selected=selected,
        columns=columns,
        group_total=len(catalog.members),
        case_total=catalog.case_total,
        unassigned=catalog.unassigned,
        markdown=_markdown(selected, columns) if selected else "",
    )


def decisions_more(
    wiki: Wiki, index: Index, area: str, q: str, sel: str, after: str
) -> tuple[Page, DecisionsView]:
    """주제 목록 "더 보기": after 주제 다음 TOPIC_PAGE개. 다음 버튼 주소를 만들 뷰도 준다."""
    catalog = _catalog(wiki, index)
    q = q.strip()[:60]
    candidates = catalog.candidates("" if q else area, q)
    page = after_cursor(candidates, after, TOPIC_PAGE, lambda g: g.name)
    items = [TopicItem(g.name, g.name == sel) for g in page.items]
    selected = catalog.topics.group(sel) if sel else None
    return Page(items, page.after), DecisionsView(q=q, area="" if q else area, selected=selected)


def browse_topics(wiki: Wiki, index: Index, per_area: int = 3) -> list[tuple[str, list[str]]]:
    """분야마다 사례가 많은 주제 몇 개. Search 첫 화면에서 비교표로 바로 간다.

    비교할 수 있는 주제(사례 MIN_SHOWN건 이상)만 건다. 그런 주제가 아직 없으면 있는 대로 건다.
    """
    catalog = _catalog(wiki, index)
    comparable = {
        area: [g for g in groups if len(catalog.members[g.name]) >= MIN_SHOWN]
        for area, groups in catalog.by_area.items()
    }
    if not any(comparable.values()):
        comparable = catalog.by_area
    return [(area, [g.name for g in gs[:per_area]]) for area, gs in comparable.items() if gs]


def _catalog(wiki: Wiki, index: Index) -> _Catalog:
    topics = topicmod.load(wiki)
    articles = index.select("WHERE decision_topic IS NOT NULL")
    members: dict[str, list[IndexedArticle]] = {}
    loose: set[str] = set()
    for a in articles:
        group = topics.group_of(a.decision_topic)
        if group is None:
            loose.add(normalize_topic(a.decision_topic or ""))
        else:
            members.setdefault(group, []).append(a)
    by_area: dict[str, list[topicmod.Group]] = {}
    for g in topics.groups:
        if g.name in members:
            by_area.setdefault(_area_of(g), []).append(g)
    order = [*AREA_ORDER, OTHER_AREA]
    ordered = {
        area: sorted(by_area[area], key=lambda g: (-len(members[g.name]), g.name))
        for area in order
        if area in by_area
    }
    return _Catalog(topics, members, ordered, len(articles), len(loose))


def _area_of(group: topicmod.Group) -> str:
    return group.area if group.area in AREA_ORDER else OTHER_AREA


def _columns(wiki: Wiki, cases: list[IndexedArticle], names: dict[str, str]) -> list[Column]:
    columns = []
    for a in sorted(cases, key=lambda a: a.published or "", reverse=True):
        stored = wiki.read(Path(a.path))
        if stored is None or not stored.decision:  # 다른 기기의 pull로 사라졌다
            continue
        d = stored.decision
        chosen = str(d.get("chosen") or "")
        options = tuple(str(o) for o in d.get("options") or [])
        columns.append(
            Column(
                id=a.id,
                topic=a.decision_topic or "",
                company=names.get(a.source_id, a.source_id),
                date=(a.published or "")[:7].replace("-", "."),
                title=stored.title,
                problem=str(d.get("problem") or ""),
                chosen=chosen,
                alternatives=tuple(
                    o for o in options if normalize_topic(o) != normalize_topic(chosen)
                ),
                tradeoffs=tuple(str(t) for t in d.get("tradeoffs") or []),
                outcome=str(d.get("outcome") or ""),
                confidence=CONFIDENCE.get(str(d.get("confidence")), 1),
            )
        )
    return columns


def _markdown(group: topicmod.Group, columns: list[Column]) -> str:
    lines = [f"## {group.name}"]
    if group.question:
        lines.append(group.question)
    lines += [
        "",
        "| 회사 | 문제 | 선택 | 다른 선택지 | 트레이드오프 | 결과 |",
        "|---|---|---|---|---|---|",
    ]
    for c in columns:
        cells = [
            f"{c.company} ({c.date})" if c.date else c.company,
            c.problem,
            c.chosen,
            ", ".join(c.alternatives),
            "; ".join(c.tradeoffs),
            c.outcome or "-",
        ]
        lines.append("| " + " | ".join(cell.replace("|", "/") for cell in cells) + " |")
    return "\n".join(lines)


# ---------- 주제 고치기 ----------


@dataclass(frozen=True)
class EditNote:
    """주제를 고친 뒤 머리에 잠깐 보이는 알림. undo는 되돌릴 때 보낼 값 (되돌린 뒤면 None)."""

    message: str
    undo: dict[str, str] | None


def edit_note(before: topicmod.Group, after: topicmod.Group, undone: bool) -> EditNote | None:
    changed = [
        label
        for label, old, new in (
            ("이름", before.name, after.name),
            ("분야", before.area, after.area),
            ("질문", before.question, after.question),
        )
        if old != new
    ]
    if not changed:
        return None
    if undone:
        return EditNote("되돌렸어요", None)
    objects = {"이름": "이름을", "분야": "분야를", "질문": "질문을"}
    what = "주제를" if len(changed) > 1 else objects[changed[0]]
    undo = {"sel": after.name, "name": before.name, "area": before.area}
    return EditNote(f"{what} 바꿨어요", {**undo, "question": before.question, "undo": "true"})


# ---------- 사례 옮기기 창 ----------


@dataclass(frozen=True)
class TopicChoice:
    """옮길 곳으로 고를 수 있는 다른 주제. 창 안에서 이름 · 질문 · 분야로 찾는다."""

    name: str
    area: str
    question: str
    cases: int


@dataclass(frozen=True)
class MoveView:
    group: topicmod.Group
    cases: list[Column]
    targets: list[TopicChoice]  # 사례가 많은 순
    areas: tuple[str, ...] = AREA_ORDER


def build_move(wiki: Wiki, index: Index, names: dict[str, str], sel: str) -> MoveView | None:
    catalog = _catalog(wiki, index)
    group = catalog.topics.group(sel)
    if group is None or group.name not in catalog.members:
        return None
    others = (g for gs in catalog.by_area.values() for g in gs if g.name != group.name)
    choices = (
        TopicChoice(g.name, _area_of(g), g.question, len(catalog.members[g.name])) for g in others
    )
    targets = sorted(choices, key=lambda t: (-t.cases, t.name))
    return MoveView(group, _columns(wiki, catalog.members[group.name], names), targets)
