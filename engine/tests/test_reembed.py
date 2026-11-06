from datetime import UTC, datetime
from pathlib import Path

import httpx

from context_holder.reembed import run_reembed
from context_holder.vectorize import write_paragraph_vectors
from context_holder.wiki import Wiki, article_id
from tests.conftest import LONG_BODY, FakeEmbedder, article_page
from tests.test_index import write

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def setup(tmp_path: Path) -> Wiki:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/sources.yaml").write_text(
        "sources:\n  - {id: toss, name: Toss, region: domestic, feed: 'https://t/feed', "
        "archive: {kind: wp_paged}}\n",
        encoding="utf-8",
    )
    wiki = Wiki(tmp_path)
    wiki.ensure_config()
    return wiki


def test_write_paragraph_vectors(tmp_path: Path):
    from context_holder.collect import Article

    wiki = Wiki(tmp_path)
    article = Article("toss", "https://t/a", "t", NOW, "첫 단락\n\n" + LONG_BODY, "h")

    count = write_paragraph_vectors(wiki, FakeEmbedder(), article)

    assert count >= 1
    assert wiki.read_vectors("fake", article_id("https://t/a")).shape[0] == count


def test_reembed_fetches_originals_writes_vectors_and_switches_model(make_client, tmp_path: Path):
    wiki = setup(tmp_path)
    ok = write(wiki, "https://t/ok", "글", ("핵심",))
    gone = write(wiki, "https://t/gone", "사라진 글", ("핵심",))
    wiki.write_vectors("old", ok, FakeEmbedder("old").passages(["x"]), "h")
    client = make_client(
        lambda r: (
            httpx.Response(200, text=article_page("글", LONG_BODY))
            if str(r.url) == "https://t/ok"
            else httpx.Response(404)
        )
    )
    updates = []

    result = run_reembed(wiki, client, [FakeEmbedder("new")], on_progress=updates.append)

    assert result.written == 1
    assert [f.article_id for f in result.failed] == [gone]
    assert wiki.read_vectors("new", ok) is not None
    assert wiki.search_model() == "new"
    assert not (tmp_path / "vectors/old").exists()  # 이전 모델 벡터는 지운다
    assert updates[-1] == (2, 2)


def test_reembed_is_resumable_and_can_keep_current_model(make_client, tmp_path: Path):
    wiki = setup(tmp_path)
    aid = write(wiki, "https://t/ok", "글", ("핵심",))
    embedder = FakeEmbedder("a")
    wiki.write_vectors("a", aid, embedder.passages(["x"]), "h")
    calls = []
    client = make_client(
        lambda r: calls.append(r) or httpx.Response(200, text=article_page("글", LONG_BODY))
    )

    result = run_reembed(wiki, client, [embedder, FakeEmbedder("b")], switch=False)

    assert result.written == 1 and len(calls) == 1
    assert wiki.search_model() == "bge-m3"  # 여러 모델 비교용 실행은 설정을 바꾸지 않는다
    again = run_reembed(wiki, client, [embedder, FakeEmbedder("b")], switch=False)
    assert again.written == 0 and len(calls) == 1


def test_feed_only_source_uses_feed_body(make_client, tmp_path: Path):
    from tests.conftest import rss

    (tmp_path / "config").mkdir()
    (tmp_path / "config/sources.yaml").write_text(
        "sources:\n  - {id: toss, name: M, region: domestic, feed: 'https://m/feed'}\n",
        encoding="utf-8",
    )
    wiki = Wiki(tmp_path)
    write(wiki, "https://m/p", "글", ("핵심",))
    feed = rss([{"title": "글", "link": "https://m/p", "content": article_page("글", LONG_BODY)}])
    client = make_client(
        lambda r: (
            httpx.Response(200, text=feed)
            if str(r.url) == "https://m/feed"
            else httpx.Response(403)
        )
    )

    result = run_reembed(wiki, client, [FakeEmbedder("f")])

    assert result.written == 1 and result.failed == ()


def test_article_of_removed_source_fails(make_client, tmp_path: Path):
    wiki = setup(tmp_path)
    (tmp_path / "config/sources.yaml").write_text(
        "sources:\n  - {id: other, name: O, region: domestic, feed: 'https://o/feed'}\n",
        encoding="utf-8",
    )
    write(wiki, "https://t/x", "글", ("핵심",))

    result = run_reembed(wiki, make_client(lambda r: httpx.Response(404)), [FakeEmbedder("z")])

    assert result.failed[0].reason == "소스 설정에서 빠졌다"
