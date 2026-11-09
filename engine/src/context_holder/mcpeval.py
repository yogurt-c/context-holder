"""결정 질문 평가셋(engine/eval/decision-queries.yaml)으로 MCP tool의 recall@5를 잰다 (plan 8장).

세 가지로 잰다.
- find_decision_cases(problem=질문): 질문 전체를 그대로 넣는다.
- search_wiki([질문]): 질문 하나만 넣는다. Claude의 질의 확장은 재지 않는다(하한).
- Claude 호출: Claude Code가 실제로 보낸 find_decision_cases 입력(topic · problem)을 다시 넣는다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from context_holder.mcptools import WikiTools
from context_holder.search import SearchHit

TOP_K = 5
DECISION = "find_decision_cases"
SEARCH = "search_wiki"
CLAUDE = "find_decision_cases (Claude 호출)"


@dataclass(frozen=True)
class Call:
    topic: str | None
    problem: str | None


@dataclass(frozen=True)
class DecisionQuery:
    query: str
    kind: str
    expected: tuple[str, ...]  # 비어 있으면 위키에 답이 없다
    claude_calls: tuple[Call, ...] = ()


@dataclass(frozen=True)
class ToolScore:
    hits: int
    total: int
    misses: tuple[str, ...]


def load_decision_queries(path: Path) -> list[DecisionQuery]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        DecisionQuery(
            q["query"],
            q["kind"],
            tuple(q.get("expected") or ()),
            tuple(Call(c.get("topic"), c.get("problem")) for c in q.get("claude_calls") or ()),
        )
        for q in data["queries"]
    ]


def evaluate_mcp(tools: WikiTools, queries: Sequence[DecisionQuery]) -> dict[str, ToolScore]:
    answered = [q for q in queries if q.expected]
    decision = [
        (q.query, tools.decision_hits(problem=q.query, limit=TOP_K)[0], q) for q in answered
    ]
    search = [(q.query, tools.search_hits([q.query], limit=TOP_K)[0], q) for q in answered]
    claude = [
        (f"{c.topic} | {c.problem}", tools.decision_hits(c.topic, c.problem, TOP_K)[0], q)
        for q in answered
        for c in q.claude_calls
    ]
    return {DECISION: _score(decision), SEARCH: _score(search), CLAUDE: _score(claude)}


def _score(trials: list[tuple[str, list[SearchHit], DecisionQuery]]) -> ToolScore:
    misses = [
        label for label, hits, q in trials if not any(h.article.url in q.expected for h in hits)
    ]
    return ToolScore(len(trials) - len(misses), len(trials), tuple(misses))
