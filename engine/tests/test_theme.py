"""라이트 · 다크 토큰의 글자와 바탕 대비가 4.5:1 이상인지 본다 (WCAG AA 본문)."""

import re
from importlib import resources

import pytest

MIN_CONTRAST = 4.5

# (글자, 바탕): 화면에서 실제로 겹치는 조합
PAIRS = [
    ("ink", "paper"),
    ("ink", "surface"),
    ("ink", "paper-deep"),
    ("ink-body", "paper"),
    ("ink-body", "surface"),
    ("ink-soft", "paper"),
    ("ink-soft", "surface"),
    ("ink-faint", "paper"),
    ("ink-faint", "surface"),
    ("ink-faint", "chrome"),
    ("ink-faint", "paper-deep"),
    ("chrome-ink", "chrome"),
    ("today", "paper"),
    ("today", "surface"),
    ("today", "today-tint"),
    ("decision", "paper"),
    ("decision", "surface"),
    ("decision", "decision-tint"),
    ("decision-ink", "decision-tint"),
    ("on-ink", "ink"),
    ("on-ink", "decision"),
    ("on-ink", "today"),
    ("on-ink", "ok"),
    ("ok", "paper"),
    ("ok", "surface"),
    ("ok", "ok-tint"),
    ("busy-ink", "busy-tint"),
    ("busy-ink", "paper"),
    ("busy-deep", "busy-tint"),
    ("error", "paper"),
    ("error", "surface"),
    ("error", "error-tint"),
    ("error", "error-row"),
    ("error-deep", "error-tint"),
    ("ink", "mark"),
]


def _tokens() -> dict[str, dict[str, str]]:
    css = resources.files("context_holder.screens").joinpath("static/tokens.css").read_text("utf-8")
    light_block = re.search(r"^:root \{(.*?)^\}", css, re.S | re.M).group(1)
    dark_block = re.search(r"prefers-color-scheme: dark\) \{\s*:root \{(.*?)\}", css, re.S).group(1)
    light = dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", light_block))
    dark = {**light, **dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", dark_block))}
    return {"light": light, "dark": dark}


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_formula():
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast("#777777", "#777777") == pytest.approx(1.0)


def test_dark_theme_overrides_every_color_it_needs():
    tokens = _tokens()
    used = {name for pair in PAIRS for name in pair}
    dark_only = set(tokens["dark"]) - set(tokens["light"])
    assert not dark_only
    assert all(tokens["dark"][n] != tokens["light"][n] for n in used)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_and_background_pairs_meet_aa(theme):
    colors = _tokens()[theme]
    failing = [
        f"{fg} on {bg}: {contrast(colors[fg], colors[bg]):.2f}"
        for fg, bg in PAIRS
        if contrast(colors[fg], colors[bg]) < MIN_CONTRAST
    ]
    assert not failing
