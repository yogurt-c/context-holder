from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from context_holder.llm import ErrorKind, LlmError, LlmResult
from context_holder.sync import run_sync
from context_holder.wiki import Wiki
from tests.conftest import LONG_BODY, article_page, rss

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
FEED = "https://blog.example.com/feed"


def setup_wiki(tmp_path: Path) -> Wiki:
    wiki = Wiki(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/sources.yaml").write_text(
        f"sources:\n  - {{id: ex, name: Ex, region: domestic, feed: '{FEED}'}}\n", encoding="utf-8"
    )
    wiki.ensure_config()
    return wiki


def feed_client(make_client, links: list[str], extra: dict | None = None):
    items = [
        {"title": f"글 {i}", "link": link, "content": article_page(f"글 {i}", LONG_BODY + link)}
        for i, link in enumerate(links, start=1)
    ]
    table = {FEED: httpx.Response(200, text=rss(items), headers={"etag": '"e1"'}), **(extra or {})}

    def handler(request: httpx.Request) -> httpx.Response:
        return table.get(str(request.url), httpx.Response(404))

    return make_client(handler)


class ScriptedLlm:
    """분류 응답은 scores에서, 요약 응답은 enrich_replies에서 차례로 준다."""

    def __init__(self, scores: list[float], enrich_replies: list | None = None):
        self.scores = scores
        self.enrich_replies = list(enrich_replies or [])
        self.enrich_calls = 0

    def structured(self, prompt, system, schema):
        if "items" in schema["properties"]:
            items = [
                {"id": str(i), "score": s, "topic": "백엔드", "plus": [], "minus": []}
                for i, s in enumerate(self.scores, start=1)
                if f'<article id="{i}">' in prompt
            ]
            return LlmResult({"items": items}, 0.01, 10)
        self.enrich_calls += 1
        reply = self.enrich_replies.pop(0) if self.enrich_replies else ENRICHED
        if isinstance(reply, Exception):
            raise reply
        return LlmResult(reply, 0.02, 10)


ENRICHED = {
    "summary": ["요약 1", "요약 2", "요약 3"],
    "key_points": ["핵심 1", "핵심 2", "핵심 3", "핵심 4", "핵심 5"],
    "tags": ["cache"],
    "decision": None,
}


def statuses(root: Path, folder: str) -> list[str]:
    return [
        yaml.safe_load(p.read_text(encoding="utf-8").split("---\n")[1])["status"]
        for p in sorted((root / folder).rglob("*.md"))
    ]


