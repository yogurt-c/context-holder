"""수집 대상 소스 설정(`sources.yaml`)을 읽고 검증한다."""

import re
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml

Region = Literal["domestic", "overseas"]
REGIONS: tuple[Region, ...] = ("domestic", "overseas")


class Mode(StrEnum):
    FEED = "feed"  # feed 본문을 쓰고, 짧으면 원문 페이지를 가져온다
    FETCH = "fetch"  # 항상 원문 페이지를 가져온다 (feed에 요약만 있는 곳)


class SourceConfigError(ValueError):
    """sources.yaml이 잘못됐다."""


@dataclass(frozen=True)
class BodyApi:
    """원문 페이지가 SPA라 본문이 없을 때, 본문 HTML을 주는 JSON API."""

    url: str  # "{id}" 자리에 글 id가 들어간다
    id_pattern: str  # 글 URL에서 id를 뽑는 정규식, 첫 그룹이 id
    field: str  # 응답 JSON에서 본문 HTML이 든 필드
    title_field: str = ""  # 응답 JSON의 제목 필드 (목록에 제목이 없을 때)
    date_field: str = ""  # 응답 JSON의 게시일 필드

    def url_for(self, article_url: str) -> str | None:
        match = re.search(self.id_pattern, article_url)
        return self.url.replace("{id}", match.group(1)) if match else None


ARCHIVE_KINDS = ("wp_paged", "json", "html", "sitemap")


@dataclass(frozen=True)
class Archive:
    """첫 실행(init) 때 feed보다 오래된 글 목록을 얻는 방법. 없으면 feed만 쓴다."""

    kind: str  # wp_paged | json | html | sitemap
    url: str = ""  # 목록 주소. json · html은 "{page}" 자리에 페이지 번호
    first_page: int = 1
    items: str = ""  # json: 글 목록이 든 필드 (점으로 중첩)
    link: str = ""  # json: 주소 필드나 "{필드}" 템플릿, html · sitemap: 주소 정규식
    link_base: str = ""  # 상대 주소 앞에 붙일 사이트 주소
    title: str = ""  # json: 제목 필드
    date: str = ""  # json: 게시 시각 필드


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    region: Region
    feed_url: str
    mode: Mode = Mode.FEED
    exclude_title: tuple[str, ...] = ()
    body_api: BodyApi | None = None
    archive: Archive | None = None
    date_pattern: str = ""  # 원문 화면에서 게시일을 찾는 정규식 (연 · 월 · 일 그룹)

    def excludes(self, title: str) -> bool:
        return any(re.search(pattern, title) for pattern in self.exclude_title)


def load_sources(path: Path) -> tuple[Source, ...]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise SourceConfigError(f"{path}: 읽을 수 없다 ({e})") from e
    return parse_sources(data, origin=str(path))


def load_default_sources() -> tuple[Source, ...]:
    text = resources.files("context_holder.defaults").joinpath("sources.yaml").read_text("utf-8")
    return parse_sources(yaml.safe_load(text), origin="기본 sources.yaml")


def parse_sources(data: Any, origin: str) -> tuple[Source, ...]:
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise SourceConfigError(f"{origin}: 최상위에 sources 목록이 필요하다")
    parsed = tuple(_parse_one(item, origin, i) for i, item in enumerate(data["sources"]))
    ids = [s.id for s in parsed]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise SourceConfigError(f"{origin}: 중복된 id {', '.join(duplicates)}")
    return parsed


def _parse_one(item: Any, origin: str, index: int) -> Source:
    where = f"{origin}: sources[{index}]"
    if not isinstance(item, dict):
        raise SourceConfigError(f"{where}: 항목은 매핑이어야 한다")
    for key in ("id", "name", "region", "feed"):
        if not isinstance(item.get(key), str) or not item[key].strip():
            raise SourceConfigError(f"{where}: {key} 값이 필요하다")
    if item["region"] not in REGIONS:
        raise SourceConfigError(f"{where}: region은 {', '.join(REGIONS)} 중 하나")
    if not item["feed"].startswith(("https://", "http://")):
        raise SourceConfigError(f"{where}: feed는 http(s) 주소여야 한다")
    try:
        mode = Mode(item.get("mode", Mode.FEED))
    except ValueError as e:
        raise SourceConfigError(f"{where}: mode는 feed 또는 fetch") from e
    excludes = item.get("exclude_title", [])
    if not isinstance(excludes, list) or not all(isinstance(p, str) for p in excludes):
        raise SourceConfigError(f"{where}: exclude_title은 문자열 목록")
    for pattern in excludes:
        try:
            re.compile(pattern)
        except re.error as e:
            raise SourceConfigError(f"{where}: exclude_title 정규식 오류 {pattern!r}") from e
    return Source(
        id=item["id"],
        name=item["name"],
        region=item["region"],
        feed_url=item["feed"],
        mode=mode,
        exclude_title=tuple(excludes),
        body_api=_parse_body_api(item.get("body_api"), where),
        archive=_parse_archive(item.get("archive"), where),
        date_pattern=_regex(item.get("date_pattern", ""), where, "date_pattern"),
    )


def _regex(pattern: Any, where: str, key: str) -> str:
    if not isinstance(pattern, str):
        raise SourceConfigError(f"{where}: {key}는 문자열이어야 한다")
    try:
        re.compile(pattern)
    except re.error as e:
        raise SourceConfigError(f"{where}: {key} 정규식 오류") from e
    return pattern


def _parse_archive(raw: Any, where: str) -> Archive | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("kind") not in ARCHIVE_KINDS:
        raise SourceConfigError(f"{where}: archive.kind는 {', '.join(ARCHIVE_KINDS)} 중 하나")
    kind = raw["kind"]
    needs = {
        "wp_paged": (),
        "json": ("url", "items", "link"),
        "html": ("url", "link"),
        "sitemap": ("url", "link"),
    }
    for key in needs[kind]:
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise SourceConfigError(f"{where}: archive.{key} 값이 필요하다")
    if kind in ("json", "html") and "{page}" not in raw["url"]:
        raise SourceConfigError(f"{where}: archive.url에 {{page}} 자리가 필요하다")
    if kind in ("html", "sitemap"):
        _regex(raw["link"], where, "archive.link")
    fields = ("url", "items", "link", "link_base", "title", "date")
    return Archive(
        kind=kind,
        first_page=int(raw.get("first_page", 1)),
        **{f: str(raw.get(f, "")) for f in fields},
    )


def _parse_body_api(raw: Any, where: str) -> BodyApi | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SourceConfigError(f"{where}: body_api는 매핑이어야 한다")
    for key in ("url", "id", "field"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise SourceConfigError(f"{where}: body_api.{key} 값이 필요하다")
    if "{id}" not in raw["url"]:
        raise SourceConfigError(f"{where}: body_api.url에 {{id}} 자리가 필요하다")
    try:
        groups = re.compile(raw["id"]).groups
    except re.error as e:
        raise SourceConfigError(f"{where}: body_api.id 정규식 오류") from e
    if groups < 1:
        raise SourceConfigError(f"{where}: body_api.id에 id를 담을 그룹이 필요하다")
    return BodyApi(
        url=raw["url"],
        id_pattern=raw["id"],
        field=raw["field"],
        title_field=str(raw.get("title", "")),
        date_field=str(raw.get("date", "")),
    )
