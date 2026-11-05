# context-holder engine

수집 · 정리 워커, 인덱서, 검색, MCP 서버를 담은 Python 엔진. 명령은 `ctx`.

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## 명령

| 명령 | 하는 일 |
|---|---|
| `ctx sync` | 요약 대기 글을 다시 처리하고, 새 글을 모아 분류 · 요약해 위키에 쓴다 |
| `ctx collect` | 소스에서 새 글만 모아 소스별 결과를 본다. `--out`으로 JSONL 저장 |
| `ctx classify` | 모은 글의 관련도를 판정한다 |
| `ctx eval-classify` | 평가셋으로 분류 정확도를 잰다 ([eval](eval/README.md)) |

기기 로컬 파일은 `~/.context-holder`(`CONTEXT_HOLDER_HOME`으로 변경)에 둔다. 위키 기본 위치는 그 아래 `wiki/`다.

```bash
# 개발할 때는 저장소 안 임시 폴더를 쓴다 (.tmp는 gitignore)
CONTEXT_HOLDER_HOME=../.tmp/home uv run ctx sync --wiki ../.tmp/dev-wiki --days 4
```