def test_sync_collects_classifies_enriches_and_writes(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    links = [
        "https://blog.example.com/a",
        "https://blog.example.com/b",
        "https://blog.example.com/c",
    ]
    llm = ScriptedLlm([0.9, 0.45, 0.1])

    report = run_sync(wiki, feed_client(make_client, links), llm, now=NOW, days=7)

    assert (report.saved, report.inboxed, report.dropped) == (1, 1, 1)
    assert statuses(tmp_path, "wiki") == ["done"]
    assert statuses(tmp_path, "inbox") == ["done"]
    assert llm.enrich_calls == 2  # 버린 글은 요약하지 않는다
    assert wiki.seen_urls() == frozenset(links)
    assert report.etags == {"ex": '"e1"'}
    assert report.cost_usd > 0
    assert report.saved_by_source == {"ex": 1}


def test_second_sync_skips_seen_articles(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    links = ["https://blog.example.com/a"]
    run_sync(wiki, feed_client(make_client, links), ScriptedLlm([0.9]), now=NOW, days=7)
    llm = ScriptedLlm([0.9])

    report = run_sync(wiki, feed_client(make_client, links), llm, now=NOW, days=7)

    assert (report.saved, report.inboxed, report.dropped) == (0, 0, 0)
    assert llm.enrich_calls == 0


def test_enrich_failure_writes_pending_and_next_sync_completes_it(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    link = "https://blog.example.com/a"
    failing = ScriptedLlm([0.9], [LlmError(ErrorKind.FAILED, "잠깐 실패")])

    first = run_sync(wiki, feed_client(make_client, [link]), failing, now=NOW, days=7)

    assert first.pending_enrich == 1
    assert statuses(tmp_path, "wiki") == ["pending"]

    page = {link: httpx.Response(200, text=article_page("글", LONG_BODY))}
    second = run_sync(
        wiki, feed_client(make_client, [link], page), ScriptedLlm([]), now=NOW, days=7
    )

    assert second.completed == 1
    assert statuses(tmp_path, "wiki") == ["done"]


def test_auth_or_limit_errors_stop_further_calls(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    links = ["https://blog.example.com/a", "https://blog.example.com/b"]
    llm = ScriptedLlm([0.9, 0.9], [LlmError(ErrorKind.LIMIT, "한도")])

    report = run_sync(wiki, feed_client(make_client, links), llm, now=NOW, days=7)

    assert llm.enrich_calls == 1  # 한도에 걸리면 나머지는 부르지 않고 대기로 둔다
    assert report.pending_enrich == 2
    assert report.stopped_reason == "limit: 한도"


def test_classification_failure_leaves_articles_unseen_for_retry(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)

    class DownLlm:
        def structured(self, prompt, system, schema):
            raise LlmError(ErrorKind.AUTH, "로그인 만료")

    report = run_sync(
        wiki, feed_client(make_client, ["https://blog.example.com/a"]), DownLlm(), now=NOW, days=7
    )

    assert report.pending_classify == 1
    assert wiki.seen_urls() == frozenset()


def test_pending_article_whose_page_is_gone_stays_pending(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    link = "https://blog.example.com/a"
    run_sync(
        wiki,
        feed_client(make_client, [link]),
        ScriptedLlm([0.9], [LlmError(ErrorKind.FAILED, "x")]),
        now=NOW,
        days=7,
    )

    report = run_sync(wiki, feed_client(make_client, []), ScriptedLlm([]), now=NOW, days=7)

    assert report.completed == 0
    assert report.still_pending == 1
    assert statuses(tmp_path, "wiki") == ["pending"]


def test_first_sync_looks_back_further(make_client, tmp_path: Path):
    from context_holder.sync import FIRST_SYNC_DAYS, default_days

    wiki = setup_wiki(tmp_path)
    assert default_days(wiki) == FIRST_SYNC_DAYS

    run_sync(
        wiki,
        feed_client(make_client, ["https://blog.example.com/a"]),
        ScriptedLlm([0.1]),
        now=NOW,
        days=7,
    )

    assert default_days(wiki) < FIRST_SYNC_DAYS


def _leave_one_pending(make_client, wiki: Wiki, link: str) -> None:
    run_sync(
        wiki,
        feed_client(make_client, [link]),
        ScriptedLlm([0.9], [LlmError(ErrorKind.FAILED, "x")]),
        now=NOW,
        days=7,
    )


def test_limit_during_pending_retry_skips_todays_claude_calls(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    old = "https://blog.example.com/a"
    _leave_one_pending(make_client, wiki, old)
    page = {old: httpx.Response(200, text=article_page("글", LONG_BODY))}
    llm = ScriptedLlm([0.9], [LlmError(ErrorKind.LIMIT, "한도")])

    report = run_sync(
        wiki,
        feed_client(make_client, [old, "https://blog.example.com/new"], page),
        llm,
        now=NOW,
        days=7,
    )

    assert report.still_pending == 1
    assert report.pending_classify == 1  # 새 글은 분류도 하지 않고 다음으로 미룬다
    assert report.stopped_reason == "limit: 한도"
    assert llm.enrich_calls == 1


def test_pending_article_of_removed_source_stays_pending(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    _leave_one_pending(make_client, wiki, "https://blog.example.com/a")
    (tmp_path / "config/sources.yaml").write_text(
        "sources:\n  - {id: other, name: O, region: domestic, feed: 'https://o.example/feed'}\n",
        encoding="utf-8",
    )

    report = run_sync(wiki, feed_client(make_client, []), ScriptedLlm([]), now=NOW, days=7)

    assert report.still_pending == 1


def test_same_article_under_another_url_is_not_written_twice(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    page = article_page("같은 글", LONG_BODY)

    def client_with(link: str):
        from tests.conftest import rss

        items = [{"title": "같은 글", "link": link, "content": page}]
        return make_client(lambda r: httpx.Response(200, text=rss(items)))

    run_sync(
        wiki, client_with("https://blog.example.com/slug"), ScriptedLlm([0.9]), now=NOW, days=7
    )
    second = run_sync(
        wiki, client_with("https://blog.example.com/123"), ScriptedLlm([0.9]), now=NOW, days=7
    )

    assert second.saved == 0
    assert len(list((tmp_path / "wiki").rglob("*.md"))) == 1


def test_sync_writes_paragraph_vectors_and_survives_embedding_errors(make_client, tmp_path: Path):
    from tests.conftest import FakeEmbedder

    wiki = setup_wiki(tmp_path)
    links = ["https://blog.example.com/a"]

    run_sync(
        wiki,
        feed_client(make_client, links),
        ScriptedLlm([0.9]),
        now=NOW,
        days=7,
        embedder=FakeEmbedder(),
    )

    assert list((tmp_path / "vectors/fake").glob("*.f16"))

    class Broken(FakeEmbedder):
        def passages(self, texts):
            raise RuntimeError("모델 없음")

    report = run_sync(
        wiki,
        feed_client(make_client, ["https://blog.example.com/b"]),
        ScriptedLlm([0.9]),
        now=NOW,
        days=7,
        embedder=Broken("broken"),
    )
    assert report.saved == 1  # 벡터화가 실패해도 글은 저장한다


def test_completed_pending_article_gets_vectors(make_client, tmp_path: Path):
    from tests.conftest import FakeEmbedder

    wiki = setup_wiki(tmp_path)
    link = "https://blog.example.com/a"
    run_sync(
        wiki,
        feed_client(make_client, [link]),
        ScriptedLlm([0.9], [LlmError(ErrorKind.FAILED, "x")]),
        now=NOW,
        days=7,
    )
    page = {link: httpx.Response(200, text=article_page("글", LONG_BODY))}

    run_sync(
        wiki,
        feed_client(make_client, [link], page),
        ScriptedLlm([]),
        now=NOW,
        days=7,
        embedder=FakeEmbedder(),
    )

    assert len(list((tmp_path / "vectors/fake").glob("*.f16"))) == 1
