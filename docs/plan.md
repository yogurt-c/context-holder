# context-holder 계획

빅테크 기술블로그를 매일 수집해 개인 위키로 관리하고, 기술 결정 시 빅테크 사례를 에이전트 문맥으로 주입하는 개인용 도구.

- 개요: [architecture/overview.png](architecture/overview.png)
- 상세 아키텍처: [architecture/architecture.png](architecture/architecture.png)
- 화면 시안: [ui/screens/](ui/screens/) (디자인 시스템 · Today · Search · Decisions · Inbox · Sources · 메뉴바 · 첫 실행 · 상태별)

## 1. 목표

| # | 요구사항 | 담당 |
|---|---|---|
| ① | 빅테크 기술블로그를 주기적으로 수집해 위키에 저장 | `ctx sync` 수집 워커 |
| ② | 위키 인덱싱 | Indexer → `index.db` |
| ③ | 검색 + 당일 주요 이슈를 한눈에 | 데스크톱 앱(메뉴바 + Today / Search 창, 수집 완료 알림) |
| ④ | 기술 결정 시 빅테크 결정 사례를 문맥으로 주입 | Decision Record + MCP 서버 |
| ⑤ | 업무와 무관한 기술 정리 | Relevance Classifier, 웹의 피드백 버튼, Pruner |

범위 밖: 다중 사용자, 공개 서비스, 서버 호스팅, CI, 모바일 앱, Electron.

## 2. 프로그램 형태

**Docker Desktop처럼 백그라운드에 상주하는 데스크톱 앱.** 평소엔 메뉴바 아이콘으로 상태만 보이고, 필요할 때 창을 연다.

```
context-holder.app (Tauri 2)
 ├─ 메뉴바 아이콘: 상태 색 (완료 · 수집 중 · 오류) + 빠른 메뉴
 ├─ 메인 창: Today · Search · Decisions · Inbox · Sources
 └─ sidecar: ctx 엔진 (Python) 실행 · 감시 · 재시작
        ├─ 스케줄러 + 수집 워커 (ctx sync)
        ├─ 인덱서
        └─ 화면용 HTTP (127.0.0.1)
Claude Code ── ctx mcp (stdio) : 앱과 독립 실행, index.db 직접 읽기
```

| 구성 | 역할 |
|---|---|
| **데스크톱 앱 (Tauri)** | 메뉴바, 창, 로그인 시 자동 실행, macOS 알림, 엔진 프로세스 관리 |
| **ctx 엔진 (Python)** | 스케줄러, 수집 워커, 인덱서, 화면 렌더링 (HTTP, 127.0.0.1) |
| `ctx mcp` | MCP 서버 (stdio). Claude Code가 직접 실행한다. 앱이 꺼져 있어도 검색할 수 있다 |
| `ctx` CLI | 터미널 검색과 관리. 엔진과 같은 패키지 |

메뉴바 메뉴:
- 상태 줄: "오늘 09:14 수집 완료 · 신규 5건" / "수집 중 3/14" / "오류: 소스 2개 실패"
- 브리프 열기, 검색
- 지금 수집, 일시정지
- 종료

창을 닫아도 앱은 메뉴바에 남아 수집을 계속한다.

구현:
- 앱은 그리기만 한다. 언제 수집할지, 메뉴에 무엇을 보일지, 알릴지는 엔진(`ctx serve --app`)이 정한다. 앱과 엔진은 stdin · stdout 줄로만 주고받고, 화면(웹뷰)에는 IPC 권한을 주지 않는다 ([app/README](../app/README.md)).
- 메뉴바 아이콘은 단색 template 이미지 네 가지(평상시 · 수집 중 · 오류 · 일시정지)다. 오류는 template이라 빨간 점 대신 채운 점으로 보인다.
- 창은 엔진 주소를 띄운다. 창이 열려 있을 때만 Dock 아이콘을 보인다. 키보드 단축키는 두지 않는다.

선택 이유:
- 매일 자동 수집하는 상주 작업이 있고, 상태를 계속 보여 줘야 한다. 브라우저 탭보다 메뉴바 앱이 가시성이 좋다.
- **Tauri 2**는 macOS 내장 WebKit을 써서 앱 자체 메모리가 수십 MB 수준이다. tray, autostart, notification, sidecar가 공식 플러그인으로 있다. Electron은 상주 앱으로 쓰기에 무겁다.
- 화면은 엔진이 렌더링한 HTML을 Tauri 창이 띄운다. 프론트엔드 빌드가 없다.
- 대안: Mac 전용으로 더 가볍게 가려면 SwiftUI 메뉴바 앱 + WKWebView. 같은 엔진을 그대로 쓸 수 있다.

## 3. 실행 환경

- **실행 시점**: 앱이 로그인 시 자동 실행되고 엔진 스케줄러가 판단한다.
  - 앱 시작, 잠자기 해제, 평일 09:30 중 하나가 발생했을 때 "오늘 미수집"이면 수집한다.
  - 완료되면 macOS 알림을 띄운다(신규 N건, 저장 M건). 알림을 누르면 브리프 창이 열린다.
  - 엔진이 `ctx sync --events`를 자식 프로세스로 띄우고 진행률(소스 · 분류 · 요약)을 메뉴 상태 줄과 사이드바 카드에 보여 준다. 오늘 기록이 끝났거나 다른 기기가 수집 중이면 띄우지 않고, 실패 · 멈춤이면 30분 뒤 다음 계기에 다시 한다. 잠자기 해제는 1분마다 보는 벽시계가 크게 건너뛴 것으로 안다.
  - "지금 수집"(메뉴, 사이드바, 며칠째 수집 못함 알림 띠)은 일시정지와 재시도 간격을 무시하지만, 오늘 수집이 끝났으면 누를 수 없다(하루 한 번 잠금).
  - 일시정지: 1시간 · 오늘 하루 · 다시 켤 때까지. 기기 로컬 `app-state.json`에 남긴다.
  - 알림은 하루 최대 2번, 종류마다 한 번: 신규 저장 1건 이상(이 기기가 수집했을 때), Claude 로그인 · 한도로 멈춤, 소스 절반 이상 실패, 3일째 수집 못함. 보낸 기록은 `notices.json`.
  - 오늘의 글은 앱(Today 창, 메뉴바, 알림)에서만 보여 준다. Claude 세션에는 넣지 않는다. Claude는 필요할 때 MCP로 찾는다(8장).
