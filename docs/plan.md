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
| ③ | 검색 + 당일 주요 이슈를 한눈에 | 데스크톱 앱(메뉴바 + Today / Search 창), SessionStart hook |
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
| SessionStart hook | Claude Code 세션 시작 시 오늘의 브리프 출력 |

메뉴바 메뉴:
- 상태 줄: "오늘 09:14 수집 완료 · 신규 5건" / "수집 중 3/14" / "오류: 소스 2개 실패"
- 브리프 열기, 검색
- 지금 수집, 일시정지
- 종료

창을 닫아도 앱은 메뉴바에 남아 수집을 계속한다.

선택 이유:
- 매일 자동 수집하는 상주 작업이 있고, 상태를 계속 보여 줘야 한다. 브라우저 탭보다 메뉴바 앱이 가시성이 좋다.
- **Tauri 2**는 macOS 내장 WebKit을 써서 앱 자체 메모리가 수십 MB 수준이다. tray, autostart, notification, sidecar가 공식 플러그인으로 있다. Electron은 상주 앱으로 쓰기에 무겁다.
- 화면은 엔진이 렌더링한 HTML을 Tauri 창이 띄운다. 프론트엔드 빌드가 없다.
- 대안: Mac 전용으로 더 가볍게 가려면 SwiftUI 메뉴바 앱 + WKWebView. 같은 엔진을 그대로 쓸 수 있다.

## 3. 실행 환경

- **실행 시점**: 앱이 로그인 시 자동 실행되고 엔진 스케줄러가 판단한다.
  - 앱 시작, 잠자기 해제, 평일 09:30 중 하나가 발생했을 때 "오늘 미수집"이면 수집한다.
  - 완료되면 macOS 알림을 띄운다(신규 N건, 저장 M건). 알림을 누르면 브리프 창이 열린다.
  - SessionStart hook은 수집을 실행하지 않는다. 엔진에 오늘의 브리프를 물어 출력만 한다. 앱이 꺼져 있으면 index.db를 직접 읽는다.
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
3. 없으면 `{host, started_at, status: running}`으로 파일을 만들어 커밋하고 **즉시 push**한다.
   - push 성공: 이 기기가 오늘 수집을 맡는다.
   - push 거절(다른 기기가 먼저 push): pull해서 상대의 실행 기록을 확인하고 수집하지 않는다.
4. 수집이 끝나면 `status: done`과 결과 수치를 기록해 커밋 · push한다.
5. `running` 상태로 2시간이 지나면(기기가 중간에 꺼진 경우) 다른 기기가 이어받는다.

GitHub의 브랜치 갱신은 원자적이라, 두 기기가 동시에 시도해도 push는 한 쪽만 성공한다. 별도 잠금 서버가 필요 없다. 주간 Pruner도 `state/runs/prune-{yyyy-ww}.json`으로 같은 방식을 쓴다.

**충돌을 구조적으로 피하기**

- **글 파일은 한 번만 쓴다.** 경로는 canonical URL에서 결정적으로 만든다. 이미 있는 글은 수집에서 건너뛴다. 다시 쓰는 경우는 `ctx enrich` · Pruner뿐이다.
- **피드백은 기기별 파일에 이어 쓴다.** `feedback/{yyyy-mm}-{host}.jsonl`. 두 기기가 같은 파일을 고치지 않는다.
- **수집 이력도 월별 파일에 이어 쓴다.** `state/seen/{yyyy-mm}.jsonl` (URL 해시, 판정, 점수). 수집을 맡은 기기만 쓴다.
- push가 거절되면 `pull --rebase` 후 다시 push한다(최대 3회). 그래도 충돌이 나면 원격 내용을 우선하고, 로컬 변경은 다음 실행에서 다시 만든다.

**동기화 시점과 인덱스 갱신**

- 앱 시작, 잠자기 해제, 앱이 켜져 있는 동안 30분마다 pull한다.
- pull 전후 커밋을 비교해(`git diff --name-only`) 바뀐 파일만 다시 인덱싱한다.
- 오프라인이면 로컬에 커밋해 두고 연결되면 push한다.

