"""원문 Markdown을 검색용 단락으로 나눈다. 짧은 단락은 합치고 긴 단락은 문장 경계에서 자른다."""

import re

MIN_CHARS = 200
MAX_CHARS = 1000


def split_paragraphs(markdown: str) -> list[str]:
    blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", markdown)]
    pieces: list[str] = []
    for block in (b for b in blocks if b):
        pieces.extend(_split_long(block))
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) < MIN_CHARS and len(merged[-1]) + len(piece) < MAX_CHARS:
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    sentences = re.split(r"(?<=[.!?。다요])\s+", text)
    chunks: list[str] = []
    for sentence in sentences:
        while len(sentence) > MAX_CHARS:  # 문장 하나가 너무 길면 글자 수로 자른다
            chunks.append(sentence[:MAX_CHARS])
            sentence = sentence[MAX_CHARS:]
        if chunks and len(chunks[-1]) + len(sentence) + 1 <= MAX_CHARS:
            chunks[-1] = f"{chunks[-1]} {sentence}"
        elif sentence:
            chunks.append(sentence)
    return chunks