- **모든 기기가 꺼져 있던 날**: 다음 실행 때 "마지막으로 본 글 이후"부터 따라잡는다. feed가 최근 글을 보존하므로 누락은 거의 없다.
- **동시 접근**: index.db는 SQLite WAL 모드다. 엔진이 쓰는 동안에도 `ctx mcp`가 읽을 수 있다. index.db 쓰기는 엔진만 한다.

### 3.1 다중 기기 동기화

목적은 **기기와 무관한 개인 지식창고**다. GitHub private 레포(`context-wiki`)가 원본이고, 앱을 설치한 모든 기기는 동등한 사본이다. 어느 기기든 수집할 수 있다.

```
            GitHub context-wiki (원본)
           ▲ push / ▼ pull      ▲ push / ▼ pull
   기기 A (앱 + index.db)     기기 B (앱 + index.db)
```

**레포에 두는 것 · 기기에만 두는 것**

| 레포 (모든 기기 공유) | 기기 로컬 (재생성 가능) |
|---|---|
| 위키 · 결정 기록 · inbox · archive | `index.db` |
| `config/` (소스 · 관심 프로필) | 로그 |
| `feedback/` (피드백) | ETag 캐시 (수집 최적화용) |
| `state/` (수집 이력 · 실행 기록) | MCP 질의 로그 |

수집 판단에 필요한 상태(어떤 글을 이미 봤고 어떻게 판정했는지)는 전부 레포의 `state/`에 둔다. 그래서 어느 기기에서 수집해도 결과가 같다.

**하루 한 번만 수집하기 (git push를 잠금으로 사용)**

1. `git pull --rebase`
2. `state/runs/{date}.json`이 이미 있으면 수집하지 않는다. pull과 인덱싱만 한다.
3. 없으면 `{device, started_at, heartbeat_at, status: running}`으로 파일을 만들어 커밋하고 **즉시 push**한다.
   - push 성공: 이 기기가 오늘 수집을 맡는다.
   - push 거절(다른 기기가 먼저 push): pull해서 상대의 실행 기록을 확인하고 수집하지 않는다.
4. 수집하는 동안 글 20건마다 `heartbeat_at`을 갱신해 그때까지 쓴 글과 함께 커밋 · push한다. 기기가 중간에 꺼져도 push한 만큼은 남는다.
5. 수집이 끝나면 `status: done`과 결과 수치를 기록해 커밋 · push한다. 한도 · 로그인 문제로 멈추면 `status: stopped`로 풀어 같은 날 다시 시도할 수 있게 한다.
6. 마지막 heartbeat 후 2시간이 지나면(기기가 중간에 꺼진 경우) 다른 기기가 이어받는다. 수집이 2시간보다 오래 걸려도 heartbeat가 이어지는 동안은 이어받지 않는다.
- 날짜는 기기의 현지 날짜다. 첫 실행 초기 수집은 `state/runs/init.json`을 같은 방식으로 쓰고, 초기 수집이 도는 동안 매일 수집은 수집을 건너뛴다.

GitHub의 브랜치 갱신은 원자적이라, 두 기기가 동시에 시도해도 push는 한 쪽만 성공한다. 별도 잠금 서버가 필요 없다. 주간 Pruner도 `state/runs/prune-{yyyy-ww}.json`으로 같은 방식을 쓴다.

**충돌을 구조적으로 피하기**

- **글 파일은 한 번만 쓴다.** 경로는 canonical URL에서 결정적으로 만든다. 이미 있는 글은 수집에서 건너뛴다. 다시 쓰는 경우는 `ctx enrich` · Pruner뿐이다.
- **피드백은 기기별 파일에 이어 쓴다.** `feedback/{yyyy-mm}-{host}.jsonl`. 두 기기가 같은 파일을 고치지 않는다.
- **수집 이력도 월별 파일에 이어 쓴다.** `state/seen/{yyyy-mm}.jsonl` (URL 해시, 판정, 점수). 수집을 맡은 기기만 쓴다.
- push가 거절되면 `pull --rebase` 후 다시 push한다(최대 3회). 그래도 충돌이 나면 원격 내용을 우선하고, 로컬 변경은 다음 실행에서 다시 만든다.

**동기화 시점과 인덱스 갱신**

- 앱 시작, 잠자기 해제, 앱이 켜져 있는 동안 30분마다 pull한다.
- pull 후 글마다 파일 시그니처를 비교해 바뀐 글만 다시 인덱싱한다. 바뀐 게 없으면 1초 남짓이다.
- 오프라인이면 로컬에 커밋해 두고 연결되면 push한다.

**새 기기 추가**: 3.2의 초기 설정과 같다. GitHub 로그인 후 기존 `context-wiki`를 찾으면 clone하고 전체 인덱싱한다. 글 286건 기준 전체 인덱싱은 약 8분이다(위키 필드를 검색 모델로 계산). 진행률을 보여 준다.

**위키 커밋 저자**: 기기의 git 설정(`user.name` · `user.email`)을 쓰고, 없으면 GitHub 계정의 noreply 주소를 쓴다.

### 3.2 초기 설정 · 인증

기준은 **아무것도 설치돼 있지 않은 Mac에 처음 설치하는 사용자**다. git, Python, Claude Code, GitHub CLI가 모두 없다고 가정한다.

**준비물**
- macOS
- GitHub 계정
- Claude Pro/Max 구독 (필수). 분류 · 요약 · 결정 기록을 모두 Claude가 처리한다.

