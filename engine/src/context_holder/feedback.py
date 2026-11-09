"""피드백을 기기별 파일에 이어 쓴다: `feedback/{yyyy-mm}-{기기}.jsonl` (plan 3.1).

두 기기가 같은 파일을 고치지 않으니 동기화할 때 충돌하지 않는다.
읽을 때는 모든 기기의 파일을 합친다.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum

from context_holder.wiki import Wiki, article_id


class Kind(Enum):
    NOT_RELEVANT = "not_relevant"
    BOOKMARK = "bookmark"
    SUGGEST_SOURCE = "suggest_source"  # MCP: 웹에서 찾은 좋은 글을 다음 수집 대상으로


@dataclass(frozen=True)
class Feedback:
    at: str
    kind: str
    url: str
    article_id: str
    device: str
    note: str | None = None  # suggest_source의 이유 등


def record_feedback(
    wiki: Wiki, device: str, kind: Kind, url: str, when: datetime, note: str | None = None
) -> Feedback:
    entry = Feedback(when.isoformat(), kind.value, url, article_id(url), device, note)
    path = wiki.root / "feedback" / f"{when:%Y-%m}-{device}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    return entry


def load_feedback(wiki: Wiki) -> list[Feedback]:
    folder = wiki.root / "feedback"
    entries = [
        Feedback(**json.loads(line))
        for path in sorted(folder.glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return sorted(entries, key=lambda e: e.at)