**새 기기 추가**: 3.2의 초기 설정과 같다. GitHub 로그인 후 기존 `context-wiki`를 찾으면 clone하고 전체 인덱싱한다.

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
4. Claude Code 연동    MCP 서버 등록 + SessionStart hook 추가
                       ~/.claude 설정은 덮어쓰지 않고 병합, 변경 내용을 보여 주고 백업 후 적용
5. 완료                바로 첫 수집 시작
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

### 레포 구조 (`context-wiki`)

```
wiki/{company}/{yyyy}/{slug}.md   # 요약 + Decision Record + 원문 링크 (전문 미저장)
decisions/{topic}.yaml            # 주제별 결정 사례 모음
inbox/                            # 관련도 애매(0.4~0.7) 보류 글
archive/                          # Pruner 정리분
transcripts/{media_id}.txt        # 영상 대본 (M4)
config/sources.yaml               # 소스 목록 · 수집 방식 · 회사 가중치
config/profile.yaml               # 관심/제외 기술, 한↔영 용어 사전
feedback/{yyyy-mm}-{host}.jsonl   # 관련없음 · 북마크 · suggest_source (기기별 파일)
state/runs/{date}.json            # 그날 수집을 맡은 기기 · 상태 · 결과 (수집 잠금)
state/seen/{yyyy-mm}.jsonl        # 본 글의 URL 해시 · 판정 · 점수 (중복 수집 방지)
state/sources.json                # 소스별 마지막으로 본 글
```

기기 로컬 (커밋 안 함): `~/.context-holder/index.db`, `~/.context-holder/logs/`, `~/.context-holder/etag-cache.json`, `~/.context-holder/mcp-queries.jsonl`

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
   - ≥ 0.7: 저장
   - 0.4 ~ 0.7: `inbox/`
   - < 0.4: Drop (로그만)
4. **Enricher**: 한국어 3줄 요약, 영문 정규화 태그, Decision Record를 추출하고 evidence를 검증한다.
5. **Commit · Push · Index**: 위키와 `state/`에 쓰고 커밋 · push한 뒤 증분 인덱싱한다. push 거절 시 rebase 후 재시도.

목표 처리 시간: 하루 분량 10분 이내.

### 5.1 관련도 기준

AI · ML은 통째로 거르지 않는다. **AI를 엔지니어링에 쓰는 글은 수집하고, 모델 자체를 만드는 글은 거른다.**

| 판정 | 주제 | 예 (2026년 실제 글 제목) |
|---|---|---|
| 수집 | 백엔드 · 인프라 · 데이터 | 동시성, DB, 메시징, 배포, 장애 분석, 아키텍처 |
| 수집 | 프론트엔드 | 웹 성능, 렌더링, 패키지 관리, 웹뷰 |
| 수집 | **AI 엔지니어링** | LLM 비용 최적화, 서빙 인프라, 에이전트 · MCP · 하네스, 컨텍스트 설계, AI로 테스트 · 개발 자동화, LLM 평가 체계 |
| 거름 | ML 모델링 | 임베딩 학습, 추천 · 랭킹 모델 구조, 비전 · 음성 모델 개발, 논문 구현 |
| 거름 | 그 외 | 디자인, 행사 홍보, 채용, 조직문화 |

- `profile.yaml`의 `include` / `exclude`는 키워드가 아니라 위 **주제 단위**로 적는다. 키워드 규칙은 1차 후보 추리기에만 쓴다. "AI"라는 단어만으로 거르면 에이전트 · 하네스 글까지 빠진다.
- 경계에 있는 글(ML 플랫폼, 벡터 DB 운영 등)은 inbox로 보내 피드백으로 기준을 다듬는다.

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

## 6. 인덱싱