**원칙**
- **필요한 실행 환경은 앱에 넣는다.** 사용자가 따로 설치하는 것이 없게 한다.
  - Python 엔진: PyInstaller로 묶어 앱 번들에 포함한다.
  - git: 앱에 내장한 git 라이브러리(pygit2)로 clone · pull · push한다. Xcode 명령줄 도구나 SSH 키가 필요 없다.
- **브라우저 승인은 최대 2번이다.** GitHub 1번, Claude 1번. 토큰을 복사해 붙여 넣는 단계는 없다.
- **한 번 로그인하면 다시 묻지 않는다.** 연결이 끊기면 메뉴바를 경고 상태로 바꾸고 "다시 연결" 버튼만 보여 준다.

**첫 실행 흐름**

```
1. 환영 화면           [시작]
2. GitHub로 로그인     앱이 코드를 띄우고 브라우저를 연다 → 사용자가 승인 (Device Flow)
                       → 계정에서 context-wiki 탐색: 있으면 clone, 없으면 private으로 생성
3. Claude 연결        Claude Code가 없으면 앱이 공식 설치 프로그램으로 설치
                       → 로그인 창을 띄움 → 사용자가 브라우저에서 승인
                       → 시험 호출로 연결 확인. 연결 전에는 수집을 시작하지 않는다
4. Claude Code 연동    MCP 서버 등록
                       ~/.claude 설정은 덮어쓰지 않고 병합, 변경 내용을 보여 주고 백업 후 적용
5. 완료                초기 수집(5.4)을 시작하고 진행률을 보여 준다. 검색 모델은 뒤에서 내려받는다
```

사용자가 하는 일: 버튼 몇 번 + 브라우저 승인 최대 2번.

**인증 정보 보관**

| 항목 | 방식 | 보관 위치 |
|---|---|---|
| GitHub | Device Flow로 받은 OAuth 토큰 (`repo` 권한). 앱 개발자가 GitHub OAuth App을 한 번 등록하고 client ID만 앱에 넣는다 | macOS 키체인 |
| Claude | Claude Code의 로그인 세션을 그대로 사용 (`claude -p`) | Claude Code가 관리 |

- 토큰은 위키 레포나 설정 파일에 절대 쓰지 않는다.
- GitHub 토큰은 내 모든 레포에 접근할 수 있다. 간편함을 위한 선택이다. 권한을 위키 레포 하나로 좁히는 fine-grained token 입력은 고급 설정으로만 둔다.
- 이미 `gh` CLI나 Claude Code에 로그인된 사용자라면 그 로그인을 감지해 승인 단계를 건너뛴다.

구현:
- 첫 실행 설정은 엔진 화면(`/setup`)이다. 위키 · GitHub 로그인 · Claude 로그인 · MCP 등록 중 하나라도 없으면 Today 대신 띄운다. 이미 다 갖춘 기기(다른 방법으로 연결)는 건너뛴다.
- GitHub OAuth App(client ID `Ov23liLDrGVpOxrV5EwI`, 공개 값)은 토큰 만료를 켰다. 접근 토큰 8시간, 리프레시 토큰 6개월(쓰면 바뀐다). Device Flow 토큰은 client secret 없이 갱신된다. 키체인에 접근 토큰 · 만료 시각 · 리프레시 토큰을 함께 두고 만료 5분 전부터 갱신한다. 화면 서버 · 수집 · `ctx mcp`가 동시에 갱신하지 않게 기기 로컬 잠금 파일(`github-token.lock`)을 쓴다. 갱신이 실패하면 다시 로그인을 요청한다. push할 때마다 토큰을 다시 받아 긴 첫 수집 중에도 만료되지 않는다.
- 키체인 읽기 · 쓰기는 `keyring`(macOS Security 프레임워크)으로 한다. `security` 명령에 토큰을 인자로 넘기지 않는다.
- Claude: `claude auth status`로 확인하고, 없으면 공식 설치 프로그램(claude.ai/install.sh)을 사용자가 눌렀을 때 실행한다. 로그인은 `claude auth login --claudeai`.
- MCP 등록은 `claude mcp add --scope user`로 한다. Claude Code가 자기 설정을 병합하므로 `~/.claude`를 직접 고치거나 백업하지 않는다. 등록 명령은 지금 돌고 있는 엔진이다. 설정 화면에서 등록된 경로가 지금 엔진과 다르면 알리고 다시 등록할 수 있다.
- Finder · 로그인 자동 실행으로 뜬 앱은 PATH가 짧다. `claude` · `gh`는 PATH, 흔한 설치 위치(`~/.local/bin`, `/opt/homebrew/bin` 등), 로그인 셸 순으로 찾는다.

### 레포 구조 (`context-wiki`)

```
wiki/{company}/{yyyy}/{slug}.md   # 요약 + 핵심 내용 + Decision Record + 원문 링크 (전문 미저장)
vectors/{model}/{article_id}.f16  # 원문 단락 벡터 (텍스트 없음, 1회 쓰기) + 같은 이름의 .json 메타
decisions/{topic}.yaml            # 주제별 결정 사례 모음
inbox/                            # 관련도 애매(0.3~0.6) 보류 글
archive/                          # Pruner 정리분
transcripts/{media_id}.txt        # 영상 대본 (M4)
config/sources.yaml               # 소스 목록 · 수집 방식 · 회사 가중치
config/profile.yaml               # 수집 · 경계 · 제외 주제와 경계값
config/search.yaml                # 모든 기기가 쓰는 검색 모델 id
feedback/{yyyy-mm}-{host}.jsonl   # 관련없음 · 북마크 · suggest_source (기기별 파일)
state/runs/{date}.json            # 그날 수집을 맡은 기기 · 상태 · 결과 (수집 잠금)
state/seen/{yyyy-mm}.jsonl        # 본 글의 URL 해시 · 판정 · 점수 (중복 수집 방지)
state/sources.json                # 소스별 마지막으로 본 글
state/init.json                   # 초기 수집 대기열 · 진행 상태 (URL · 제목 · 날짜만, 본문 없음)
```

