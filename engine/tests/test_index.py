from datetime import UTC, datetime
from pathlib import Path

from context_holder.classify import Bucket, Decision
from context_holder.collect import Article
from context_holder.enrich import Confidence, DecisionRecord, Enrichment
from context_holder.index import Index
from context_holder.textproc import search_tokens
from context_holder.wiki import Wiki, article_id
from tests.conftest import FakeEmbedder

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def write(
    wiki: Wiki,
    url: str,
    title: str,
    key_points: tuple[str, ...],
    bucket: Bucket = Bucket.SAVE,
    topic: str | None = None,
    tags: tuple[str, ...] = (),
) -> str:
    article = Article("toss", url, title, NOW, "본문", "h")
    decision = (
        DecisionRecord(
            topic, "문제", ("A", "B"), "B", ("빠름 ↔ 복잡",), "", (), (), Confidence.HIGH
        )
        if topic
        else None
    )
    enrichment = Enrichment(("요약 한 줄",), key_points, tags, decision, None, 0.0)
    wiki.write_article(article, Decision(url, 0.9, bucket, "백엔드", (), ()), enrichment, NOW)
    return article_id(url)


def test_search_tokens_split_hangul_into_bigrams_and_keep_words():
    assert search_tokens("캐시 무효화 전략") == ["캐시", "무효", "효화", "전략"]
    assert search_tokens("Transactional-Outbox CDC 패턴을") == [
        "transactional",
        "outbox",
        "cdc",
        "패턴",
        "턴을",
    ]
    assert search_tokens("락") == ["락"]


def test_wiki_articles_are_read_back_with_fields(tmp_path: Path):
    wiki = Wiki(tmp_path)
    aid = write(
        wiki, "https://t/a", "아웃박스 도입기", ("CDC 릴레이로 Kafka 발행",), topic="이벤트 발행"
    )

    [stored] = list(wiki.articles())

    assert stored.id == aid
    assert stored.title == "아웃박스 도입기"
    assert stored.key_points == ("CDC 릴레이로 Kafka 발행",)
    assert stored.summary == ("요약 한 줄",)
    assert stored.decision["topic"] == "이벤트 발행"
    assert stored.bucket is Bucket.SAVE


def test_keyword_search_ranks_matching_articles(tmp_path: Path):
    wiki = Wiki(tmp_path)
    outbox = write(
        wiki, "https://t/outbox", "주문 이벤트 유실 막기", ("Transactional Outbox와 CDC 릴레이",)
    )
    cache = write(
        wiki, "https://t/cache", "캐시 무효화 전략", ("이벤트 기반 만료로 캐시를 지운다",)
    )
    index = Index(tmp_path / "index.db")
    index.sync(wiki)

    assert [h.article_id for h in index.keyword("outbox 패턴")][:1] == [outbox]
    assert [h.article_id for h in index.keyword("캐시")][:1] == [cache]
    assert index.keyword("없는단어xyz") == []


def test_decision_and_tags_outrank_summary_mentions(tmp_path: Path):
    wiki = Wiki(tmp_path)
    mention = write(wiki, "https://t/m", "글 하나", ("메시지 큐를 잠깐 언급",))
    decided = write(
        wiki, "https://t/d", "글 둘", ("다른 내용",), topic="메시지 큐 선택", tags=("kafka",)
    )
    index = Index(tmp_path / "index.db")
    index.sync(wiki)

    ranked = [h.article_id for h in index.keyword("메시지 큐")]

    assert ranked.index(decided) < ranked.index(mention)


def test_sync_is_incremental_and_removes_deleted_articles(tmp_path: Path):
    wiki = Wiki(tmp_path)
    first = write(wiki, "https://t/1", "첫 글", ("하나",))
    index = Index(tmp_path / "index.db")

    assert index.sync(wiki) == (1, 0)  # (갱신, 삭제)
    assert index.sync(wiki) == (0, 0)

    write(wiki, "https://t/2", "둘째 글", ("둘",))
    assert index.sync(wiki) == (1, 0)

    next(
        p for p in (tmp_path / "wiki").rglob("*.md") if first in p.read_text(encoding="utf-8")
    ).unlink()
    assert index.sync(wiki) == (0, 1)
    assert index.get(first) is None


def test_inbox_articles_are_indexed_with_their_bucket(tmp_path: Path):
    wiki = Wiki(tmp_path)
    aid = write(wiki, "https://t/i", "경계 글", ("벡터 DB 운영",), bucket=Bucket.INBOX)
    index = Index(tmp_path / "index.db")
    index.sync(wiki)

    assert index.get(aid).bucket is Bucket.INBOX
    assert [h.article_id for h in index.keyword("벡터", buckets=(Bucket.SAVE,))] == []
    assert [h.article_id for h in index.keyword("벡터")] == [aid]


def test_query_without_searchable_characters_returns_nothing(tmp_path: Path):
    index = Index(tmp_path / "index.db")

    assert index.keyword("?! ...") == []


def test_pending_articles_are_read_without_sections(tmp_path: Path):
    wiki = Wiki(tmp_path)
    article = Article("toss", "https://t/p", "대기 글", NOW, "본문", "h")
    wiki.write_article(article, Decision(article.url, 0.9, Bucket.SAVE, "", (), ()), None, NOW)

    [stored] = list(wiki.articles())

    assert stored.status == "pending"
    assert stored.summary == () and stored.key_points == ()


def test_old_index_gets_screen_columns_without_recomputing_vectors(tmp_path: Path):
    import sqlite3

    wiki = Wiki(tmp_path / "wiki")
    aid = write(wiki, "https://t/a", "Kafka 암호화", ("종단 간 암호화",))
    embedder = FakeEmbedder()
    path = tmp_path / "index.db"
    Index(path).sync(wiki, embedder)
    # 화면용 컬럼이 없던 예전 인덱스로 되돌린다
    db = sqlite3.connect(path)
    for column in ("collected", "relevance", "summary_line", "media"):
        db.execute(f"ALTER TABLE articles DROP COLUMN {column}")
    db.execute("DELETE FROM meta WHERE key = 'schema'")
    db.commit()
    db.close()

    index = Index(path)
    assert index.get(aid).collected is None
    calls = embedder.calls
    assert index.sync(wiki, embedder) == (0, 0)  # 다시 색인한 글 없음

    article = index.get(aid)
    assert embedder.calls == calls  # 벡터를 다시 계산하지 않았다
    assert article.collected == NOW.isoformat()
    assert article.relevance == 0.9
    assert article.summary_line == "요약 한 줄"
    assert article.media is None
    assert Index(path)._backfill is False  # 다음에 열 때는 다시 채우지 않는다


def test_vector_can_look_at_chosen_pieces_only(tmp_path: Path):
    wiki = Wiki(tmp_path / "wiki")
    embedder = FakeEmbedder(dim=1024)
    aid = write(wiki, "https://t/1", "캐시 전략", ("레디스 클러스터 운영",))
    index = Index(tmp_path / "index.db")
    index.sync(wiki, embedder)
    query = embedder.query("레디스")
    assert [h.label for h in index.vector(query)] == ["핵심 내용 1번째 줄"]
    title_only = index.vector(query, labels=("제목",))
    assert [(h.article_id, h.label) for h in title_only] == [(aid, "제목")]
    assert title_only[0].score < 0.1  # 제목에는 레디스가 없다
