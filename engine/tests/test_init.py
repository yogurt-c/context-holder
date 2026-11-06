from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import yaml

from context_holder.init import InitSettings, load_state, progress, run_init
from context_holder.llm import ErrorKind, LlmError
from context_holder.wiki import Wiki
from tests.conftest import LONG_BODY, article_page
from tests.test_sync import ScriptedLlm

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
API = "https://blog.example.com/api?page={page}"


def setup_wiki(tmp_path: Path) -> Wiki:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/sources.yaml").write_text(
        f"""
sources:
  - id: ex
    name: Ex
    region: domestic
    feed: https://blog.example.com/feed
    archive:
      kind: json
      url: "{API}"
      items: posts
      link: url
      title: title
      date: at
""",
        encoding="utf-8",
    )
    wiki = Wiki(tmp_path)
    wiki.ensure_config()
    return wiki


def make_routes(make_client, ages_in_days: list[int], gone: set[int] = frozenset()):
    posts = [
        {
            "url": f"https://blog.example.com/p/{i}",
            "title": f"글 {i}",
            "at": (NOW - timedelta(days=age)).isoformat(),
        }
        for i, age in enumerate(ages_in_days, start=1)
    ]
    table = {
        "https://blog.example.com/api?page=1": httpx.Response(200, json={"posts": posts}),
        "https://blog.example.com/api?page=2": httpx.Response(200, json={"posts": []}),
    }
    for i in range(1, len(posts) + 1):
        if i not in gone:
            table[f"https://blog.example.com/p/{i}"] = httpx.Response(
                200, text=article_page(f"글 {i}", LONG_BODY + str(i))
            )

    def handler(request: httpx.Request) -> httpx.Response:
        return table.get(str(request.url), httpx.Response(404))

    return make_client(handler)


