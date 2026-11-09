from pathlib import Path

from context_holder.mcpeval import (
    CLAUDE,
    DECISION,
    SEARCH,
    Call,
    DecisionQuery,
    evaluate_mcp,
    load_decision_queries,
)
from tests.test_index import write
from tests.test_mcp import make_tools, wiki  # noqa: F401 - fixture


def test_load_queries_with_claude_calls(tmp_path: Path):
    path = tmp_path / "q.yaml"
    path.write_text(
        "queries:\n"
        "  - query: a\n    kind: backend\n    expected: [https://t/1]\n"
        "    claude_calls:\n      - {topic: 주제, problem: 문제}\n      - {problem: 문제만}\n"
        "  - {query: b, kind: infra, expected: []}\n",
        encoding="utf-8",
    )

    queries = load_decision_queries(path)

    assert queries == [
        DecisionQuery(
            "a", "backend", ("https://t/1",), (Call("주제", "문제"), Call(None, "문제만"))
        ),
        DecisionQuery("b", "infra", ()),
    ]


def test_scores_each_way_on_answered_questions_only(wiki, tmp_path):  # noqa: F811
    write(wiki, "https://t/outbox", "Outbox 도입기", ("커밋 후 발행",), topic="이벤트 발행")
    write(wiki, "https://t/cache", "캐시 전략", ("이벤트 기반 만료",))  # 결정 기록 없음
    tools = make_tools(wiki, tmp_path)
    queries = [
        DecisionQuery(
            "커밋 후 발행",
            "backend",
            ("https://t/outbox",),
            (Call("이벤트 발행", "커밋 후 발행"),),
        ),
        DecisionQuery("이벤트 기반 만료", "backend", ("https://t/cache",), (Call("캐시", "만료"),)),
        DecisionQuery("redlock", "backend", (), (Call("락", "redlock"),)),  # 답 없음: 채점 안 함
    ]

    scores = evaluate_mcp(tools, queries)

    assert (scores[DECISION].hits, scores[DECISION].total) == (1, 2)
    assert scores[DECISION].misses == ("이벤트 기반 만료",)  # 결정 기록 없는 글은 사례가 아니다
    assert (scores[SEARCH].hits, scores[SEARCH].total, scores[SEARCH].misses) == (2, 2, ())
    assert (scores[CLAUDE].hits, scores[CLAUDE].total) == (1, 2)
    assert scores[CLAUDE].misses == ("캐시 | 만료",)
