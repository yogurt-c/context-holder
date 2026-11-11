"""피드백을 기기별 파일에 이어 쓴다: `feedback/{yyyy-mm}-{기기}.jsonl` (plan 3.1).

두 기기가 같은 파일을 고치지 않으니 동기화할 때 충돌하지 않는다.
읽을 때는 모든 기기의 파일을 합친다.
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum

from context_holder.wiki import Wiki, article_id


class Kind(Enum):
    NOT_RELEVANT = "not_relevant"
    BOOKMARK = "bookmark"
    UNBOOKMARK = "unbookmark"  # 북마크 해제. 글마다 가장 나중 기록이 지금 상태다
    SAVE = "save"  # Inbox에서 보류 글을 저장으로 골랐다 (분류기의 저장 예시)
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


@dataclass(frozen=True)
class Bookmark:
    article_id: str
    at: datetime  # 마지막으로 북마크한 시각

    @property
    def key(self) -> tuple[datetime, str]:
        """정렬 · 커서 값. 시각이 같아도 글 id로 순서가 하나로 정해진다."""
        return (self.at, self.article_id)


def bookmarks(entries: list[Feedback]) -> list[Bookmark]:
    """지금 북마크된 글. (북마크 시각, 글 id)가 큰 것부터 (최근에 북마크한 글부터).

    모든 기기의 기록을 시간순으로 보고 글마다 가장 나중 기록(북마크 · 해제)을 따른다.
    다시 북마크한 글은 그 시각으로 맨 앞에 온다.
    """
    latest: dict[str, datetime | None] = {}
    for e in entries:
        if e.kind == Kind.BOOKMARK.value:
            latest[e.article_id] = _aware(datetime.fromisoformat(e.at))
        elif e.kind == Kind.UNBOOKMARK.value:
            latest[e.article_id] = None
    found = [Bookmark(aid, at) for aid, at in latest.items() if at is not None]
    return sorted(found, key=lambda b: b.key, reverse=True)


def bookmarked(entries: list[Feedback]) -> list[str]:
    """지금 북마크된 글 id. 최근에 북마크한 글부터."""
    return [b.article_id for b in bookmarks(entries)]


def _aware(at: datetime) -> datetime:
    """시간대가 없는 기록은 UTC로 본다 (시간대가 섞이면 비교할 수 없다)."""
    return at if at.tzinfo else at.replace(tzinfo=UTC)
