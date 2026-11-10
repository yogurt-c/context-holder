from pathlib import Path

import pytest
import yaml

from context_holder.classify import (
    LEGACY_AUDIENCE,
    ProfileError,
    _system_prompt,
    classify,
    load_default_profile,
    load_profile,
    schema,
)
from context_holder.roles import catalog, with_roles
from tests.test_classify import FakeLlm, article, item

LEGACY = """\
thresholds:
  save: 0.65
  inbox: 0.3
include:
  - topic: 백엔드 · 인프라 · 데이터
    about: 동시성, DB
exclude:
  - topic: ML 모델링
    about: 모델 아키텍처
"""


def profile_at(tmp_path: Path, text: str):
    path = tmp_path / "profile.yaml"
    path.write_text(text, encoding="utf-8")
    return load_profile(path)


def names(topics) -> list[str]:
    return [t.topic for t in topics]


def test_catalog_has_seven_roles_with_names_and_hints():
    roles = catalog().roles

    assert [r.id for r in roles] == [
        "backend",
        "frontend",
        "ai",
        "ml",
        "infra",
        "data",
        "security",
    ]
    assert all(r.name and r.hint and r.about for r in roles)


def test_default_profile_chooses_everything_but_ml_and_security():
    profile = load_default_profile()

    assert profile.roles == ("backend", "frontend", "ai", "infra", "data")
    assert names(profile.include) == [
        "개발 생산성",
        "백엔드",
        "프론트엔드 · 모바일",
        "AI 엔지니어링",
        "인프라 · DevOps",
        "데이터 엔지니어링",
    ]
    assert names(profile.others) == ["ML", "보안"]
    assert names(profile.exclude) == ["엔지니어링 외"]
    assert names(profile.borderline) == ["기술 동향 정리"]


def test_prompt_names_chosen_roles_and_marks_the_others():
    system = _system_prompt(load_default_profile())

    assert system.startswith(
        "너는 백엔드, 프론트엔드 · 모바일, AI 엔지니어링, 인프라 · DevOps, 데이터 엔지니어링 "
        "직군의 개발자가"
    )
    assert "선택하지 않은 직군" in system and "- 보안: 위협 모델링" in system
    assert "모델을 만들고 학습시키는 글은 제외한다" in system


def test_choosing_ml_collects_model_building_posts(tmp_path: Path):
    profile = profile_at(tmp_path, "roles: [ml, security]\n")

    assert names(profile.include)[1:] == ["ML", "보안"]
    assert "학습시키는 글은 제외한다" not in _system_prompt(profile)


def test_unknown_roles_are_ignored_but_one_known_role_is_required(tmp_path: Path):
    assert profile_at(tmp_path, "roles: [game, data]\n").roles == ("data",)
    with pytest.raises(ProfileError, match="아는 직군"):
        profile_at(tmp_path, "roles: [game]\n")
    with pytest.raises(ProfileError, match="아는 직군"):
        profile_at(tmp_path, "roles: []\n")
    with pytest.raises(ProfileError, match="직군 id 목록"):
        profile_at(tmp_path, "roles: backend\n")


def test_topics_written_in_profile_are_added_on_top_of_roles(tmp_path: Path):
    profile = profile_at(
        tmp_path,
        "roles: [backend]\n"
        "include: [{topic: 게임 서버, about: 실시간 동기화}]\n"
        "exclude: [{topic: 블록체인, about: 코인}]\n",
    )

    assert names(profile.include)[-1] == "게임 서버"
    assert names(profile.exclude) == ["엔지니어링 외", "블록체인"]


def test_legacy_profile_without_roles_keeps_its_own_topics(tmp_path: Path):
    profile = profile_at(tmp_path, LEGACY)
    system = _system_prompt(profile)

    assert profile.roles == () and profile.others == ()
    assert names(profile.include) == ["백엔드 · 인프라 · 데이터"]
    assert system.startswith(f"너는 {LEGACY_AUDIENCE}가")
    assert "AI를 엔지니어링에 쓰는 글은 수집하고" in system
    assert "선택하지 않은 직군" not in system


class SchemaLlm(FakeLlm):
    def structured(self, prompt, system, schema):
        self.schema = schema
        return super().structured(prompt, system, schema)


def test_classifier_must_pick_one_of_the_criteria_names():
    profile = load_default_profile()
    llm = SchemaLlm([{"items": [item("1", 0.8)]}])

    classify(llm, [article(1)], profile)

    assert llm.schema == schema(profile)
    topic = llm.schema["properties"]["items"]["items"]["properties"]["topic"]
    assert topic["enum"] == list(profile.topic_names)
    assert "보안" in topic["enum"] and "엔지니어링 외" in topic["enum"]


def test_with_roles_replaces_only_the_roles_line():
    text = "# 메모\nthresholds:\n  save: 0.7\nroles:\n  - backend\n  - ai\ninclude: []\n"

    out = with_roles(text, ["security", "backend"])

    assert out == "# 메모\nthresholds:\n  save: 0.7\nroles: [backend, security]\ninclude: []\n"


def test_with_roles_turns_a_legacy_profile_into_the_roles_format_keeping_thresholds():
    out = with_roles(LEGACY.rstrip("\n"), ["ml"])
    data = yaml.safe_load(out)

    assert data["roles"] == ["ml"]
    assert data["thresholds"] == {"save": 0.65, "inbox": 0.3}
    assert "include" not in data and "exclude" not in data


def test_with_roles_needs_a_known_role():
    with pytest.raises(ValueError, match="하나 이상"):
        with_roles("roles: [backend]\n", ["game"])