기기 로컬 (커밋 안 함): `~/.context-holder/index.db`, `~/.context-holder/logs/`, `~/.context-holder/etag-cache.json`, `~/.context-holder/mcp-queries.jsonl`, `~/.context-holder/app-state.json`(설정 · 일시정지), `~/.context-holder/github-token.lock`, `~/.context-holder/notices.json`(보낸 알림)

## 4. LLM Gateway

LLM 호출은 한 인터페이스 뒤에 두고, 작업별로 백엔드를 설정으로 고른다.

모든 LLM 작업은 Claude 구독(`claude -p --output-format json`)으로 처리한다. 수집 대상 글이 하루 2~3건 수준이라 구독 사용량에 주는 영향이 작다.

| 작업 | 호출 방식 | 비고 |
|---|---|---|
| 관련도 분류 | 그날 새 글을 한 번에 묶어 1회 호출 | 제목 · 요약 · feedback 예시를 함께 전달 |
| 요약 + Decision Record | 저장 대상 글마다 1회 호출 | 핵심 가치(④)라 품질 우선 |
| 주간 Pruner 재평가 | 저장된 글의 요약을 50건씩 묶어 호출 | 전문은 보내지 않음 |
| 영상 보강 (M4) | 대본 단위 호출 | 대본이 길어 요약 단계를 나눔 |

- **실패 처리**: 한도 초과 · 로그인 만료 · 네트워크 오류가 나면 글을 `status: pending`으로 저장하고 다음 실행에서 다시 처리한다. 메뉴바에 "요약 대기 N건"을 표시한다.
- LLM 호출은 한 인터페이스 뒤에 둔다. 나중에 소스를 크게 늘려 분류 양이 많아지면 다른 백엔드를 붙일 수 있다.
- API 키는 사용하지 않는다.

## 5. 파이프라인 (`ctx sync`)

0. **Pull · 수집 잠금**: `git pull --rebase` 후 오늘 실행 기록을 확인하고, 없으면 잠금을 push한다 (3.1).
1. **Fetcher**: `sources.yaml`을 순회한다. RSS/Atom을 먼저 쓰고 없으면 HTML. ETag와 "마지막으로 본 글"로 증분 수집한다. robots.txt와 rate limit을 지킨다.
2. **Extract · Dedup**: Readability → Markdown 변환. canonical URL + SHA-256으로 중복 제거. 영상 글을 감지한다.
3. **Relevance Classifier**: `profile.yaml` 주제 기준과 feedback 예시로 Claude가 판정한다 (5.1).
   - ≥ 0.6: 저장
   - 0.3 ~ 0.6: `inbox/`
   - < 0.3: Drop (로그만)
   - 경계값은 M0 평가셋으로 보정했다 ([engine/eval](../engine/eval/README.md)).
4. **Enricher**: 한국어 3줄 요약, 한국어 핵심 내용 5~10줄, 영문 정규화 태그, Decision Record를 만들고 evidence를 검증한다. 원문 단락을 검색 모델로 벡터화한다.
5. **Commit · Push · Index**: 위키, `vectors/`, `state/`에 쓰고 커밋 · push한 뒤 증분 인덱싱한다. push 거절 시 rebase 후 재시도.

목표 처리 시간: 하루 분량 10분 이내.

### 5.1 관련도 기준

AI · ML은 통째로 거르지 않는다. **AI를 엔지니어링에 쓰는 글은 수집하고, 모델 자체를 만드는 글은 거른다.** 검색 · 추천도 모델 구조가 아니라 측정 · 통합 · 서빙을 다루면 수집한다.

| 판정 | 주제 | 예 (2026년 실제 글 제목) |
|---|---|---|
| 수집 | 백엔드 · 인프라 · 데이터 | 동시성, DB, 메시징, 배포, 장애 분석, 아키텍처 |
| 수집 | 프론트엔드 | 웹 성능, 렌더링, 패키지 관리, 웹뷰 |
| 수집 | **AI 엔지니어링** | LLM 비용 최적화, 서빙 인프라, 에이전트 · MCP · 하네스, 컨텍스트 설계, AI로 테스트 · 개발 자동화, LLM 평가 체계 |
| 수집 | 검색 · 추천 시스템 엔지니어링 | 추천 효과 측정과 실험, 임베딩 · 모델을 서비스에 통합하는 방식과 비용, 검색 · 추천 서빙 |
| 보류 | 경계 주제 | 학습 데이터 생성 · 합성, 개인화 ML 플랫폼, 결정 사례 없는 기술 동향 정리 |
| 거름 | ML 모델링 | 모델 아키텍처 설계, 비전 · 음성 모델 개발, 논문 구현 |
| 거름 | 그 외 | 디자인, 행사 홍보, 채용, 조직문화 |

- `profile.yaml`의 `include` / `borderline` / `exclude`는 키워드가 아니라 위 **주제 단위**로 적는다. 키워드 규칙은 1차 후보 추리기에만 쓴다. "AI"라는 단어만으로 거르면 에이전트 · 하네스 글까지 빠진다.
- 경계 주제는 `borderline`에 적는다. 분류기가 보류 점수를 주고, Inbox 피드백으로 기준을 다듬는다.

### 5.2 Decision Record

```yaml
decision:
  topic: "메시지 큐 선택"            # 정규화된 주제 (decisions/{topic}.yaml 키)
  problem: "..."
  options: ["Kafka", "Pulsar"]
  chosen: "Pulsar"
  tradeoffs: ["...", "..."]
  outcome: "..."                     # 수치 결과가 있으면 포함
  evidence: ["원문 인용 1~2문장"]
  confidence: high | medium | low
```

