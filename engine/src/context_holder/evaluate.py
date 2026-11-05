"""분류 평가셋(사람 라벨)과 분류기 판정을 비교한다.

평가셋에는 URL · 제목 · 라벨만 둔다. 글 본문은 공개 레포에 넣지 않고, 평가할 때
`ctx collect --out`으로 받은 파일과 URL로 이어 붙인다.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from context_holder.classify import DEFAULT_INBOX_AT, DEFAULT_SAVE_AT, Bucket, Decision

MIN_SAVE_PRECISION = 0.8  # plan M1 완료 기준


class LabelError(ValueError):
    """평가셋 파일이 잘못됐다."""


@dataclass(frozen=True)
class Label:
    url: str
    title: str
    source_id: str
    bucket: Bucket
    memo: str


@dataclass(frozen=True)
class Mismatch:
    url: str
    title: str
    expected: Bucket
    got: Bucket
    score: float


@dataclass(frozen=True)
class Report:
    total: int  # 판정이 있는 라벨 수
    exact: float  # 세 판정이 정확히 맞은 비율
    save_precision: float | None  # 저장으로 고른 것 중 정답도 저장인 비율
    save_recall: float | None  # 정답 저장 중 저장으로 고른 비율
    missed: tuple[Mismatch, ...]  # 정답 저장인데 버린 글 (가장 피할 실수)
    mismatches: tuple[Mismatch, ...]
    confusion: dict[Bucket, dict[Bucket, int]]  # [정답][판정]
    unjudged: tuple[str, ...]  # 판정이 없는 라벨


@dataclass(frozen=True)
class Thresholds:
    inbox_at: float
    save_at: float
    exact: float
    save_precision: float | None
    missed: int


def evaluate(labels: Sequence[Label], decisions: Sequence[Decision]) -> Report:
    by_url = {d.url: d for d in decisions}
    pairs = [(lb, by_url[lb.url]) for lb in labels if lb.url in by_url]
    confusion = {exp: dict.fromkeys(Bucket, 0) for exp in Bucket}
    mismatches: list[Mismatch] = []
    for lb, d in pairs:
        confusion[lb.bucket][d.bucket] += 1
        if lb.bucket is not d.bucket:
            mismatches.append(Mismatch(lb.url, lb.title, lb.bucket, d.bucket, d.score))
    predicted_save = sum(confusion[exp][Bucket.SAVE] for exp in Bucket)
    actual_save = sum(confusion[Bucket.SAVE].values())
    hit = confusion[Bucket.SAVE][Bucket.SAVE]
    return Report(
        total=len(pairs),
        exact=(len(pairs) - len(mismatches)) / len(pairs) if pairs else 0.0,
        save_precision=hit / predicted_save if predicted_save else None,
        save_recall=hit / actual_save if actual_save else None,
        missed=tuple(m for m in mismatches if m.expected is Bucket.SAVE and m.got is Bucket.DROP),
        mismatches=tuple(mismatches),
        confusion=confusion,
        unjudged=tuple(lb.url for lb in labels if lb.url not in by_url),
    )


def sweep_thresholds(
    labels: Sequence[Label],
    decisions: Sequence[Decision],
    step: float = 0.05,
    min_precision: float = MIN_SAVE_PRECISION,
) -> Thresholds:
    """점수는 그대로 두고 경계값만 바꿔 라벨과 가장 잘 맞는 조합을 찾는다.

    저장 정밀도가 min_precision 이상인 조합 중에서, 놓친 글이 적고 일치율이 높은 것을 고른다.
    """
    grid = [round(i * step, 4) for i in range(1, round(1 / step) + 1)]
    candidates: list[Thresholds] = []
    for inbox_at in grid:
        for save_at in grid:
            if not inbox_at < save_at:
                continue
            report = evaluate(labels, [_rebucket(d, inbox_at, save_at) for d in decisions])
            candidates.append(
                Thresholds(
                    inbox_at, save_at, report.exact, report.save_precision, len(report.missed)
                )
            )
    eligible = [
        c for c in candidates if c.save_precision is not None and c.save_precision >= min_precision
    ] or candidates
    # 놓친 글이 적을수록, 일치율이 높을수록, 지금 기본값에 가까울수록 좋다
    return min(
        eligible,
        key=lambda c: (
            c.missed,
            -c.exact,
            abs(c.inbox_at - DEFAULT_INBOX_AT) + abs(c.save_at - DEFAULT_SAVE_AT),
        ),
    )


def _rebucket(d: Decision, inbox_at: float, save_at: float) -> Decision:
    bucket = (
        Bucket.SAVE if d.score >= save_at else Bucket.INBOX if d.score >= inbox_at else Bucket.DROP
    )
    return Decision(d.url, d.score, bucket, d.topic, d.plus, d.minus)


def load_labels(path: Path) -> list[Label]:
    labels = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path}:{number}"
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise LabelError(f"{where}: JSON이 아니다") from e
        for key in ("url", "title", "source_id", "label"):
            if not isinstance(row.get(key), str):
                raise LabelError(f"{where}: {key} 값이 필요하다")
        try:
            bucket = Bucket(row["label"])
        except ValueError as e:
            raise LabelError(f"{where}: label은 save, inbox, drop 중 하나") from e
        labels.append(
            Label(row["url"], row["title"], row["source_id"], bucket, row.get("memo", ""))
        )
    return labels
