"""PyInstaller로 묶은 단일 바이너리에서 pygit2가 동작하는지 확인한다.

확인 항목: libgit2 버전, HTTPS 지원(GitHub push에 필요), 저장소 생성과 커밋, 공개 저장소 clone.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import pygit2

START = time.perf_counter()


def commit_file(repo: pygit2.Repository, name: str, text: str) -> str:
    Path(repo.workdir, name).write_text(text, encoding="utf-8")
    repo.index.add(name)
    repo.index.write()
    tree = repo.index.write_tree()
    sig = pygit2.Signature("spike", "spike@example.com")
    oid = repo.create_commit("HEAD", sig, sig, "spike", tree, [])
    return str(oid)


def main() -> int:
    result: dict[str, object] = {
        "libgit2": pygit2.LIBGIT2_VERSION,
        "https": bool(pygit2.features & pygit2.enums.Feature.HTTPS),
        "ssh": bool(pygit2.features & pygit2.enums.Feature.SSH),
    }
    with tempfile.TemporaryDirectory() as tmp:
        repo = pygit2.init_repository(str(Path(tmp, "local")))
        result["commit"] = commit_file(repo, "a.md", "# hello\n")[:7]
        if "--clone" in sys.argv:
            t = time.perf_counter()
            cloned = pygit2.clone_repository(
                "https://github.com/octocat/Hello-World.git", str(Path(tmp, "clone"))
            )
            result["clone_head"] = str(cloned.head.target)[:7]
            result["clone_sec"] = round(time.perf_counter() - t, 2)
    result["startup_to_done_sec"] = round(time.perf_counter() - START, 2)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
