from pathlib import Path

from context_holder.index import Index
from context_holder.searcheval import evaluate_search, load_queries
from context_holder.wiki import Wiki
from tests.conftest import FakeEmbedder
from tests.test_index import write


def test_load_queries(tmp_path: Path):
    path = tmp_path / "q.yaml"
    path.write_text(
        "queries:\n  - {query: a, kind: ko-en, expected: [https://t/1]}\n"
        "  - {query: b, kind: detail, expected: []}\n",
        encoding="utf-8",
    )

    queries = load_queries(path)

    assert [(q.query, q.kind, q.expected) for q in queries] == [
        ("a", "ko-en", ("https://t/1",)),
        ("b", "detail", ()),
    ]


def test_evaluate_counts_hits_by_kind_and_separates_unanswerable(tmp_path: Path):
    from context_holder.searcheval import EvalQuery

    wiki = Wiki(tmp_path)
    write(wiki, "https://t/kafka", "Kafka 암호화", ("종단 간 암호화",))
    write(wiki, "https://t/cache", "캐시 전략", ("이벤트 기반 만료",))
    index = Index(tmp_path / "index.db")
    embedder = FakeEmbedder()
    index.sync(wiki, embedder)
    queries = [
        EvalQuery("kafka 암호화", "en-ko", ("https://t/kafka",)),
        EvalQuery("캐시", "detail", ("https://t/nowhere",)),
        EvalQuery("redlock", "detail", ()),
    ]

    result = evaluate_search(index, queries, embedder)

    assert (result.hits, result.total) == (1, 2)
    assert result.by_kind == {"en-ko": (1, 1), "detail": (0, 1)}
    assert result.misses == ("캐시",)
    assert len(result.answered_top) == 2 and len(result.unanswered_top) == 1


def test_keyword_only_evaluation_has_no_similarities(tmp_path: Path):
    from context_holder.searcheval import EvalQuery

    wiki = Wiki(tmp_path)
    write(wiki, "https://t/kafka", "Kafka 암호화", ("종단 간 암호화",))
    index = Index(tmp_path / "index.db")
    index.sync(wiki)

    result = evaluate_search(index, [EvalQuery("없는말", "detail", ())], None)

    assert result.total == 0 and result.unanswered_top == ()
