"""결정 주제 정리: 지도 파일, Claude로 정리(버튼), 검색 모델로 붙이기(매일)."""

import pytest
import yaml

from context_holder import topics
from context_holder.topics import Group, TopicMap
from context_holder.wiki import Wiki
from tests.conftest import FakeEmbedder
from tests.test_classify import FakeLlm
from tests.test_index import write


@pytest.fixture
def wiki(tmp_path) -> Wiki:
    wiki = Wiki(tmp_path / "wiki")
    wiki.ensure_config()
    (wiki.root / ".git").mkdir()
    return wiki


def cases(wiki: Wiki, *names: str) -> None:
    for n, name in enumerate(names):
        write(wiki, f"https://t/{n}", f"글 {n}", ("p",), topic=name)


def answer(*groups) -> dict:
    return {
        "groups": [
            {"name": name, "area": area, "question": f"{name}을 어떻게 할까", "cases": list(nums)}
            for name, area, nums in groups
        ]
    }


# ---------- 지도 ----------


def test_map_lookup_and_add():
    m = TopicMap((Group("캐시 전략", "백엔드", "", ("캐시 무효화",)),))
    assert m.group_of(" 캐시  무효화 ") == "캐시 전략" and m.group_of(None) is None
    assert m.group("캐시 전략 ").name == "캐시 전략" and m.group("없음") is None
    added = m.add(
        {"캐시 전략": ["캐시 무효화", "캐시 예열"], "모노레포": ["모노레포 도구"]},
        [Group("모노레포", "개발 생산성"), Group("빈 주제")],
    )
    assert [g.name for g in added.groups] == ["캐시 전략", "모노레포"]  # 빈 주제는 남기지 않는다
    assert added.group("캐시 전략").topics == ("캐시 무효화", "캐시 예열")  # 중복은 더하지 않는다
    assert m.add({"모노레포": ["캐시 무효화"]}, [Group("모노레포")]).group("모노레포") is None


def test_load_and_save_roundtrip(wiki):
    assert topics.load(wiki) == TopicMap()
    saved = TopicMap((Group("캐시 전략", "백엔드", "언제 지울까", ("캐시 무효화",)),))
    topics.save(wiki, saved)
    assert topics.load(wiki) == saved
    path = wiki.root / topics.TOPICS_FILE
    path.write_text("groups: [{area: 백엔드}, 3, {name: 빈, topics: [null, x]}]")
    assert topics.load(wiki).groups == (Group("빈", "", "", ("x",)),)
    path.write_text("- 목록")
    assert topics.load(wiki) == TopicMap()
    path.write_text("groups: [")
    assert topics.load(wiki) == TopicMap()


def test_cases_dedupe_by_topic(wiki):
    cases(wiki, "캐시 무효화", "캐시  무효화", "모노레포 도구")
    write(wiki, "https://t/x", "결정 없음", ("p",))
    assert [c.topic for c in topics.cases(wiki)] in (
        ["캐시 무효화", "모노레포 도구"],
        ["캐시  무효화", "모노레포 도구"],
        ["모노레포 도구", "캐시 무효화"],
        ["모노레포 도구", "캐시  무효화"],
    )


# ---------- 버튼: Claude로 정리 ----------


def test_first_regroup_sends_all_cases_and_saves(wiki):
    cases(wiki, "캐시 무효화", "캐시 예열", "모노레포 도구")
    order = [c.topic for c in topics.cases(wiki)]
    number = {t: i + 1 for i, t in enumerate(order)}
    llm = FakeLlm(
        [
            answer(
                ("캐시 전략", "백엔드", [number["캐시 무효화"], number["캐시 예열"], 99]),
                ("모노레포 운영", "개발 생산성", [number["모노레포 도구"], number["캐시 예열"]]),
                ("", "x", [1]),
            )
        ]
    )
    seen = []
    result = topics.regroup(wiki, llm, lambda done, total: seen.append((done, total)))
    prompt, system = llm.prompts[0]
    assert "20~40개의 넓은 주제" in prompt and "캐시 무효화 — 문제" in prompt
    assert "본문" not in prompt and "기술 결정 사례" in system
    assert (result.groups, result.assigned, result.left, result.cost_usd) == (2, 3, 0, 0.01)
    assert seen == [(1, 1)]
    saved = topics.load(wiki)
    assert saved.group("캐시 전략").area == "백엔드"
    assert saved.group("캐시 전략").question == "캐시 전략을 어떻게 할까"
    assert saved.group_of("모노레포 도구") == "모노레포 운영"
    data = yaml.safe_load((wiki.root / topics.TOPICS_FILE).read_text())
    assert data["groups"][0]["topics"] == ["캐시 무효화", "캐시 예열"]