- `index.db`는 SQLite FTS5다. 위키에서 언제든 다시 만들 수 있다(`ctx reindex`).
- **한국어**: 기본 `unicode61` 토크나이저는 조사 때문에 검색이 잘 안 된다. `trigram` 토크나이저나 kiwipiepy 형태소 분석을 쓴다.
- **한·영 교차 검색**: 한국어 요약과 영문 정규화 태그를 함께 저장하고, `profile.yaml` 용어 사전으로 동의어를 보강한다.
- **필드 가중치**: decision > tags > summary.
- **임베딩**: M1 MCP 평가에서 recall이 부족할 때만 로컬 다국어 임베딩(bge-m3 등)을 sqlite-vec로 추가한다.

## 7. 화면 (데스크톱 앱 메인 창)

출근 후 아침에 펼쳐 보는 "기술 조간 브리프". 시안: [ui/screens/today.png](ui/screens/today.png)

| 화면 | 내용 |
|---|---|
| **Today** | 오늘의 리드 이슈 1건(요약 + Decision Record 카드)과 2~5위 목록. 관련도 × 신선도 × 회사 가중치 순. 좌측에 수집 현황(확인한 소스, 신규, 저장, 보류, 제외, Claude 사용 건수) |
| **Search** | 검색, 필터(결정 사례만, 기간, 회사, 태그), 결과에 하이라이트 스니펫 |
| **Decisions** | 주제별 결정 사례 모음. 같은 문제에 회사별로 무엇을 골랐는지 나란히 비교 |
| **Inbox** | 보류 글을 저장 또는 관련없음으로 분류. 이 클릭이 곧 ⑤의 피드백 |
| **Sources** | 소스별 최근 수집 성공 여부, 신규 글 수, 필터 통과율 |

주요 동작:
- 원문 열기, 북마크, 관련없음(→ `feedback/`)
- **Claude에 문맥으로 보내기**: Decision Record를 Markdown으로 클립보드에 복사한다. MCP를 거치지 않고 수동으로 문맥을 넣을 때 쓴다.
- 사이드바에 "Claude가 찾아본 것"(MCP 질의 로그)을 보여 준다. 0건 질의는 소스 추가 후보로 표시한다.

구현:
- 엔진이 FastAPI + Jinja2 + HTMX로 HTML을 렌더링하고, Tauri 창이 `http://127.0.0.1:<port>`를 띄운다. 프론트엔드 빌드가 없다.
- 엔진은 `127.0.0.1`의 임의 포트에 바인딩한다. 시작할 때 생성한 토큰을 앱에만 전달해서 다른 로컬 프로세스의 요청을 막는다.
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
- **결과가 없으면 없다고 명시한다.** 빈 결과일 때는 비슷한 토픽 목록을 돌려줘 지어내지 않게 한다.

| tool | 입력 | 반환 (기본 토큰 예산) |
|---|---|---|
| `list_topics(prefix?)` | — | 결정 주제와 사례 수 (~500) |
| `search_wiki(queries[], company?, since?, limit=8)` | 질의 1~5개 | id, 제목, 회사, 날짜, 한 줄 요약, decision topic, 점수 (~1.5k) |
| `find_decision_cases(topic \| problem, limit=5)` | 주제 또는 문제 서술 | Record 요약: options, chosen, 핵심 tradeoff, confidence, URL (~2k) |
| `get_decision(id)` | id | Record 전체 + evidence (~1k) |
| `get_article(id, section?)` | id | 요약과 지정 섹션 (~3k) |
| `suggest_source(url, reason)` | URL | 이 기기의 `feedback/` 파일에 수집 요청 기록. 다음 수집에서 처리 |

호출 유도:
- tool description에 "기술 선택, 아키텍처 결정, 트레이드오프 비교 전에 호출"을 명시한다.
- 전역 CLAUDE.md 또는 skill에 다음 순서를 둔다: `find_decision_cases` → 부족하면 웹 검색 → 좋은 글은 `suggest_source`.
- 응답의 모든 사례에 출처 URL을 넣는다.

