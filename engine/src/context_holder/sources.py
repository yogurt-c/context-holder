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

    def url_for(self, article_url: str) -> str | None:
        match = re.search(self.id_pattern, article_url)
        return self.url.replace("{id}", match.group(1)) if match else None


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    region: Region
    feed_url: str
    mode: Mode = Mode.FEED
    exclude_title: tuple[str, ...] = ()
    body_api: BodyApi | None = None

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
    return BodyApi(url=raw["url"], id_pattern=raw["id"], field=raw["field"])
