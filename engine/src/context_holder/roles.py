"""직군 프리셋(`defaults/roles.yaml`)으로 분류 기준을 만든다 (plan 5.1).

고른 직군은 수집 기준, 고르지 않은 직군은 제외 기준이 된다.
프리셋은 앱과 함께 바뀌고, 위키의 profile.yaml에는 고른 직군 id만 적는다.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

import yaml


@dataclass(frozen=True)
class Topic:
    topic: str
    about: str


@dataclass(frozen=True)
class Role:
    id: str
    name: str
    hint: str  # 직군 고르는 화면의 한 줄 설명
    about: str

    @property
    def topic(self) -> Topic:
        return Topic(self.name, self.about)


@dataclass(frozen=True)
class Criteria:
    include: tuple[Topic, ...]
    borderline: tuple[Topic, ...]
    exclude: tuple[Topic, ...]
    others: tuple[Topic, ...]  # 고르지 않은 직군. 그 직군에만 해당하는 글을 거른다
    notes: tuple[str, ...]  # 판정 원칙에 더할 문장


@dataclass(frozen=True)
class Catalog:
    roles: tuple[Role, ...]
    include: tuple[Topic, ...]  # 직군과 상관없이 늘 적용
    borderline: tuple[Topic, ...]
    exclude: tuple[Topic, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.roles)

    def known(self, ids: Sequence[str]) -> tuple[str, ...]:
        """프리셋에 있는 id만 프리셋 순서로. 모르는 id(다른 버전 앱이 쓴 것)는 버린다."""
        chosen = set(ids)
        return tuple(r.id for r in self.roles if r.id in chosen)

    def criteria(self, selected: Sequence[str]) -> Criteria:
        chosen = set(selected)
        picked = [r for r in self.roles if r.id in chosen]
        others = [r for r in self.roles if r.id not in chosen]
        notes = []
        if "ml" not in chosen:
            notes.append(
                "AI · ML 글이라고 통째로 거르지 않는다. 만들어진 모델을 서비스와 개발에 쓰는 글은 "
                "수집 기준에 맞으면 수집하고, 모델을 만들고 학습시키는 글은 제외한다."
            )
        return Criteria(
            include=self.include + tuple(r.topic for r in picked),
            borderline=self.borderline,
            exclude=self.exclude,
            others=tuple(r.topic for r in others),
            notes=tuple(notes),
        )


ROLES_LINE = re.compile(r"^roles:[^\n]*\n(?:[ \t]*-[^\n]*\n)*", re.MULTILINE)
THRESHOLD = re.compile(r"^(\s+(save|inbox):\s*)([0-9.]+)", re.MULTILINE)


def with_roles(profile_text: str, roles: Sequence[str]) -> str:
    """profile.yaml 내용에 고른 직군을 적는다. 주석과 다른 설정은 그대로 둔다.

    roles가 없는 예전 profile(주제를 직접 적은 것)은 기본 profile 형식으로 바꾸고 경계값만 남긴다.
    """
    chosen = catalog().known(roles)
    if not chosen:
        raise ValueError("직군을 하나 이상 골라야 한다")
    line = f"roles: [{', '.join(chosen)}]\n"
    data = yaml.safe_load(profile_text) or {}
    if isinstance(data, dict) and "roles" in data:
        text = profile_text if profile_text.endswith("\n") else profile_text + "\n"
        return ROLES_LINE.sub(line, text, count=1)
    template = resources.files("context_holder.defaults").joinpath("profile.yaml")
    text = ROLES_LINE.sub(line, template.read_text("utf-8"), count=1)
    thresholds = (data.get("thresholds") if isinstance(data, dict) else None) or {}
    return THRESHOLD.sub(lambda m: m.group(1) + str(thresholds.get(m.group(2), m.group(3))), text)


@cache
def catalog() -> Catalog:
    text = resources.files("context_holder.defaults").joinpath("roles.yaml").read_text("utf-8")
    return parse_catalog(yaml.safe_load(text))


def parse_catalog(data: dict[str, Any]) -> Catalog:
    common = data.get("common") or {}
    return Catalog(
        roles=tuple(
            Role(str(r["id"]), str(r["name"]), str(r["hint"]), str(r["about"]))
            for r in data["roles"]
        ),
        include=_topics(common.get("include")),
        borderline=_topics(common.get("borderline")),
        exclude=_topics(common.get("exclude")),
    )


def _topics(raw: list[dict[str, str]] | None) -> tuple[Topic, ...]:
    return tuple(Topic(str(t["topic"]), str(t["about"])) for t in raw or ())
