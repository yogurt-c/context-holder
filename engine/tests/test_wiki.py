from datetime import UTC, datetime
from pathlib import Path

import yaml

from context_holder.classify import Bucket, Decision
from context_holder.collect import Article
from context_holder.enrich import Confidence, DecisionRecord, Enrichment, Media
from context_holder.wiki import Wiki, article_id

NOW = datetime(2026, 11, 5, 9, 14, tzinfo=UTC)


def article(
    url: str = "https://toss.tech/article/cache-strategy/", title: str = "캐시 전략"
) -> Article:
    return Article("toss", url, title, datetime(2026, 11, 4, tzinfo=UTC), "본문", "h")


def decision(url: str, bucket: Bucket = Bucket.SAVE, score: float = 0.9) -> Decision:
    return Decision(url, score, bucket, "백엔드", ("캐시",), ())


def enrichment(with_decision: bool = True) -> Enrichment:
    record = DecisionRecord(
        topic="캐시 무효화",
        problem="오래된 캐시",
        options=("TTL", "이벤트 기반 만료"),
        chosen="이벤트 기반 만료",
        tradeoffs=("정확함 ↔ 이벤트 운영",),
        outcome="오류 90% 감소",
        evidence=("We switched to event-based invalidation.",),
        unverified=("없는 문장",),
        confidence=Confidence.LOW,
    )
    return Enrichment(
        summary=("요약 1", "요약 2", "요약 3"),
        key_points=("핵심 1", "핵심 2"),
        tags=("cache", "kafka"),
        decision=record if with_decision else None,
        media=Media("https://youtu.be/x", "youtube"),
        cost_usd=0.02,
    )


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---\n")[1])


def test_ensure_config_seeds_defaults_once(tmp_path: Path):
    wiki = Wiki(tmp_path)

    wiki.ensure_config()
    (tmp_path / "config/sources.yaml").write_text(
        "sources:\n  - {id: a, name: A, region: domestic, feed: 'https://a.example/feed'}\n",
        encoding="utf-8",
    )
    wiki.ensure_config()

    assert [s.id for s in wiki.sources()] == ["a"]  # 사용자가 고친 설정은 덮어쓰지 않는다
    assert wiki.profile().save_at == 0.6


def test_saved_article_is_written_with_frontmatter_and_korean_body(tmp_path: Path):
    wiki = Wiki(tmp_path)
    a = article()

    path = wiki.write_article(a, decision(a.url), enrichment(), collected=NOW)

    assert path == tmp_path / "wiki/toss/2026/cache-strategy.md"
    meta = frontmatter(path)
    assert meta["id"] == article_id(a.url)
    assert meta["status"] == "done"
    assert meta["relevance"] == {
        "score": 0.9,
        "bucket": "save",
        "topic": "백엔드",
        "plus": ["캐시"],
        "minus": [],
    }
    assert meta["tags"] == ["cache", "kafka"]
    assert meta["decision"]["chosen"] == "이벤트 기반 만료"
    assert meta["decision"]["confidence"] == "low"
    assert meta["media"]["provider"] == "youtube"
    body = path.read_text(encoding="utf-8")
    for text in (
        "## 요약",
        "- 요약 1",
        "## 핵심 내용",
        "## 결정 기록: 캐시 무효화",
        "원문에서 찾지 못한 근거 1건",
    ):
        assert text in body
    assert f"[원문 보기]({a.url})" in body
    assert "본문" not in body  # 원문 전문은 저장하지 않는다


def test_inbox_article_goes_to_inbox_and_drop_is_never_written(tmp_path: Path):
    wiki = Wiki(tmp_path)
    a = article()

    path = wiki.write_article(
        a, decision(a.url, Bucket.INBOX, 0.5), enrichment(False), collected=NOW
    )

    assert path.parent == tmp_path / "inbox/toss/2026"
    assert frontmatter(path)["decision"] is None


def test_non_ascii_url_falls_back_to_article_id(tmp_path: Path):
    wiki = Wiki(tmp_path)
    a = article(url="https://oliveyoung.tech/2026-10-29/%EC%98%AC%EB%A6%AC%EB%B8%8C%EC%98%81")

    path = wiki.write_article(a, decision(a.url), enrichment(), collected=NOW)

    assert path.name == f"{article_id(a.url)}.md"


def test_slug_collision_with_another_url_gets_suffix(tmp_path: Path):
    wiki = Wiki(tmp_path)
    first = article(url="https://toss.tech/a/post")
    second = article(url="https://toss.tech/b/post")

    p1 = wiki.write_article(first, decision(first.url), enrichment(), collected=NOW)
    p2 = wiki.write_article(second, decision(second.url), enrichment(), collected=NOW)

    assert p1.name == "post.md"
    assert p2.name == f"post-{article_id(second.url)[:6]}.md"


def test_pending_article_can_be_completed_later(tmp_path: Path):
    wiki = Wiki(tmp_path)
    a = article()

    path = wiki.write_article(a, decision(a.url), None, collected=NOW, reason="limit: 한도")
    [pending] = wiki.pending()
    assert pending.url == a.url and pending.source_id == "toss"
    assert pending.published == a.published
    assert frontmatter(path)["status"] == "pending"
    assert "요약 대기" in path.read_text(encoding="utf-8")

    wiki.complete(pending, enrichment(), collected=NOW)

    assert wiki.pending() == []
    meta = frontmatter(path)
    assert meta["status"] == "done"
    assert meta["relevance"]["score"] == 0.9


def test_existing_done_article_is_not_overwritten(tmp_path: Path):
    wiki = Wiki(tmp_path)
    a = article()
    path = wiki.write_article(a, decision(a.url), enrichment(), collected=NOW)
    before = path.read_text(encoding="utf-8")

    again = wiki.write_article(a, decision(a.url, score=0.1), enrichment(False), collected=NOW)

    assert again == path
    assert path.read_text(encoding="utf-8") == before


def test_seen_records_are_appended_per_month(tmp_path: Path):
    wiki = Wiki(tmp_path)
    a = article()

    wiki.record_seen([decision(a.url, Bucket.DROP, 0.1)], when=NOW)
    wiki.record_seen([decision("https://x/1")], when=datetime(2026, 12, 1, tzinfo=UTC))

    assert wiki.seen_urls() == frozenset({a.url, "https://x/1"})
    assert (tmp_path / "state/seen/2026-11.jsonl").exists()
    assert (tmp_path / "state/seen/2026-12.jsonl").exists()


def test_empty_wiki_has_nothing_seen_or_pending(tmp_path: Path):
    wiki = Wiki(tmp_path)

    assert wiki.seen_urls() == frozenset()
    assert wiki.pending() == []


def test_dropped_articles_are_never_written(tmp_path: Path):
    import pytest

    wiki = Wiki(tmp_path)
    a = article()

    with pytest.raises(ValueError):
        wiki.write_article(a, decision(a.url, Bucket.DROP, 0.1), None, collected=NOW)


def test_url_without_path_uses_article_id(tmp_path: Path):
    wiki = Wiki(tmp_path)
    a = article(url="https://blog.example.com")

    path = wiki.write_article(a, decision(a.url), enrichment(), collected=NOW)

    assert path.name == f"{article_id(a.url)}.md"
