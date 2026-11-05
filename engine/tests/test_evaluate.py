from pathlib import Path

import pytest

from context_holder.classify import Bucket, Decision
from context_holder.evaluate import (
    Label,
    LabelError,
    evaluate,
    load_labels,
    sweep_thresholds,
)


def label(n: int, bucket: Bucket) -> Label:
    return Label(url=f"https://a/{n}", title=f"t{n}", source_id="s", bucket=bucket, memo="")


def decision(n: int, score: float, bucket: Bucket) -> Decision:
    return Decision(f"https://a/{n}", score, bucket, "", (), ())


def test_evaluate_reports_precision_misses_and_confusion():
    labels = [
        label(1, Bucket.SAVE),
        label(2, Bucket.SAVE),
        label(3, Bucket.INBOX),
        label(4, Bucket.DROP),
        label(5, Bucket.SAVE),
    ]
    decisions = [
        decision(1, 0.9, Bucket.SAVE),
        decision(2, 0.2, Bucket.DROP),  # 놓친 글
        decision(3, 0.8, Bucket.SAVE),  # 저장 오판
        decision(4, 0.1, Bucket.DROP),
        decision(5, 0.5, Bucket.INBOX),
    ]

    report = evaluate(labels, decisions)

    assert report.total == 5
    assert report.exact == pytest.approx(2 / 5)
    assert report.save_precision == pytest.approx(1 / 2)
    assert report.save_recall == pytest.approx(1 / 3)
    assert [m.url for m in report.missed] == ["https://a/2"]
    assert report.confusion[Bucket.SAVE][Bucket.DROP] == 1
    assert report.confusion[Bucket.INBOX][Bucket.SAVE] == 1
    assert {m.url for m in report.mismatches} == {"https://a/2", "https://a/3", "https://a/5"}


def test_evaluate_counts_unjudged_labels_separately():
    report = evaluate(
        [label(1, Bucket.SAVE), label(2, Bucket.DROP)], [decision(1, 0.9, Bucket.SAVE)]
    )

    assert report.total == 1
    assert report.unjudged == ("https://a/2",)
    assert report.save_precision == 1.0


def test_precision_is_none_when_nothing_saved():
    report = evaluate([label(1, Bucket.DROP)], [decision(1, 0.1, Bucket.DROP)])

    assert report.save_precision is None
    assert report.save_recall is None


def test_sweep_finds_thresholds_that_match_labels_best():
    labels = [
        label(1, Bucket.SAVE),
        label(2, Bucket.SAVE),
        label(3, Bucket.INBOX),
        label(4, Bucket.INBOX),
        label(5, Bucket.DROP),
    ]
    decisions = [
        decision(1, 0.9, Bucket.SAVE),
        decision(2, 0.75, Bucket.SAVE),
        decision(3, 0.6, Bucket.INBOX),
        decision(4, 0.35, Bucket.DROP),
        decision(5, 0.1, Bucket.DROP),
    ]

    best = sweep_thresholds(labels, decisions, step=0.05)

    assert best.inbox_at <= 0.35 < 0.6 < best.save_at <= 0.75
    assert best.exact == 1.0


def test_sweep_requires_minimum_save_precision():
    labels = [label(1, Bucket.SAVE), label(2, Bucket.INBOX), label(3, Bucket.INBOX)]
    decisions = [
        decision(1, 0.9, Bucket.SAVE),
        decision(2, 0.85, Bucket.SAVE),
        decision(3, 0.5, Bucket.INBOX),
    ]

    best = sweep_thresholds(labels, decisions, step=0.05, min_precision=0.8)

    assert best.save_at > 0.85
    assert best.save_precision == 1.0


def test_load_labels_reads_jsonl(tmp_path: Path):
    path = tmp_path / "labels.jsonl"
    path.write_text(
        '{"url": "https://a/1", "title": "t", "source_id": "s", "label": "save", "memo": "m"}\n'
        "\n"
        '{"url": "https://a/2", "title": "t", "source_id": "s", "label": "drop"}\n',
        encoding="utf-8",
    )

    labels = load_labels(path)

    assert [(lb.url, lb.bucket, lb.memo) for lb in labels] == [
        ("https://a/1", Bucket.SAVE, "m"),
        ("https://a/2", Bucket.DROP, ""),
    ]


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ('{"url": "https://a/1", "title": "t", "source_id": "s", "label": "maybe"}', "label"),
        ('{"title": "t", "source_id": "s", "label": "save"}', "url"),
        ("not json", "JSON"),
    ],
)
def test_load_labels_rejects_bad_rows(tmp_path: Path, line: str, message: str):
    path = tmp_path / "labels.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    with pytest.raises(LabelError, match=message):
        load_labels(path)