품질 관리:
- MCP 질의 로그를 `~/.context-holder/mcp-queries.jsonl`에 남기고 화면에 보여 준다.
- 평가셋: 실제 기술 결정 질문 20개 × 기대 사례로 recall@5를 측정한다.

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
- SQLite FTS5, WAL 모드 (+ sqlite-vec는 선택)
- 내장 git: pygit2 (libgit2)
- Claude Code CLI (`claude -p`). 없으면 앱이 공식 설치 프로그램으로 설치
- GitHub OAuth App (Device Flow), macOS 키체인
- Claude Code SessionStart hook
- M4: whisper.cpp, Playwright, ffmpeg
- 배포: 개인용 무서명 빌드. 처음 한 번 우클릭 → 열기.

## 12. 로드맵

| 단계 | 범위 | 완료 기준 |
|---|---|---|
| **M0 검증** | 평가셋 50개 수동 라벨링, `claude -p` 분류 정확도와 JSON 출력 안정성 확인 | 소스별 수집 방식 확정, 분류 정확도 기준선 확보 |
| **M1 엔진 · 문맥** | 엔진(수집 · 분류 · 요약 · 인덱싱 · Git 동기화), `ctx` CLI, **MCP 서버**, SessionStart hook, MCP 질의 로그 | `ctx sync`로 매일 위키가 쌓이고 관련 글 precision 80% 이상. 평가셋 recall@5 70% 이상 |
| **M2 앱** | 화면(Today · Search · Inbox), **Tauri 앱(메뉴바 · 자동 실행 · 알림 · 스케줄)**, **첫 실행 설정(GitHub · Claude 연결)**, 패키징, Decisions 화면 · 결정 주제 정규화 | 새 Mac에서 설치 후 일주일간 손대지 않아도 매일 아침 수집 완료 알림이 오고, 실제 기술 결정에서 사례 3건 이상 인용 |
| **M3 정리** | 피드백의 Classifier 반영, Pruner, Sources 화면 | 관련없음 피드백이 다음 수집에 반영됨 |
| **M4 확장** | 영상 보강(Media Worker), 로컬 임베딩 | 필요성이 확인된 것만 진행 |

## 13. 리스크

| 리스크 | 대응 |
|---|---|
| 여러 기기가 동시에 수집 · 커밋 | push를 잠금으로 쓰는 하루 1회 수집, 글 파일 1회 쓰기, 기기별 피드백 파일 (3.1) |
| 엔진 프로세스가 죽거나 포트 충돌 | 앱이 sidecar를 감시해 재시작하고, 메뉴바를 오류 상태로 표시. 포트는 임의 할당 |
| 엔진 번들 크기와 패키징 복잡도 | 엔진(PyInstaller)과 git(pygit2)을 앱에 포함. Whisper 등 M4 도구는 필요할 때 설치 |
| Claude Code 설치 · 로그인 실패 | 설정 화면에서 상태 확인 · 다시 연결. 연결 전 수집 글은 pending으로 보관 |
| Claude 의존 (한도 · 장애 · 약관) | 하루 호출 수가 작음. 실패 시 pending 후 재처리. 자동화 사용 범위는 현재 약관 확인 |
| Decision Record 환각 | evidence 코드 검증, confidence 표시, 결정 없으면 null |
| 관련도 임계값(0.7) 근거 없음 | M0 평가셋으로 보정하고 inbox 비율 관찰 |
| 신규 글이 적은 날 브리프가 빈약 | 화면에 "오늘 신규 없음"과 최근 7일 상위 글 표시 |
| 네이버 클립 오디오 추출 불가 또는 깨짐 | 출처별 플러그인으로 분리하고 실패 시 `summary_only` 유지 |
| 회사 PC 사용 | 개인 레포 clone 가능 여부를 보안 정책으로 확인 |

## 14. 저작권 · 보안

- 개인용이다. 위키 레포는 private으로 두고 공개하지 않는다.
- 전문 대신 요약, Decision Record, 원문 링크를 저장한다.
- 엔진 HTTP는 `127.0.0.1`에만 바인딩하고, 앱에만 전달한 토큰으로 요청을 확인한다.
- API 키와 비밀값을 쓰지 않는다. `claude`는 기존 로그인 세션을 사용한다.
