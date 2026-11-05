# context-holder 계획

빅테크 기술블로그를 매일 수집해 개인 위키로 관리하고, 기술 결정 시 빅테크 사례를 에이전트 문맥으로 주입하는 개인용 도구.

- 개요: [architecture/overview.png](architecture/overview.png)
- 상세 아키텍처: [architecture/architecture.png](architecture/architecture.png)
- 화면 시안: [ui/mockup-today.png](ui/mockup-today.png)

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
- 브리프 열기 (⌘⇧B), 검색… (⌘⇧K)
- 지금 수집, 일시정지
- 종료

창을 닫아도 앱은 메뉴바에 남아 수집을 계속한다.

선택 이유:
- 매일 자동 수집하는 상주 작업이 있고, 상태를 계속 보여 줘야 한다. 브라우저 탭보다 메뉴바 앱이 가시성이 좋다.
- **Tauri 2**는 macOS 내장 WebKit을 써서 앱 자체 메모리가 수십 MB 수준이다. tray, autostart, notification, sidecar가 공식 플러그인으로 있다. Electron은 로컬 모델과 함께 상주시키기에 무겁다.
- 화면은 엔진이 렌더링한 HTML을 Tauri 창이 띄운다. 프론트엔드 빌드가 없다.
- 대안: Mac 전용으로 더 가볍게 가려면 SwiftUI 메뉴바 앱 + WKWebView. 같은 엔진을 그대로 쓸 수 있다.

## 3. 실행 환경

- **실행 시점**: 앱이 로그인 시 자동 실행되고 엔진 스케줄러가 판단한다.
  - 앱 시작, 잠자기 해제, 평일 09:30 중 하나가 발생했을 때 "오늘 미수집"이면 수집한다.
  - 완료되면 macOS 알림을 띄운다(신규 N건, 저장 M건). 알림을 누르면 브리프 창이 열린다.
  - SessionStart hook은 수집을 실행하지 않는다. 엔진에 오늘의 브리프를 물어 출력만 한다. 앱이 꺼져 있으면 index.db를 직접 읽는다.
- **Mac이 꺼져 있던 날**: 다음 실행 때 "마지막으로 본 글 이후"부터 따라잡는다. feed가 최근 글을 보존하므로 누락은 거의 없다.
- **동시 접근**: index.db는 SQLite WAL 모드다. 엔진이 쓰는 동안에도 `ctx mcp`가 읽을 수 있다. 쓰기는 엔진만 한다.
- **위키 레포**: 로컬 Git 레포다. GitHub private 레포로 push해서 백업하고 다른 Mac과 동기화한다. 쓰는 주체가 로컬 하나뿐이라 충돌이 없다.

### 레포 구조 (`context-wiki`)

```
wiki/{company}/{yyyy}/{slug}.md   # 요약 + Decision Record + 원문 링크 (전문 미저장)
decisions/{topic}.yaml            # 주제별 결정 사례 모음
inbox/                            # 관련도 애매(0.4~0.7) 보류 글
archive/                          # Pruner 정리분
transcripts/{media_id}.txt        # 영상 대본 (M4)
config/sources.yaml               # 소스 목록 · 수집 방식 · 회사 가중치
config/profile.yaml               # 관심/제외 기술, 한↔영 용어 사전
feedback/*.jsonl                  # 관련없음 · 북마크 · suggest_source
```

로컬 전용 (커밋 안 함): `~/.context-holder/index.db`, `~/.context-holder/logs/`, `~/.context-holder/mcp-queries.jsonl`

## 4. LLM Gateway

LLM 호출은 한 인터페이스 뒤에 두고, 작업별로 백엔드를 설정으로 고른다.

| 작업 | 기본 백엔드 | 이유 |
|---|---|---|
| 관련도 분류 (하루 10~30건) | 로컬 Ollama `qwen3:8b` (Q4, 약 5GB) | 쉬운 작업이고 양이 많음 |
| 요약 + Decision Record (하루 2~3건) | Claude 구독 `claude -p --model sonnet --output-format json` | 핵심 가치(④)라 품질 우선. 사용량이 작아 구독 한도에 영향 적음 |
| 주간 Pruner 재분류 | 로컬 모델 | 일괄 작업 |
| 영상 보강 (M4) | Claude 구독 | 대본이 길고 품질 중요 |

- Claude 호출이 실패하거나 한도에 걸리면 로컬 모델로 대체하고 `enriched_by: local`로 표시한다. 나중에 `ctx enrich <id>`로 다시 처리할 수 있다.
- 로컬 모델은 작업할 때만 올리고 끝나면 내린다(`keep_alive: 0`). 평소 개발 도구와 메모리를 나눠 쓰기 위해서다.
- 모델 크기는 노트북에서 백그라운드로 돌릴 수 있는 수준(8B 안팎)으로 제한한다.
- API 키는 사용하지 않는다.

## 5. 파이프라인 (`ctx sync`)

1. **Fetcher**: `sources.yaml`을 순회한다. RSS/Atom을 먼저 쓰고 없으면 HTML. ETag와 "마지막으로 본 글"로 증분 수집한다. robots.txt와 rate limit을 지킨다.
2. **Extract · Dedup**: Readability → Markdown 변환. canonical URL + SHA-256으로 중복 제거. 영상 글을 감지한다.
3. **Relevance Classifier**: 1차 `profile.yaml` 키워드 allow/deny, 2차 로컬 모델(feedback을 few-shot 예시로 사용).
   - ≥ 0.7: 저장
   - 0.4 ~ 0.7: `inbox/`
   - < 0.4: Drop (로그만)
