"""화면에서 sources.yaml 고치기: 소스 추가 · 수집 제외. 주석과 다른 항목은 그대로 둔다."""

import pytest

from context_holder import sourcesedit
from context_holder.sources import SourceConfigError, active, load_default_sources
from context_holder.wiki import Wiki

BLOCK = """# 수집 설정. 이 주석은 남아야 한다
sources:
  - id: toss
    name: Toss
    region: domestic
    feed: https://toss.tech/rss.xml

  # ---------- 직군 소스 ----------
  - id: meta
    name: Meta
    region: overseas
    feed: https://engineering.fb.com/feed/
    roles: [ml]
"""


@pytest.fixture
def wiki(tmp_path) -> Wiki:
    wiki = Wiki(tmp_path / "wiki")
    wiki.ensure_config()
    (wiki.root / "config/sources.yaml").write_text(BLOCK, encoding="utf-8")
    return wiki


def text(wiki: Wiki) -> str:
    return (wiki.root / "config/sources.yaml").read_text(encoding="utf-8")


def test_add_source_appends_and_keeps_comments(wiki):
    added = wiki.add_source('달팽이 "블로그"', "domestic", "https://blog.snail.kr/feed")

    assert (added.id, added.name, added.region) == (
        "blog.snail.kr",
        '달팽이 "블로그"',
        "domestic",
    )
    assert text(wiki).startswith(BLOCK)
    assert [s.id for s in wiki.sources()] == ["toss", "meta", "blog.snail.kr"]


def test_add_source_rejects_same_feed(wiki):
    with pytest.raises(SourceConfigError, match="Toss"):
        wiki.add_source("토스", "domestic", "http://www.toss.tech/rss.xml/")
    assert text(wiki) == BLOCK


def test_add_source_inserts_before_other_top_level_keys(wiki):
    wiki.exclude_source("meta")
    wiki.add_source("New", "overseas", "https://new.example/feed")

    assert text(wiki).rstrip().endswith('excluded: ["meta"]')
    sources = {s.id: s for s in wiki.sources()}
    assert sources["new.example"].feed_url == "https://new.example/feed"
    assert sources["meta"].excluded and not sources["new.example"].excluded


def test_add_source_into_flow_style_list(tmp_path):
    wiki = Wiki(tmp_path / "wiki")
    wiki.ensure_config()
    path = wiki.root / "config/sources.yaml"
    path.write_text(
        "sources:\n  - {id: toss, name: Toss, region: domestic, feed: 'https://toss.tech/rss'}",
        encoding="utf-8",
    )
    wiki.add_source("New", "overseas", "https://new.example/feed")
    assert [s.id for s in wiki.sources()] == ["toss", "new.example"]


def test_exclude_and_include_keep_source_and_comments(wiki):
    wiki.exclude_source("toss")

    toss = next(s for s in wiki.sources() if s.id == "toss")
    assert toss.excluded and toss.name == "Toss"  # 이미 모은 글의 회사 이름은 남는다
    assert [s.id for s in active(wiki.sources(), ["ml"])] == ["meta"]
    assert "toss" not in [s.id for s in wiki.active_sources()]

    wiki.include_source("toss")
    assert text(wiki) == BLOCK


def test_exclude_unknown_source_fails(wiki):
    with pytest.raises(SourceConfigError):
        wiki.exclude_source("nope")


def test_excluded_must_be_id_list(wiki):
    (wiki.root / "config/sources.yaml").write_text(BLOCK + "excluded: toss\n", encoding="utf-8")
    with pytest.raises(SourceConfigError, match="excluded"):
        wiki.sources()


def test_new_id_uses_platform_path_and_avoids_taken():
    assert sourcesedit.new_id("https://medium.com/feed/daangn", set()) == "medium-daangn"
    assert sourcesedit.new_id("https://v2.velog.io/rss/@Kim", set()) == "velog-kim"
    assert sourcesedit.new_id("https://rss.blog.naver.com/abc.xml", set()) == "naver-blog-abc"
    taken = {"blog.example.com", "blog.example.com-2"}
    assert sourcesedit.new_id("https://www.blog.example.com/rss", taken) == "blog.example.com-3"


def test_same_feed_ignores_scheme_www_and_slash():
    assert sourcesedit.same_feed("https://www.a.com/feed/", "http://a.com/feed")
    assert not sourcesedit.same_feed("https://a.com/feed", "https://a.com/rss")


def test_clean_name():
    assert sourcesedit.clean_name("  달팽이   블로그 ") == "달팽이 블로그"
    with pytest.raises(ValueError):
        sourcesedit.clean_name("   ")
    with pytest.raises(ValueError):
        sourcesedit.clean_name("가" * 61)


def test_default_sources_accept_additions():
    """기본 sources.yaml(새 위키의 초깃값)에도 넣을 수 있다."""
    from importlib import resources

    default = resources.files("context_holder.defaults").joinpath("sources.yaml").read_text("utf-8")
    updated = sourcesedit.with_source(default, "x.example", "X", "overseas", "https://x.example/f")
    ids = [s.id for s in sourcesedit.checked(updated, "test")]
    assert ids == [s.id for s in load_default_sources()] + ["x.example"]
