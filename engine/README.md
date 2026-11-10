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
| `ctx sync` | 요약 대기 글을 다시 처리하고, 새 글을 모아 분류 · 요약해 위키에 쓴다. 위키가 git 레포면 수집 잠금을 잡고 20건마다 push한다. `--events`로 진행률을 JSON 줄로 쓴다 |
| `ctx serve` | 화면 서버를 `127.0.0.1`의 임의 포트로 띄우고 주소와 토큰을 JSON 한 줄로 쓴다. `--open`으로 브라우저를 연다. `--app`(앱이 띄울 때)이면 매일 수집 스케줄러를 켜고 메뉴 · 알림 이벤트를 쓴다 ([app](../app/README.md)) |
| `ctx mcp` | 에이전트(Claude Code · Codex 등)용 MCP 서버 (stdio). tool 6개와 질의 로그 `~/.context-holder/mcp-queries.jsonl` |
| `ctx feedback` | 관련 없음 · 북마크를 이 기기의 피드백 파일에 남긴다 |
| `ctx collect` | 소스에서 새 글만 모아 소스별 결과를 본다. `--out`으로 JSONL 저장 |
| `ctx classify` | 모은 글의 관련도를 판정한다 |
| `ctx eval-classify` | 평가셋으로 분류 정확도를 잰다 ([eval](eval/README.md)) |
| `ctx search` | 위키를 키워드 + 의미 검색한다. `--keyword-only`, `--saved-only` |
| `ctx reindex` | 위키와 `vectors/`에서 검색 인덱스(`index.db`)를 다시 만든다 |
| `ctx reembed` | 원문을 다시 가져와 단락 벡터를 새 검색 모델로 만든다. `--model`, `--no-switch` |
| `ctx eval-search` | 평가셋으로 키워드만 쓸 때와 모델별 검색 recall@5를 비교한다 ([eval](eval/README.md)) |
| `ctx eval-mcp` | 결정 질문 평가셋을 MCP tool 경로 그대로 넣어 recall@5를 잰다 ([eval](eval/README.md)) |

분류 · 요약을 하는 명령(`sync`, `init`, `classify`, `eval-classify`)은 `--agent`(없으면 앱 설정의 에이전트와 계정)와 `--model`(없으면 에이전트 기본값)을 받는다.

GitHub 토큰은 macOS 키체인(`context-holder`)에서, 없으면 `gh auth token`에서 가져온다. 파일에는 쓰지 않는다. 앱의 첫 실행 설정(`/setup`)이 Device Flow로 로그인해 키체인에 저장하고, 8시간마다 리프레시 토큰으로 갱신한다.

기기 로컬 파일은 `~/.context-holder`(`CONTEXT_HOLDER_HOME`으로 변경)에 둔다. 위키 기본 위치는 그 아래 `wiki/`다. 검색 모델은 처음 쓸 때 `models/`에 내려받는다(bge-m3 약 570MB).

```bash
# 개발할 때는 저장소 안 임시 폴더를 쓴다 (.tmp는 gitignore). git 레포가 아닌 폴더는 push하지 않는다
CONTEXT_HOLDER_HOME=../.tmp/home uv run ctx sync --wiki ../.tmp/dev-wiki --days 4
```

## 에이전트 MCP 연결

```bash
# 앱의 첫 실행 설정 · 설정 화면이 같은 명령으로 등록한다. 개발 중 손으로 할 때:
claude mcp add context-holder --scope user -- "$(pwd)/.venv/bin/ctx" mcp
claude mcp list   # context-holder: ✔ Connected

codex mcp add context-holder -- "$(pwd)/.venv/bin/ctx" mcp
codex mcp list
```

## 에이전트 추가하기

1. `llm.py`: `AGENTS` · `AGENT_TOOLS`에 이름을 넣고, `structured()`로 구조화 응답을 내는 클래스와 `make_llm` 분기를 추가한다.
2. `{agent}setup.py`: 상태 · 설치 · 로그인 · 로그아웃 · MCP 등록 명령 (`codexsetup.py`가 예시).
3. `agentaccounts.CONFIG_VARS`: 앱 전용 계정의 설정 폴더 환경 변수.
4. `appsettings.Settings`: `{agent}_account` 필드.
5. `setupflow.py` · `screens/setuproutes.py`: 에이전트별 상태 · 작업 · 설치 · MCP 분기. 지금은 Claude · Codex 두 갈래로 나뉘어 있어 새 에이전트마다 분기를 넓혀야 한다.
6. `screens/templates/_icons.html`: 로고. 출처와 라이선스 파일을 `screens/static/icons/`에 둔다.
7. [README](../README.md#에이전트)의 지원 에이전트 표에 행을 추가한다.