4. **Enricher**: 한국어 3줄 요약, 영문 정규화 태그, Decision Record를 추출하고 evidence를 검증한다.
5. **Commit · Index**: 위키에 쓰고 git commit, push(백업)한 뒤 증분 인덱싱한다.

목표 처리 시간: 하루 분량 10분 이내.

### 5.1 Decision Record

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
enriched_by: claude | local
```

- **evidence는 코드로 검증한다.** 인용 문장이 원문에 실제로 있는지 정규화한 문자열로 대조한다. 없으면 `confidence: low`로 내리고 화면에 표시한다.
- 결정 내용이 없는 글은 `decision: null`로 둔다.

### 5.2 영상 글

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
- **임베딩**: M2 평가에서 recall이 부족할 때만 로컬 다국어 임베딩(bge-m3 등)을 sqlite-vec로 추가한다.

## 7. 화면 (데스크톱 앱 메인 창)

출근 후 아침에 펼쳐 보는 "기술 조간 브리프". 시안: [ui/mockup-today.png](ui/mockup-today.png)

| 화면 | 내용 |
|---|---|
| **Today** | 오늘의 리드 이슈 1건(요약 + Decision Record 카드)과 2~5위 목록. 관련도 × 신선도 × 회사 가중치 순. 좌측에 수집 현황(확인한 소스, 신규, 저장, 보류, 제외, Claude 사용 건수) |
| **Search** | ⌘K 검색, 필터(결정 사례만, 기간, 회사, 태그), 결과에 하이라이트 스니펫 |
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
- 디자인: 종이 톤 배경, 명조 헤드라인 + Pretendard 본문, 붉은 잉크(오늘 · 영상) / 청록(결정 사례) 의미 색상.
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
| `suggest_source(url, reason)` | URL | `feedback/`에 수집 요청 기록. 다음 `ctx sync`에서 처리 |

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
| 그 외 | 미확인 | M0에서 소스별 feed 유무, 전문 포함 여부 확인 |

## 11. 기술 스택

- **앱 셸**: Tauri 2 (Rust), 플러그인: tray, autostart, notification, shell(sidecar), clipboard
- **엔진**: Python 3.12 + uv
  - 처음에는 `uv tool install`로 설치한 `ctx`를 앱이 실행한다.
  - 안정화되면 PyInstaller로 묶어 앱 번들에 포함한다.
- feedparser, trafilatura, httpx, APScheduler
- FastAPI, Jinja2, HTMX
- MCP Python SDK
- SQLite FTS5, WAL 모드 (+ sqlite-vec는 선택)
- Ollama (`qwen3:8b`), Claude Code CLI (`claude -p`)
- Claude Code SessionStart hook
- M4: whisper.cpp, Playwright, ffmpeg
- 배포: 개인용 무서명 빌드. 처음 한 번 우클릭 → 열기.

## 12. 로드맵

| 단계 | 범위 | 완료 기준 |
|---|---|---|
| **M0 검증** | 소스 10개 feed 확인, 평가셋 50개 수동 라벨링, `qwen3:8b` 분류 정확도와 `claude -p` JSON 출력 확인 | 소스별 수집 방식 확정, 분류 정확도 기준선 확보 |
| **M1 브리프** | 엔진(스케줄러 · 수집 · 분류 · 요약 · 커밋 · 인덱싱), **Tauri 앱(메뉴바 · 자동 실행 · 알림 · Today / Search / Inbox 창)**, SessionStart hook | 일주일간 손대지 않아도 매일 아침 수집 완료 알림이 오고, 관련 글 precision 80% 이상 |
| **M2 문맥** | MCP 서버, Decisions 화면, 결정 주제 정규화, MCP 질의 로그 | 평가셋 recall@5 70% 이상, 실제 기술 결정에서 사례 3건 이상 인용 |
| **M3 정리** | 피드백의 Classifier 반영, Pruner, Sources 화면 | 관련없음 피드백이 다음 수집에 반영됨 |
| **M4 확장** | 영상 보강(Media Worker), 로컬 임베딩 | 필요성이 확인된 것만 진행 |

## 13. 리스크

| 리스크 | 대응 |
|---|---|
| 로컬 8B 모델의 분류 품질 부족 | M0 평가셋으로 측정. 부족하면 키워드 규칙을 강화하거나 분류도 `claude -p`로 전환 |
| 로컬 리소스(메모리 · 디스크) 압박 | 모델은 작업 때만 로드, 8B 안팎으로 제한, 처리는 백그라운드. 앱 셸은 Tauri로 가볍게. Whisper(M4)는 필요할 때 설치 |
| 엔진 프로세스가 죽거나 포트 충돌 | 앱이 sidecar를 감시해 재시작하고, 메뉴바를 오류 상태로 표시. 포트는 임의 할당 |
| Python 엔진 패키징 복잡도 | M1은 `uv tool install`한 `ctx`를 실행. 번들 포함(PyInstaller)은 안정화 후 |
| `claude -p` 구독 한도 · 약관 | 하루 2~3건으로 제한. 실패 시 로컬로 대체. 자동화 사용 범위는 현재 약관 확인 |
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
