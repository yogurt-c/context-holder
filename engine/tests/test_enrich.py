from datetime import UTC, datetime

import pytest

from context_holder.collect import Article
from context_holder.enrich import Confidence, detect_media, enrich
from context_holder.llm import ErrorKind, LlmError, LlmResult

BODY = """## 배경

주문 저장 후 메시지 발행 전에 장애가 나면 이벤트가 사라졌다.
우리는 2PC, 발행 후 재시도, Transactional Outbox를 비교했다.

결국 같은 트랜잭션에 outbox 행을 쓰고 CDC 릴레이가 Kafka로 옮기는 방식을 골랐다.
도입 후 3개월 동안 이벤트 유실은 0건이었다.
"""


def article(markdown: str = BODY) -> Article:
    return Article(
        source_id="woowahan",
        url="https://techblog.woowahan.com/123",
        title="주문 이벤트 유실을 막기 위한 Transactional Outbox 도입기",
        published=datetime(2026, 9, 18, tzinfo=UTC),
        markdown=markdown,
        content_hash="h",
    )


class FakeLlm:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def structured(self, prompt, system, schema):
        self.calls.append((prompt, system, schema))
        if isinstance(self.reply, Exception):
            raise self.reply
        return LlmResult(data=self.reply, cost_usd=0.02, duration_ms=1000)


def reply(**decision_overrides):
    decision = {
        "topic": "이벤트 발행 일관성",
        "problem": "발행 전 장애로 이벤트 유실",
        "options": ["2PC", "발행 후 재시도", "Outbox + CDC"],
        "chosen": "Outbox + CDC",
        "tradeoffs": ["유실 없음 ↔ 릴레이 운영 부담"],
        "outcome": "3개월간 유실 0건",
        "evidence": ["도입 후 3개월 동안 이벤트 유실은 0건이었다."],
        "confidence": "high",
        **decision_overrides,
    }
    return {
        "summary": ["한 줄", "두 줄", "세 줄"],
        "key_points": ["핵심 1", "핵심 2", "핵심 3", "핵심 4", "핵심 5"],
        "tags": ["Transactional Outbox", "kafka", "CDC", "kafka"],
        "decision": decision,
    }


def test_enrich_returns_korean_fields_and_normalized_tags():
    llm = FakeLlm(reply())

    result = enrich(llm, article())

    assert result.summary == ("한 줄", "두 줄", "세 줄")
    assert len(result.key_points) == 5
    assert result.tags == ("transactional-outbox", "kafka", "cdc")
    prompt, system, _ = llm.calls[0]
    assert "Transactional Outbox 도입기" in prompt and "CDC 릴레이" in prompt
    assert "한국어" in system


def test_verified_evidence_keeps_confidence():
    result = enrich(FakeLlm(reply()), article())

    assert result.decision is not None
    assert result.decision.chosen == "Outbox + CDC"
    assert result.decision.confidence is Confidence.HIGH
    assert result.decision.evidence == ("도입 후 3개월 동안 이벤트 유실은 0건이었다.",)
    assert result.decision.unverified == ()


def test_evidence_match_ignores_spacing_and_markdown():
    llm = FakeLlm(reply(evidence=["**도입 후 3개월** 동안   이벤트 유실은 0건이었다"]))

    result = enrich(llm, article())

    assert result.decision.confidence is Confidence.HIGH


def test_unverified_evidence_lowers_confidence():
    llm = FakeLlm(
        reply(evidence=["원문에 없는 문장이다.", "도입 후 3개월 동안 이벤트 유실은 0건이었다."])
    )

    result = enrich(llm, article())

    assert result.decision.confidence is Confidence.LOW
    assert result.decision.evidence == ("도입 후 3개월 동안 이벤트 유실은 0건이었다.",)
    assert result.decision.unverified == ("원문에 없는 문장이다.",)


@pytest.mark.parametrize(
    "overrides",
    [{"options": ["하나뿐"]}, {"chosen": ""}],
)
def test_decision_without_real_choice_is_dropped(overrides):
    result = enrich(FakeLlm(reply(**overrides)), article())

    assert result.decision is None


def test_null_decision_is_kept_as_none():
    data = reply()
    data["decision"] = None

    assert enrich(FakeLlm(data), article()).decision is None


def test_body_is_trimmed_before_sending():
    llm = FakeLlm(reply())

    enrich(llm, article(markdown="가" * 100_000))

    prompt, _, _ = llm.calls[0]
    assert len(prompt) < 30_000


@pytest.mark.parametrize(
    "data",
    [
        {"summary": [], "key_points": ["a"], "tags": [], "decision": None},
        {"summary": ["a"], "key_points": "not a list", "tags": [], "decision": None},
    ],
)
def test_malformed_reply_is_bad_output(data):
    with pytest.raises(LlmError) as excinfo:
        enrich(FakeLlm(data), article())

    assert excinfo.value.kind is ErrorKind.BAD_OUTPUT


def test_llm_errors_propagate():
    with pytest.raises(LlmError):
        enrich(FakeLlm(LlmError(ErrorKind.LIMIT, "한도")), article())


@pytest.mark.parametrize(
    ("markdown", "provider"),
    [
        ("짧은 소개 [발표 영상](https://tv.naver.com/v/123) 입니다.", "naver_tv"),
        ("짧은 소개 https://naver.me/abc 입니다.", "naver_clip"),
        ("세션 [video](https://www.youtube.com/watch?v=xyz)", "youtube"),
        ("세션 https://youtu.be/xyz", "youtube"),
    ],
)
def test_detects_video_posts(markdown, provider):
    media = detect_media(markdown)

    assert media is not None
    assert media.provider == provider
    assert media.status == "summary_only"


def test_long_posts_with_video_links_are_not_video_posts():
    assert detect_media("본문 " * 2000 + " https://youtu.be/xyz") is None
    assert detect_media("영상 링크가 없는 짧은 글") is None


def test_unknown_confidence_defaults_to_medium_and_missing_tags_are_empty():
    data = reply(confidence="certain")
    data["tags"] = None

    result = enrich(FakeLlm(data), article())

    assert result.decision.confidence is Confidence.MEDIUM
    assert result.tags == ()