- **evidence는 코드로 검증한다.** 인용 문장이 원문에 실제로 있는지 정규화한 문자열로 대조한다. 없으면 `confidence: low`로 내리고 화면에 표시한다.
- 결정 내용이 없는 글은 `decision: null`로 둔다.

### 5.3 영상 글

NAVER D2의 Engineering Day 세션 글처럼 본문은 요약이고 영상이 붙은 글을 다룬다.

- **감지**: 본문이 3,000자 미만이고 영상 링크(`clip.naver.com`, `naver.me`, YouTube 등)가 있다.
- **기본**: 본문 요약만으로 Decision Record를 만든다(`media.status: summary_only`). D2 세션 글은 본문에 주제, 문제, 분석, 조치, 결과가 정리돼 있어 이것만으로도 쓸 만하다.
- **보강 (M4)**: 관련도 ≥ 0.85인 글 또는 `ctx enrich <id>`로 지정한 글만 처리한다.
  1. 슬라이드 텍스트
  2. yt-dlp가 지원하는 출처면 자막, 없으면 Whisper
  3. 네이버 클립: Playwright로 HLS 주소 확보 → ffmpeg 오디오 → Whisper (**검증 전**. yt-dlp 2026.10.26은 네이버 클립 미지원 확인)
  - 대본을 `transcripts/`에 저장한 뒤 Claude로 Record를 보강하고 타임스탬프 링크를 추가한다.
- **안전장치**: 90분 초과 영상 skip, 하루 처리 개수 제한, 3회 실패 시 `failed`.

```yaml
media:
  url: https://naver.me/...
  provider: naver_clip | youtube | other
  transcript_source: none | slides | captions | stt
  status: summary_only | queued | done | failed | skipped
```

### 5.4 첫 실행 초기 수집 (`ctx init`)

위키가 비어 있으면 feed보다 오래된 글까지 가져와 첫날부터 쓸 만한 위키를 만든다.

- **범위**: 소스마다 최근 50개, 1년 이내 (`--per-source`, `--days`로 바꿀 수 있다). 13개 소스 기준 약 400건.
- **글 목록**: 소스 설정의 `archive`로 지난 글 주소를 최신순으로 얻는다.

| 방식 | 소스 |
|---|---|
| 목록 API | NAVER D2, NHN Cloud |
| WordPress feed 페이지(`?paged=N`) | 우아한형제들, GitHub |
| 목록 페이지(`?page=N`) | Toss |
| 사이트맵(글 번호 역순) | Kakao |
| feed만 | LINE(50건), 올리브영(209건), Cloudflare(20건), Netflix · 당근 · 무신사 · Airbnb(10건, Medium이 아카이브와 글 페이지를 막음) |

- **단계**: 목록 찾기(Claude 없음) → 20건씩 분류 · 요약 · 위키 쓰기. 소스를 번갈아 처리해 한 소스가 시간을 독차지하지 않는다.
- **이어서 하기**: 대기열과 진행 상태를 `state/init.json`에 둔다. 한도 · 로그인 문제로 멈추면 다음 실행에서 이어서 한다. 목록 찾기가 실패한 소스는 최대 3번 다시 시도한다.
- **보류 글**: 자동 승인하지 않는다. `origin: init`으로 표시해 Inbox에서 따로 묶는다(7장).
- **진행 표시**: `ctx init`은 20건마다 진행률과 예상 시간을, `ctx init --status`는 소스별 표를 보여 준다. `ctx init --dry-run`은 Claude 없이 가져올 글 수만 센다. 앱은 첫 실행 3단계 · 메뉴바 상태 줄 · Sources 화면에 같은 정보를 보여 준다.
- **사이트 부담**: 같은 사이트에는 1초에 한 번만 요청하고 robots.txt를 지킨다(모든 요청에 적용).

## 6. 인덱싱

검색은 **키워드 검색 + 다국어 임베딩 검색**을 함께 돌리고 RRF로 합친다. 한국어 질의로 영어 글을, 영어 질의로 한국어 글을 찾는다.

| 대상 | 키워드 (FTS5) | 의미 (임베딩) | 어디서 만드나 |
|---|---|---|---|
| 제목, 요약, 핵심 내용, Decision Record, 태그 | O | O (필드 · 줄 단위) | 기기마다 위키에서 계산 |
| 원문 단락 | X | O (단락 단위) | 수집할 때 계산해 `vectors/`에 저장 · 동기화 |

- `index.db`는 SQLite FTS5다. 위키와 `vectors/`에서 언제든 다시 만들 수 있다(`ctx reindex`).
- **한국어**: 기본 `unicode61` 토크나이저는 조사 때문에 검색이 잘 안 된다. 색인 · 질의 전에 한글은 두 글자씩(bigram), 영문은 소문자 단어로 나눠 넣는다.
- **필드 가중치**: decision > tags > 핵심 내용 > summary.
- **임베딩 모델**: 로컬 다국어 모델 **bge-m3**(양자화 ONNX, 약 570MB)를 ONNX Runtime으로 돌린다. 앱 번들에 넣지 않고 첫 실행 때 내려받는다. 검색 평가셋 recall@5로 multilingual-e5-small, paraphrase-multilingual-MiniLM과 비교해 골랐다([결과](../engine/eval/README.md)).
- **결과 없음**: 유사도로는 답이 없는 질의와 있는 질의가 잘 갈리지 않아 기준값을 두지 않는다. 결과는 순위대로 보여 준다.
- **결과 표시**: 어느 필드나 원문 몇 번째 단락이 맞았는지 보여 준다. 원문 텍스트는 없으므로 단락은 번호와 원문 링크로만 안내한다.
- **검색 모델 바꾸기** (설정 화면, `ctx reembed --model`):
  1. 새 모델을 내려받고, 수집 잠금과 같은 방식으로 한 기기만 다시 인덱싱한다.
  2. 글마다 원문을 다시 가져와 단락 벡터를 `vectors/{새 모델}/`에 쓰고 일정 건수마다 커밋 · push한다. 진행률과 실패 목록을 보여 주고, 멈추면 이어서 한다.
  3. 끝나면 `config/search.yaml`을 새 모델로 바꾸고 이전 모델 폴더를 지운다. 다른 기기는 pull에서 변경을 감지해 모델을 받고 `index.db`를 다시 만든다.
  - 원문을 다시 못 가져온 글은 단락 벡터 없이 위키 필드로만 검색되고 "원문 없음"으로 표시한다. 원문이 바뀐 글은 해시로 감지해 "원문 변경됨"으로 표시한다.

