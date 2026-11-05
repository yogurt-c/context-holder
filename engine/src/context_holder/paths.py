"""기기 로컬 경로. 위키 레포와 달리 커밋하지 않는 파일을 둔다 (plan 레포 구조)."""

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("CONTEXT_HOLDER_HOME", "~/.context-holder")).expanduser()


def default_wiki() -> Path:
    return home() / "wiki"


def etag_cache() -> Path:
    return home() / "etag-cache.json"
