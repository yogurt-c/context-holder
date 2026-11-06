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
| `ctx connect` | GitHub 계정에서 `context-wiki`를 찾아 받는다. 없으면 private으로 만든다 |
| `ctx pull` | 다른 기기의 변경을 받아 바뀐 글만 다시 인덱싱한다 |
| `ctx init` | 첫 실행: 소스마다 최근 50개(1년 이내)를 가져와 위키를 채운다. `--status`, `--dry-run` |
| `ctx sync` | 요약 대기 글을 다시 처리하고, 새 글을 모아 분류 · 요약해 위키에 쓴다. 위키가 git 레포면 수집 잠금을 잡고 20건마다 push한다 |
| `ctx feedback` | 관련 없음 · 북마크를 이 기기의 피드백 파일에 남긴다 |
| `ctx collect` | 소스에서 새 글만 모아 소스별 결과를 본다. `--out`으로 JSONL 저장 |
| `ctx classify` | 모은 글의 관련도를 판정한다 |
| `ctx eval-classify` | 평가셋으로 분류 정확도를 잰다 ([eval](eval/README.md)) |
| `ctx search` | 위키를 키워드 + 의미 검색한다. `--keyword-only`, `--saved-only` |
| `ctx reindex` | 위키와 `vectors/`에서 검색 인덱스(`index.db`)를 다시 만든다 |
| `ctx reembed` | 원문을 다시 가져와 단락 벡터를 새 검색 모델로 만든다. `--model`, `--no-switch` |
| `ctx eval-search` | 평가셋으로 키워드만 쓸 때와 모델별 검색 recall@5를 비교한다 ([eval](eval/README.md)) |

GitHub 토큰은 macOS 키체인(`context-holder`)에서, 없으면 `gh auth token`에서 가져온다. 파일에는 쓰지 않는다.

기기 로컬 파일은 `~/.context-holder`(`CONTEXT_HOLDER_HOME`으로 변경)에 둔다. 위키 기본 위치는 그 아래 `wiki/`다. 검색 모델은 처음 쓸 때 `models/`에 내려받는다(bge-m3 약 570MB).

```bash
# 개발할 때는 저장소 안 임시 폴더를 쓴다 (.tmp는 gitignore). git 레포가 아닌 폴더는 push하지 않는다
CONTEXT_HOLDER_HOME=../.tmp/home uv run ctx sync --wiki ../.tmp/dev-wiki --days 4
```