## 7. 화면 (데스크톱 앱 메인 창)

출근 후 아침에 펼쳐 보는 "기술 조간 브리프". 시안: [ui/screens/today.png](ui/screens/today.png)

| 화면 | 내용 |
|---|---|
| **Today** | 오늘의 리드 이슈 1건(요약 + Decision Record 카드)과 2~5위 목록. 관련도 × 신선도 × 회사 가중치 순. 좌측에 수집 현황(확인한 소스, 신규, 저장, 보류, 제외, Claude 사용 건수) |
| **Search** | 검색, 필터(결정 사례만, 기간, 회사, 태그), 결과에 하이라이트 스니펫 |
| **Decisions** | 주제별 결정 사례 모음. 같은 문제에 회사별로 무엇을 골랐는지 나란히 비교 |
| **Inbox** | 보류 글을 저장 또는 관련없음으로 분류. 이 클릭이 곧 ⑤의 피드백. 초기 수집에서 온 보류 글(`origin: init`)은 "초기 수집 보류 N건"으로 접어 두고 일괄 처리 버튼을 둔다 |
| **Sources** | 소스별 최근 수집 성공 여부, 신규 글 수, 필터 통과율. Claude 검색 0건 질의와 `suggest_source` 글을 소스 추가 후보로 보여 준다 |
| **설정** | 수집 시각(주말 포함 여부), 수집 완료 알림, 로그인 시 자동 실행, 화면 모드(시스템 · 라이트 · 다크), 연결 상태(GitHub · Claude · Claude Code MCP)와 다시 연결, 검색 모델 내려받기. 기기 로컬 `app-state.json`에 둔다. 검색 모델 바꾸기와 다시 인덱싱은 아직 `ctx reembed`로 한다 (6장) |

주요 동작:
- 원문 열기, 북마크, 관련없음(→ `feedback/`)
- **Claude에 문맥으로 보내기**: Decision Record를 Markdown으로 클립보드에 복사한다. MCP를 거치지 않고 수동으로 문맥을 넣을 때 쓴다.
- 사이드바에 "Claude가 찾아본 것"(MCP 질의 로그)을 보여 준다. 0건 질의는 소스 추가 후보로 표시한다.

구현:
- 엔진이 FastAPI + Jinja2 + HTMX로 HTML을 렌더링하고, Tauri 창이 `http://127.0.0.1:<port>`를 띄운다. 프론트엔드 빌드가 없다.
- 엔진(`ctx serve`)은 `127.0.0.1`의 임의 포트에 바인딩하고, 준비되면 `{"port", "token", "url"}` JSON 한 줄을 stdout에 쓴다. 앱이 이 줄을 읽는다. 앱이 띄울 때는 `--exit-on-stdin-eof`로 앱이 죽으면 함께 끝난다.
- 토큰은 시작할 때 만들어 앱에만 준다. 첫 요청 `/?token=…`에서 HttpOnly · SameSite=Strict 쿠키로 바꾸고 주소에서 지운다. Host 헤더가 서버 주소가 아니면 거절하고(DNS rebinding), 쓰기 요청은 HTMX의 `HX-Request` 헤더도 요구한다. CSP로 외부 스크립트 · 인라인 스크립트를 막는다.
- HTMX와 글꼴(Pretendard, OFL)은 엔진에 넣는다. 외부 요청 없이 오프라인에서도 열린다.
- 서버는 시작할 때 인덱스를 위키에 맞춘다. 검색 모델이 이 기기에 없으면 맞추지 않는다(모델 없이 다시 색인하면 벡터가 지워진다).
- Today 순위 = 관련도 × 신선도(게시일부터 7일마다 절반) × 회사 가중치(`sources.yaml`의 `weight`, 기본 1.0). 1위는 요약과 결정 기록 카드, 2~5위는 목록, 나머지는 접는다. 그날 저장 글이 없으면 최근 7일 상위 3건을 보여 준다.
- Search는 MCP와 같은 검색 경로(키워드 + 의미, RRF)를 쓴다. 회사 · 태그 필터 목록은 종류 · 기간만 거른 결과에서 개수와 함께 만든다. 질의 확장은 하지 않는다(8장: Claude가 한다). 검색 모델이 없으면 키워드로만 찾고 그렇게 알린다.
- Inbox에서 저장을 고르면 글 파일을 `inbox/`에서 `wiki/`로 옮기고 피드백 `save`를, 관련없음이면 피드백 `not_relevant`를 남기고 파일을 지운다(본 글 기록은 남아 다시 모으지 않는다). 인덱스는 그 행만 고쳐 벡터를 그대로 둔다. 위키 변경은 다음 수집이 커밋하고 push한다.
- Sources는 수집 기록(`state/runs`)의 소스별 결과(`result.by_source`: 확인 · 새 글 · 저장 · 실패 이유)를 30일치 읽어 계산한다. 통과율 = 저장 ÷ 새 글. 지금은 읽기 전용이고, 소스 추가 · 수정은 M3에서 연다.
- 상태 알림: 마지막 수집이 이틀 이상 지났을 때, 마지막 수집이 Claude 로그인 · 한도 문제로 멈췄을 때(`result.stopped`) Today와 Sources 위에 띄운다.
- 다크 모드: 기본은 macOS 화면 모드를 따르고(`prefers-color-scheme`), 설정에서 라이트 · 다크로 고정할 수 있다(`<html data-theme>`). 토큰만 바꾸고, 글자와 바탕 조합의 대비 4.5:1을 테스트로 지킨다.
- 결정 사례 주제 패널은 사례가 2건 이상인 주제만 보여 준다. 주제 정규화(16번 커밋) 전에는 대부분 1건이라 비어 있다.
- "Claude에 문맥으로 보내기"(클립보드)와 알림은 Tauri API로 처리한다.
- 디자인: 종이 톤 배경, 제목과 본문 모두 Pretendard(굵고 촘촘한 제목), 붉은 잉크(오늘 · 영상) / 청록(결정 사례) 의미 색상.
- 더 풍부한 상호작용이 필요해지면 그때 화면만 Svelte 등으로 옮긴다. 엔진은 JSON API를 함께 제공한다.

