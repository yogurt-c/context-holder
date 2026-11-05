"""블로그마다 다른 게시일 표기를 UTC datetime으로 바꾼다."""

import re
from datetime import UTC, datetime
from typing import Any

DATE_IN_TEXT = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


def parse_date(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        match = DATE_IN_TEXT.search(text)
        if not match:
            return None
        year, month, day = (int(g) for g in match.groups())
        return datetime(year, month, day, tzinfo=UTC)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
