from datetime import UTC, datetime
from pathlib import Path

import pytest

from context_holder.classify import (
    Bucket,
    Example,
    ProfileError,
    classify,
    load_default_profile,
    load_profile,
)
from context_holder.collect import Article
from context_holder.llm import ErrorKind, LlmError, LlmResult


def article(n: int, title: str = "글") -> Article:
    return Article(
        source_id="toss",
        url=f"https://toss.tech/{n}",
        title=f"{title} {n}",
        published=datetime(2026, 11, 5, tzinfo=UTC),
        markdown="본문 " * 2000,
        content_hash=f"h{n}",
    )


class FakeLlm:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[tuple[str, str]] = []

    def structured(self, prompt, system, schema):
        self.prompts.append((prompt, system))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LlmResult(data=reply, cost_usd=0.01, duration_ms=1000)


def item(i: str, score: float, **extra):
    return {"id": i, "score": score, "topic": "백엔드", "plus": ["DB"], "minus": [], **extra}


def test_buckets_by_threshold_and_keeps_reasons():
    llm = FakeLlm(
        [{"items": [item("1", 0.9), item("2", 0.55, minus=["임베딩 학습"]), item("3", 0.1)]}]
    )

    result = classify(llm, [article(1), article(2), article(3)], load_default_profile())

    buckets = {d.url: d.bucket for d in result.decisions}
    assert buckets == {
        "https://toss.tech/1": Bucket.SAVE,
        "https://toss.tech/2": Bucket.INBOX,
        "https://toss.tech/3": Bucket.DROP,
    }
    inbox = next(d for d in result.decisions if d.bucket is Bucket.INBOX)
    assert inbox.minus == ("임베딩 학습",)
    assert result.pending == ()


def test_prompt_contains_criteria_excerpt_and_feedback_examples():
    llm = FakeLlm([{"items": [item("1", 0.8)]}])
    examples = [Example(title="FE News 11월 소식", verdict="관련없음")]

    classify(llm, [article(1, "캐시 전략")], load_default_profile(), examples=examples)

    prompt, system = llm.prompts[0]
    assert "AI 엔지니어링" in system and "ML 모델링" in system
    assert "캐시 전략 1" in prompt
    assert "FE News 11월 소식" in prompt
    assert len(prompt) < 4000  # 본문은 앞부분만 보낸다


def test_batches_large_inputs():
    articles = [article(i) for i in range(1, 6)]
    llm = FakeLlm(
        [
            {"items": [item("1", 0.8), item("2", 0.8)]},
            {"items": [item("1", 0.8), item("2", 0.8)]},
            {"items": [item("1", 0.8)]},
        ]
    )

    result = classify(llm, articles, load_default_profile(), batch_size=2)

    assert len(llm.prompts) == 3
    assert {d.url for d in result.decisions} == {a.url for a in articles}


def test_missing_or_invalid_items_become_pending():
    llm = FakeLlm([{"items": [item("1", 0.8), item("2", 1.7), item("9", 0.5)]}])

    result = classify(llm, [article(1), article(2), article(3)], load_default_profile())

    assert [d.url for d in result.decisions] == ["https://toss.tech/1"]
    assert {p.url for p in result.pending} == {"https://toss.tech/2", "https://toss.tech/3"}


def test_llm_failure_marks_the_batch_pending_and_continues():
    llm = FakeLlm([LlmError(ErrorKind.AUTH, "로그인 만료"), {"items": [item("1", 0.9)]}])

    result = classify(
        llm, [article(1), article(2), article(3)], load_default_profile(), batch_size=2
    )

    assert [d.url for d in result.decisions] == ["https://toss.tech/3"]
    assert {p.url for p in result.pending} == {"https://toss.tech/1", "https://toss.tech/2"}
    assert all(p.reason == "auth: 로그인 만료" for p in result.pending)
    assert result.cost_usd == pytest.approx(0.01)


def test_custom_thresholds_from_profile(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        """
thresholds: {save: 0.8, inbox: 0.5}
include:
  - {topic: 백엔드, about: 서버와 데이터}
exclude:
  - {topic: 채용, about: 채용 공고}
""",
        encoding="utf-8",
    )

    profile = load_profile(path)

    assert profile.bucket_for(0.75) is Bucket.INBOX
    assert profile.bucket_for(0.8) is Bucket.SAVE
    assert profile.bucket_for(0.49) is Bucket.DROP


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("include: []\nexclude: []\n", "include"),
        ("thresholds: {save: 0.3, inbox: 0.5}\ninclude: [{topic: a, about: b}]\n", "thresholds"),
        ("include: [{topic: a}]\n", "about"),
        ("include: [\n", "읽을 수 없다"),
        ("- just a list\n", "매핑"),
        ("include: not-a-list\n", "목록"),
        ("include: [just-text]\n", "매핑"),
    ],
)
def test_rejects_invalid_profile(tmp_path: Path, body: str, message: str):
    path = tmp_path / "profile.yaml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ProfileError, match=message):
        load_profile(path)


def test_exclude_is_optional(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text("include:\n  - {topic: 백엔드, about: 서버}\n", encoding="utf-8")

    profile = load_profile(path)

    assert profile.exclude == ()
    assert (profile.save_at, profile.inbox_at) == (0.6, 0.3)


def test_borderline_topics_are_sent_as_inbox_guidance(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        """
include: [{topic: 백엔드, about: 서버}]
borderline: [{topic: ML 파이프라인, about: 학습 데이터 생성 중심 글}]
""",
        encoding="utf-8",
    )
    profile = load_profile(path)
    llm = FakeLlm([{"items": [item("1", 0.5)]}])

    classify(llm, [article(1)], profile)

    _, system = llm.prompts[0]
    assert profile.borderline[0].topic == "ML 파이프라인"
    assert "경계 주제" in system and "학습 데이터 생성 중심 글" in system