## 8. MCP 검색 (Claude가 직접 검색하는 경우)

사람의 검색과 다른 점:
- 질의가 길고 자연어다.
- 한·영이 섞인다.
- 여러 번 반복해서 좁혀 간다.
- 결과가 그대로 문맥에 들어가므로 토큰 비용과 정확성이 중요하다.

원칙:
- **질의 확장은 호출자(Claude)에게 맡긴다.** Claude가 한·영 동의어와 관련 개념으로 여러 질의를 보내고, 서버는 RRF로 합친다.
- **단계적으로 공개한다.** 검색은 짧은 목록만, 상세 내용은 id로 다시 요청한다.
- **결과가 답인지는 호출자가 판단한다.** 의미 유사도로도, 질의 단어가 글에 든 비율로도 답 없는 질의를 가를 수 없었다([검색 평가](../engine/eval/README.md)). 그래서 검색 응답 끝에 "관련도 순일 뿐 답이라는 보장은 없다. 맞지 않으면 위키에 없다고 답한다"를 붙이고, 판단은 요약을 읽은 Claude가 한다. 결과가 0건이면 있는 결정 주제 목록을 돌려줘 지어내지 않게 한다.

| tool | 입력 | 반환 (기본 토큰 예산) |
|---|---|---|
| `list_topics(prefix?)` | — | 결정 주제와 사례 수 (~500) |
| `search_wiki(queries[], company?, since?, limit=8)` | 질의 1~5개 | id, 제목, 회사, 날짜, 한 줄 요약, decision topic, 점수 (~1.5k) |
| `find_decision_cases(topic \| problem, limit=5)` | 주제 또는 문제 서술 | Record 요약: options, chosen, 핵심 tradeoff, confidence, URL (~2k) |
| `get_decision(id)` | id | Record 전체 + evidence (~1k) |
| `get_article(id, section?)` | id | 요약과 지정 섹션 (~3k) |
| `suggest_source(url, reason)` | URL | 이 기기의 `feedback/` 파일에 수집 요청 기록. 다음 수집에서 처리 |

구현:
- 공식 MCP Python SDK(`mcp` 2.x의 `MCPServer`)로 stdio 서버를 띄운다. 응답은 글자만 보내고 같은 내용의 구조화 출력은 끈다(토큰 절약).
- 서버는 바로 뜨고 검색 모델은 첫 검색 때 불러온다(약 2초, 이후 0.1초 안팎). 모델을 아직 받지 않은 기기에서는 키워드로만 찾고 그렇게 알린다.
- tool은 작업 스레드에서 불리므로 한 번에 하나씩 처리한다. 엔진이 인덱스를 다시 만들면 다음 검색부터 새 인덱스를 본다.
- `suggest_source`는 피드백 파일에 쓰기만 하고 git은 건드리지 않는다. 다음 `ctx sync`가 함께 올린다.
- `find_decision_cases`는 topic과 problem을 한 질의로 검색한다. 따로 검색해 합치면 짧은 topic의 낱말만 맞는 글이 정답을 밀어냈다.
- 결정 주제는 주제 정규화(16번 커밋) 전까지 대소문자 · 공백만 맞춰 묶는다.

호출 유도:
- tool description에 "기술 선택, 아키텍처 결정, 트레이드오프 비교 전에 호출"을 명시한다.
- 서버 안내문(instructions)에 다음 순서를 둔다: `find_decision_cases` → 부족하면 `search_wiki`를 한·영으로 → 웹 검색 → 좋은 글은 `suggest_source`. 평가에서 이것만으로 Claude가 결정 질문마다 위키를 먼저 찾았다. 전역 CLAUDE.md · hook은 두지 않는다.
- 응답의 모든 사례에 출처 URL을 넣는다.

품질 관리:
- MCP 질의 로그를 `~/.context-holder/mcp-queries.jsonl`에 남기고 화면에 보여 준다.
- 평가셋: 기술 결정 질문 23개(실제 고민 3개, 답 없음 5개 포함) × 기대 사례로 recall@5를 잰다(`ctx eval-mcp`). Claude가 실제로 보낸 호출도 그대로 다시 넣어 잰다. 답 없는 질문은 Claude에 실제로 물어 지어내지 않는지 사람이 확인한다 ([검색 평가](../engine/eval/README.md)).

## 9. 정리 (⑤)

- **Feedback**: 화면의 관련없음, 북마크, Inbox 분류 → `feedback/` → 다음 수집 때 Classifier few-shot 예시와 deny 키워드에 반영.
- **Pruner** (주 1회, `ctx sync`가 월요일에 함께 실행): 현재 profile과 feedback으로 기존 글을 재평가해 `archive/`로 이동한다. 삭제하지 않으며 git 이력으로 복구할 수 있다.

## 10. 소스별 메모

| 소스 | 수집 방식 | 확인 결과 |
|---|---|---|
| NAVER D2 | `https://d2.naver.com/d2.atom` | 최근 20개 글 본문 HTML 포함. 페이지는 SPA 껍데기라 크롤링 불필요. 단건은 `/api/v1/contents/{id}` (작성자 · 부서 포함). 영상 글 6/20, 영상은 네이버 클립으로 연결 |
| 그 외 | README 참고 | 32곳 feed 확인, 5.1 기준으로 13곳 선정 (국내 9, 해외 4). 최근 90일 수집 대상 글 약 90건 |

