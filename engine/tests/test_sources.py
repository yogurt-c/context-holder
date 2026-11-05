from pathlib import Path

import pytest

from context_holder.sources import Mode, SourceConfigError, load_default_sources, load_sources


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_sources_with_defaults(tmp_path: Path):
    path = write(
        tmp_path,
        """
sources:
  - id: toss
    name: Toss
    region: domestic
    feed: https://toss.tech/rss.xml
  - id: kakao
    name: Kakao
    region: domestic
    feed: https://tech.kakao.com/feed/
    mode: fetch
    exclude_title: ["(?i)채용"]
""",
    )

    sources = load_sources(path)

    assert [s.id for s in sources] == ["toss", "kakao"]
    assert sources[0].mode is Mode.FEED
    assert sources[1].mode is Mode.FETCH
    assert sources[1].excludes("2026 개발자 채용 안내")
    assert not sources[1].excludes("캐시 무효화")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("sources:\n  - id: a\n    name: A\n    region: domestic\n", "feed"),
        (
            "sources:\n  - {id: a, name: A, region: mars, feed: 'https://a.example'}\n",
            "region",
        ),
        (
            "sources:\n  - {id: a, name: A, region: domestic, feed: 'https://a', mode: x}\n",
            "mode",
        ),
        (
            "sources:\n  - {id: a, name: A, region: domestic, feed: 'ftp://a'}\n",
            "feed",
        ),
        (
            "sources:\n"
            "  - {id: a, name: A, region: domestic, feed: 'https://a'}\n"
            "  - {id: a, name: B, region: domestic, feed: 'https://b'}\n",
            "중복",
        ),
        ("nothing: here\n", "sources"),
    ],
)
def test_rejects_invalid_config(tmp_path: Path, body: str, message: str):
    with pytest.raises(SourceConfigError, match=message):
        load_sources(write(tmp_path, body))


def test_default_sources_are_the_thirteen_from_readme():
    sources = load_default_sources()

    assert len(sources) == 13
    assert sum(s.region == "domestic" for s in sources) == 9
    assert {s.id for s in sources if s.mode is Mode.FETCH} == {"line", "oliveyoung", "kakao"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("sources:\n  - just-a-string\n", "매핑"),
        (
            "sources:\n  - {id: a, name: A, region: domestic, feed: 'https://a', "
            "exclude_title: x}\n",
            "목록",
        ),
        (
            "sources:\n  - {id: a, name: A, region: domestic, feed: 'https://a', "
            "exclude_title: ['(']}\n",
            "정규식",
        ),
        ("sources: [\n", "읽을 수 없다"),
    ],
)
def test_rejects_malformed_items(tmp_path: Path, body: str, message: str):
    with pytest.raises(SourceConfigError, match=message):
        load_sources(write(tmp_path, body))


def test_missing_file_is_a_config_error(tmp_path: Path):
    with pytest.raises(SourceConfigError, match="읽을 수 없다"):
        load_sources(tmp_path / "nope.yaml")


def test_body_api_builds_url_from_post_id(tmp_path: Path):
    path = write(
        tmp_path,
        """
sources:
  - id: kakao
    name: Kakao
    region: domestic
    feed: https://tech.kakao.com/feed/
    mode: fetch
    body_api:
      url: https://tech.kakao.com/api/v1/posts/{id}
      id: /posts/(\\d+)
      field: content
""",
    )

    [source] = load_sources(path)

    assert source.body_api is not None
    assert source.body_api.field == "content"
    assert source.body_api.url_for("https://tech.kakao.com/posts/845") == (
        "https://tech.kakao.com/api/v1/posts/845"
    )
    assert source.body_api.url_for("https://tech.kakao.com/about") is None


@pytest.mark.parametrize(
    ("api", "message"),
    [
        ("{url: 'https://a/{id}', id: '(\\d+)'}", "field"),
        ("{url: 'https://a/x', id: '(\\d+)', field: content}", "\\{id\\}"),
        ("{url: 'https://a/{id}', id: '\\d+', field: content}", "그룹"),
        ("just-text", "매핑"),
        ("{url: 'https://a/{id}', id: '(', field: content}", "정규식"),
    ],
)
def test_rejects_invalid_body_api(tmp_path: Path, api: str, message: str):
    body = (
        f"sources:\n  - {{id: a, name: A, region: domestic, feed: 'https://a', body_api: {api}}}\n"
    )
    with pytest.raises(SourceConfigError, match=message):
        load_sources(write(tmp_path, body))


def test_default_sources_use_body_api_for_spa_blogs():
    by_id = {s.id: s for s in load_default_sources()}

    assert by_id["kakao"].body_api.url_for("https://tech.kakao.com/posts/845") == (
        "https://tech.kakao.com/api/v1/posts/845"
    )
    assert by_id["naver-d2"].body_api.url_for("https://d2.naver.com/news/0907869") == (
        "https://d2.naver.com/api/v1/contents/0907869"
    )