def test_later_regroup_sends_only_unassigned_with_existing_names(wiki):
    cases(wiki, "캐시 무효화", "LLM 평가")
    topics.save(wiki, TopicMap((Group("캐시 전략", "백엔드", "", ("캐시 무효화",)),)))
    llm = FakeLlm([answer(("캐시 전략", "백엔드", []), ("AI 평가", "AI 엔지니어링", [1, 1]))])
    result = topics.regroup(wiki, llm)
    prompt = llm.prompts[0][0]
    assert "기존 주제:\n- 캐시 전략 (백엔드)" in prompt
    assert "1. LLM 평가" in prompt and "캐시 무효화 —" not in prompt
    assert (result.groups, result.assigned, result.left) == (2, 1, 0)


def test_regroup_keeps_cases_claude_skipped_for_next_time(wiki):
    cases(wiki, "캐시 무효화", "LLM 평가")
    result = topics.regroup(wiki, FakeLlm([answer(("캐시 전략", "백엔드", [1]))]))
    assert result.left == 1 and result.assigned == 1


def test_regroup_with_nothing_to_do(wiki):
    result = topics.regroup(wiki, FakeLlm([]))
    assert (result.groups, result.assigned, result.left, result.cost_usd) == (0, 0, 0, 0.0)


def test_regroup_batches_large_backlog(wiki, monkeypatch):
    monkeypatch.setattr(topics, "BATCH", 2)
    cases(wiki, "가", "나", "다")
    llm = FakeLlm([answer(("묶음", "백엔드", [1, 2])), answer(("묶음", "백엔드", [1]))])
    result = topics.regroup(wiki, llm)
    assert len(llm.prompts) == 2 and "기존 주제:\n- 묶음" in llm.prompts[1][0]
    assert result.assigned == 3 and len(topics.load(wiki).group("묶음").topics) == 3


# ---------- 매일: 검색 모델로 붙이기 ----------


def test_attach_joins_close_cases_and_leaves_far_ones(wiki):
    cases(wiki, "캐시 무효화 전략", "캐시 무효화 방식", "모노레포 도구 선택")
    topics.save(wiki, TopicMap((Group("캐시 전략", "백엔드", "", ("캐시 무효화 전략",)),)))
    assert topics.attach(wiki, FakeEmbedder()) == 1
    saved = topics.load(wiki)
    assert saved.group_of("캐시 무효화 방식") == "캐시 전략"
    assert saved.group_of("모노레포 도구 선택") is None
    assert [c.topic for c in topics.unassigned(wiki, saved)] == ["모노레포 도구 선택"]
    assert topics.attach(wiki, FakeEmbedder()) == 0  # 붙일 만큼 가까운 사례가 없다


def test_attach_does_nothing_before_first_regroup_or_without_model(wiki):
    cases(wiki, "캐시 무효화", "캐시 예열")
    assert topics.attach(wiki, None) == 0
    assert topics.attach(wiki, FakeEmbedder()) == 0
    assert not (wiki.root / topics.TOPICS_FILE).exists()
    topics.save(wiki, TopicMap((Group("캐시 전략", "백엔드", "", ("캐시 무효화", "캐시 예열")),)))
    assert topics.attach(wiki, FakeEmbedder()) == 0  # 붙일 사례가 없다


def test_sync_attaches_topics_and_survives_failure(wiki, monkeypatch, caplog):
    from context_holder import sync

    caplog.set_level("INFO", logger="context_holder.sync")
    monkeypatch.setattr(sync, "attach", lambda w, e: 2)
    sync._attach_topics(wiki, FakeEmbedder())
    assert "2건을 기존 주제에 붙였다" in caplog.text

    def broken(w, e):
        raise OSError("디스크")

    monkeypatch.setattr(sync, "attach", broken)
    sync._attach_topics(wiki, FakeEmbedder())  # 수집은 멈추지 않는다
    assert "결정 주제를 붙이지 못했다: 디스크" in caplog.text
    monkeypatch.setattr(sync, "attach", lambda w, e: 0)
    sync._attach_topics(wiki, None)