## 11. 기술 스택

- **앱 셸**: Tauri 2 (Rust), 플러그인: tray, autostart, notification, shell(sidecar), clipboard
- **엔진**: Python 3.12 + uv
  - 사용자 배포: PyInstaller로 묶어 앱 번들에 포함한다 (사용자는 Python 불필요).
  - 개발 중: `uv`로 실행한다.
- feedparser, trafilatura, httpx, APScheduler
- FastAPI, Jinja2, HTMX
- MCP Python SDK
- SQLite FTS5, WAL 모드
- 다국어 임베딩: ONNX Runtime + 로컬 모델(첫 실행 때 다운로드)
- 내장 git: pygit2 (libgit2)
- Claude Code CLI (`claude -p`). 없으면 앱이 공식 설치 프로그램으로 설치
- GitHub OAuth App (Device Flow), macOS 키체인
- M4: whisper.cpp, Playwright, ffmpeg
- 배포: 직접 빌드(`scripts/build-app.sh`, ad-hoc 서명)는 격리 표시가 없어 바로 열린다. 다른 Mac에 배포하는 빌드는 Developer ID 서명과 공증을 거친다(릴리스 워크플로우). ad-hoc 서명 앱은 내려받으면 Gatekeeper가 거절한다. Apple Silicon 전용.

## 12. 로드맵

| 단계 | 범위 | 완료 기준 |
|---|---|---|
| **M0 검증** | 평가셋 50개 수동 라벨링, `claude -p` 분류 정확도와 JSON 출력 안정성 확인 | 소스별 수집 방식 확정, 분류 정확도 기준선 확보 |
| **M1 엔진 · 문맥** | 엔진(수집 · 분류 · 요약 · 혼합 검색 인덱싱 · Git 동기화), `ctx` CLI, **MCP 서버**, MCP 질의 로그 | `ctx sync`로 매일 위키가 쌓이고 관련 글 precision 80% 이상. 평가셋 recall@5 70% 이상<br>**완료 (2026-11-09)**: 실제 위키에 매일 수집 1회(잠금 · push), 저장 정밀도 100%(M0), 결정 질문 recall@5 18/18 · Claude 실제 호출 6/6, 답 없는 질문 5/5에서 Claude가 사례를 지어내지 않음 ([평가](../engine/eval/README.md)) |
| **M2 앱** | 화면(Today · Search · Inbox), **Tauri 앱(메뉴바 · 자동 실행 · 알림 · 스케줄)**, **첫 실행 설정(GitHub · Claude 연결)**, 패키징, Decisions 화면 · 결정 주제 정규화 | 새 Mac에서 설치 후 일주일간 손대지 않아도 매일 아침 수집 완료 알림이 오고, 실제 기술 결정에서 사례 3건 이상 인용 |
| **M3 정리** | 피드백의 Classifier 반영, Pruner, Sources 화면 | 관련없음 피드백이 다음 수집에 반영됨 |
| **M4 확장** | 영상 보강(Media Worker) | 필요성이 확인된 것만 진행 |

## 13. 리스크

| 리스크 | 대응 |
|---|---|
| 여러 기기가 동시에 수집 · 커밋 | push를 잠금으로 쓰는 하루 1회 수집, 글 파일 1회 쓰기, 기기별 피드백 파일 (3.1) |
| 엔진 프로세스가 죽거나 포트 충돌 | 앱이 sidecar를 감시해 재시작하고, 메뉴바를 오류 상태로 표시. 포트는 임의 할당. 스파이크로 resources의 onedir 엔진 실행 · 재시작 · 앱 종료 시 정리 확인 ([spikes/tauri-sidecar](../spikes/tauri-sidecar/README.md)) |
| 엔진 번들 크기와 패키징 복잡도 | 엔진(PyInstaller onedir, 약 26MB)과 git(pygit2)을 앱에 포함. 스파이크로 HTTPS clone · push 동작 확인 ([spikes/sidecar](../spikes/sidecar/README.md)). Whisper 등 M4 도구는 필요할 때 설치 |
| Claude Code 설치 · 로그인 실패 | 설정 화면에서 상태 확인 · 다시 연결. 연결 전 수집 글은 pending으로 보관 |
| Claude 의존 (한도 · 장애 · 약관) | 하루 호출 수가 작음. 실패 시 pending 후 재처리. 자동화 사용 범위는 현재 약관 확인 |
| Decision Record 환각 | evidence 코드 검증, confidence 표시, 결정 없으면 null |
| 관련도 경계값이 평가셋 50건에 맞춰짐 | M0에서 0.3 / 0.6으로 보정(저장 정밀도 100%, 놓친 글 0). 수집이 쌓이면 평가셋을 늘려 다시 보정하고 inbox 비율 관찰 |
| 신규 글이 적은 날 브리프가 빈약 | 화면에 "오늘 신규 없음"과 최근 7일 상위 글 표시 |
| 네이버 클립 오디오 추출 불가 또는 깨짐 | 출처별 플러그인으로 분리하고 실패 시 `summary_only` 유지 |
| 회사 PC 사용 | 개인 레포 clone 가능 여부를 보안 정책으로 확인 |

## 14. 저작권 · 보안

- 개인용이다. 위키 레포는 private으로 두고 공개하지 않는다.
- 전문 대신 요약, 핵심 내용, Decision Record, 원문 링크를 저장한다.
- 원문 단락은 검색용 벡터만 저장하고 텍스트는 저장하지 않는다.
- 엔진 HTTP는 `127.0.0.1`에만 바인딩하고, 앱에만 전달한 토큰으로 요청을 확인한다.
- API 키와 비밀값을 쓰지 않는다. `claude`는 기존 로그인 세션을 사용한다.