def front(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---\n")[1])


def test_init_lists_recent_posts_within_limits_and_writes_them(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    client = make_routes(make_client, [1, 2, 3, 400])  # 마지막 글은 1년보다 오래됨
    llm = ScriptedLlm([0.9, 0.45, 0.1])

    result = run_init(
        wiki, client, llm, now=NOW, settings=InitSettings(per_source=3, max_age_days=365)
    )

    state = load_state(wiki)
    assert [i["url"] for i in state["items"]] == [
        f"https://blog.example.com/p/{n}" for n in (1, 2, 3)
    ]
    assert result.finished
    assert (result.saved, result.inboxed, result.dropped) == (1, 1, 1)
    [saved] = (tmp_path / "wiki").rglob("*.md")
    [inboxed] = (tmp_path / "inbox").rglob("*.md")
    assert "origin" not in front(saved)
    assert front(inboxed)["origin"] == "init"  # 초기 수집 보류 글은 Inbox에서 따로 묶는다
    assert wiki.seen_urls() == frozenset(i["url"] for i in state["items"])


def test_progress_reports_counts_per_source(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    run_init(
        wiki,
        make_routes(make_client, [1, 2], gone={2}),
        ScriptedLlm([0.9]),
        now=NOW,
        settings=InitSettings(per_source=5),
    )

    report = progress(load_state(wiki))

    assert report.total == 2
    assert report.counts == {"save": 1, "failed": 1}
    assert report.remaining == 0
    assert report.by_source["ex"]["listed"] == 2


def test_limit_stops_init_and_next_run_resumes(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    client = make_routes(make_client, [1, 2])
    blocked = ScriptedLlm([0.9, 0.9], [LlmError(ErrorKind.LIMIT, "한도")])

    first = run_init(wiki, client, blocked, now=NOW, settings=InitSettings(per_source=5))

    assert not first.finished
    assert first.stopped_reason == "limit: 한도"
    assert (
        progress(load_state(wiki)).remaining == 2
    )  # 요약하지 못한 글은 다시 처리할 대기열에 남는다

    second = run_init(
        wiki, client, ScriptedLlm([0.9, 0.9]), now=NOW, settings=InitSettings(per_source=5)
    )

    assert second.finished
    assert len(list((tmp_path / "wiki").rglob("*.md"))) == 2


def test_articles_already_seen_by_daily_sync_are_skipped(make_client, tmp_path: Path):
    from context_holder.classify import Bucket, Decision

    wiki = setup_wiki(tmp_path)
    wiki.record_seen(
        [Decision("https://blog.example.com/p/1", 0.9, Bucket.SAVE, "", (), ())], when=NOW
    )
    llm = ScriptedLlm([0.9])

    result = run_init(
        wiki, make_routes(make_client, [1, 2]), llm, now=NOW, settings=InitSettings(per_source=5)
    )

    assert [i["url"] for i in load_state(wiki)["items"]] == ["https://blog.example.com/p/2"]
    assert result.saved == 1


def test_undated_posts_that_turn_out_old_are_skipped_without_claude(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    (tmp_path / "config/sources.yaml").write_text(
        """
sources:
  - id: ex
    name: Ex
    region: domestic
    feed: https://blog.example.com/feed
    archive:
      kind: html
      url: "https://blog.example.com/?page={page}"
      link: "/p/[0-9]+"
      link_base: "https://blog.example.com"
""",
        encoding="utf-8",
    )
    old = (
        '<html><head><meta property="article:published_time" content="2024-01-01T00:00:00Z"></head>'
        f"<body><article><p>{LONG_BODY}</p></article></body></html>"
    )
    table = {
        "https://blog.example.com/?page=1": httpx.Response(200, text='<a href="/p/1">x</a>'),
        "https://blog.example.com/?page=2": httpx.Response(200, text='<a href="/p/1">x</a>'),
        "https://blog.example.com/p/1": httpx.Response(200, text=old),
    }
    client = make_client(lambda r: table.get(str(r.url), httpx.Response(404)))
    llm = ScriptedLlm([])

    result = run_init(wiki, client, llm, now=NOW, settings=InitSettings(per_source=5))

    assert progress(load_state(wiki)).counts == {"old": 1}
    assert result.finished and llm.enrich_calls == 0


def test_listing_failure_is_recorded_and_other_work_continues(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    client = make_client(lambda r: httpx.Response(500))

    result = run_init(wiki, client, ScriptedLlm([]), now=NOW, settings=InitSettings(per_source=5))

    assert not result.finished  # 다음 실행에서 목록을 다시 찾는다
    assert "500" in progress(load_state(wiki)).by_source["ex"]["error"]


def test_finished_init_does_nothing_on_rerun(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    client = make_routes(make_client, [1])
    run_init(wiki, client, ScriptedLlm([0.9]), now=NOW, settings=InitSettings(per_source=5))
    llm = ScriptedLlm([0.9])

    again = run_init(wiki, client, llm, now=NOW, settings=InitSettings(per_source=5))

    assert again.finished and again.saved == 0 and llm.enrich_calls == 0


def test_listing_stops_at_posts_older_than_max_age(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    updates = []

    run_init(
        wiki,
        make_routes(make_client, [1, 400, 2]),
        ScriptedLlm([0.1]),
        now=NOW,
        settings=InitSettings(per_source=10, max_age_days=365),
        on_progress=updates.append,
    )

    assert [i["url"] for i in load_state(wiki)["items"]] == ["https://blog.example.com/p/1"]
    assert updates and updates[-1].remaining == 0


def test_queue_items_handled_elsewhere_or_orphaned_are_closed(make_client, tmp_path: Path):
    from context_holder.classify import Bucket, Decision
    from context_holder.init import save_state

    wiki = setup_wiki(tmp_path)
    save_state(
        wiki,
        {
            "per_source": 5,
            "max_age_days": 365,
            "started": NOW.isoformat(),
            "listed": {"ex": {"count": 1, "error": None}, "gone": {"count": 1, "error": None}},
            "items": [
                {
                    "url": "https://blog.example.com/p/1",
                    "source": "ex",
                    "title": "t",
                    "published": None,
                    "status": "queued",
                },
                {
                    "url": "https://other.example.com/x",
                    "source": "gone",
                    "title": "t",
                    "published": None,
                    "status": "queued",
                },
            ],
        },
    )
    wiki.record_seen(
        [Decision("https://blog.example.com/p/1", 0.9, Bucket.SAVE, "", (), ())], when=NOW
    )

    result = run_init(wiki, make_routes(make_client, []), ScriptedLlm([]), now=NOW)

    assert result.finished
    assert progress(load_state(wiki)).counts == {"seen": 1, "failed": 1}


def test_preview_lists_without_writing_anything(make_client, tmp_path: Path):
    from context_holder.init import preview

    wiki = setup_wiki(tmp_path)

    found = preview(
        make_routes(make_client, [1, 2, 400]), wiki, now=NOW, settings=InitSettings(per_source=5)
    )

    assert found == {"ex": (2, 2, None)}
    assert load_state(wiki) == {}


def test_failed_listing_is_retried_on_next_run(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    run_init(wiki, make_client(lambda r: httpx.Response(504)), ScriptedLlm([]), now=NOW)

    result = run_init(wiki, make_routes(make_client, [1]), ScriptedLlm([0.9]), now=NOW)

    assert result.saved == 1
    assert progress(load_state(wiki)).by_source["ex"] == {
        "listed": 1,
        "error": None,
        "remaining": 0,
        "save": 1,
    }


def test_listing_that_keeps_failing_stops_blocking_completion(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    down = make_client(lambda r: httpx.Response(500))

    results = [run_init(wiki, down, ScriptedLlm([]), now=NOW).finished for _ in range(3)]

    assert results == [False, False, True]


def test_feed_only_sources_use_feed_body_when_pages_are_blocked(make_client, tmp_path: Path):
    from tests.conftest import rss

    (tmp_path / "config").mkdir()
    (tmp_path / "config/sources.yaml").write_text(
        "sources:\n  - {id: med, name: M, region: domestic, feed: 'https://medium.example.com/feed'}\n",
        encoding="utf-8",
    )
    wiki = Wiki(tmp_path)
    body = rss(
        [
            {
                "title": "막힌 글",
                "link": "https://medium.example.com/p-1?source=rss",
                "date": "Mon, 02 Nov 2026 00:00:00 GMT",
                "content": article_page("막힌 글", LONG_BODY),
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://medium.example.com/feed":
            return httpx.Response(200, text=body)
        return httpx.Response(403)  # 글 페이지는 막혀 있다

    result = run_init(wiki, make_client(handler), ScriptedLlm([0.9]), now=NOW)

    assert result.saved == 1


def test_same_body_under_two_urls_is_saved_once(make_client, tmp_path: Path):
    wiki = setup_wiki(tmp_path)
    posts = [
        {"url": "https://blog.example.com/p/slug", "title": "글", "at": NOW.isoformat()},
        {"url": "https://blog.example.com/p/123", "title": "글", "at": NOW.isoformat()},
    ]
    same = article_page("글", LONG_BODY)
    table = {
        "https://blog.example.com/api?page=1": httpx.Response(200, json={"posts": posts}),
        "https://blog.example.com/api?page=2": httpx.Response(200, json={"posts": []}),
        "https://blog.example.com/p/slug": httpx.Response(200, text=same),
        "https://blog.example.com/p/123": httpx.Response(200, text=same),
    }
    client = make_client(lambda r: table.get(str(r.url), httpx.Response(404)))

    result = run_init(
        wiki, client, ScriptedLlm([0.9]), now=NOW, settings=InitSettings(per_source=5)
    )

    assert result.saved == 1
    assert progress(load_state(wiki)).counts == {"save": 1, "duplicate": 1}
