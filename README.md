# context-holder

빅테크 기술블로그의 기술 결정 사례를 모아, 나와 AI 코딩 에이전트의 문맥으로 쓰는 개인용 도구.

![context-holder 개요](docs/architecture/overview.png)

## 무엇을 하나

- **수집 · 정리 워커**: 백그라운드에서 매일 빅테크 기술블로그를 수집한다. 업무와 무관한 기술은 걸러 내고, 요약과 기술 결정 기록(문제 · 선택지 · 결정 · 트레이드오프)을 만들어 위키에 저장한다. 요약과 분류는 설정에서 고른 [에이전트](#에이전트)가 구독 로그인으로 한다(API 키 불필요).
- **화면**: 메뉴바에 상주하는 데스크톱 앱이다. 오늘의 브리프, 검색, 회사별 결정 사례 비교를 보여 주고, 관련없는 글은 피드백으로 정리한다.
- **위키 검색**: 터미널의 에이전트가 기술 결정 전에 MCP로 위키를 검색해 빅테크 사례를 출처와 함께 문맥으로 가져온다.

## 시작하기

준비물:

- macOS
- GitHub 계정
- [지원 에이전트](#에이전트) 중 하나의 구독

git과 Python은 앱에 들어 있어 따로 설치할 필요가 없다.

[GitHub Releases](https://github.com/yogurt-c/context-holder/releases)에서 최신 `.dmg`를 내려받아 설치한다.

앱을 처음 실행하면 **시작하기** 탭이 열리고 5단계를 차례로 안내한다.

1. **GitHub 로그인** — 브라우저에서 승인하면 위키 레포(`context-wiki`, private)를 찾거나 새로 만든다.
2. **에이전트 연결** — 요약 · 분류에 쓸 에이전트를 고른다. 명령줄 도구가 없으면 공식 설치 프로그램으로 설치하고, 브라우저에서 로그인을 승인한다. 위키 검색(MCP) 연결은 선택이다.
3. **수집 시작** — 수집 시각을 고르고 완료를 누르면 첫 수집이 시작된다.
4. **첫 수집 완료** — 수집이 끝나면 Today·Search·Inbox를 쓸 수 있다.
5. **주제 정리** — Decisions에서 버튼을 누르면 고른 에이전트가 결정 사례를 넓은 주제로 묶는다.

다 끝나면 시작하기 탭은 사라진다. 다른 기기에서도 같은 순서로 설치하면 같은 위키를 쓴다.

## 에이전트


| 에이전트   | 필요한 구독          | 명령줄 도구      |
| ------ | --------------- | ----------- |
| Claude | Pro / Max       | Claude Code |
| Codex  | ChatGPT Plus 이상 | Codex CLI   |


- **요약 · 분류 에이전트**: 설정에서 하나를 고른다. 매일 수집과 주제 정리에 쓴다. 터미널의 사용자 설정(모델 · MCP · hook)은 적용하지 않는다.
- **계정**: 에이전트마다 시스템 기본값(터미널 로그인)과 "계정 추가"로 만든 계정 중 하나를 쓴다.
- **위키 검색(MCP)**: 에이전트마다 따로 연결한다. 표의 등록 위치(터미널 쪽 설정)에 등록하며, 고른 계정과는 상관없다.

## 수집 대상 블로그

기준: **백엔드 · 프론트엔드 · AI 엔지니어링 글이 꾸준히 나오는 곳.** AI는 엔지니어링에 쓰는 글(LLM 비용 · 서빙, 에이전트 · MCP · 하네스, 컨텍스트 설계, AI 개발 자동화, 검색 · 추천의 측정과 서빙)만 수집하고, 모델 자체를 만드는 글(모델 아키텍처, 비전 · 음성 모델 개발 등)은 거른다. 상세 기준은 [계획 5.1](docs/plan.md#51-관련도-기준) 참고.


| 구분  | 회사             | feed                                              | 수집 방식                 |
| --- | -------------- | ------------------------------------------------- | --------------------- |
| 국내  | NAVER D2       | `https://d2.naver.com/d2.atom`                    | feed 전문 · 세션 영상 글 포함  |
| 국내  | Toss           | `https://toss.tech/rss.xml`                       | feed 전문               |
| 국내  | 우아한형제들         | `https://techblog.woowahan.com/feed/`             | feed 전문               |
| 국내  | LINE (LY Corp) | `https://techblog.lycorp.co.jp/ko/feed/index.xml` | feed + 본문 요청          |
| 국내  | 당근             | `https://medium.com/feed/daangn`                  | feed 전문               |
| 국내  | 올리브영           | `https://oliveyoung.tech/rss.xml`                 | feed + 본문 요청          |
| 국내  | 무신사            | `https://medium.com/feed/musinsa-tech`            | feed 전문               |
| 국내  | Kakao          | `https://tech.kakao.com/feed/`                    | feed + 본문 요청          |
| 국내  | NHN Cloud      | `https://meetup.nhncloud.com/rss`                 | feed 전문               |
| 해외  | Netflix        | `https://netflixtechblog.com/feed`                | feed 전문               |
| 해외  | GitHub         | `https://github.blog/engineering/feed/`           | feed 전문               |
| 해외  | Cloudflare     | `https://blog.cloudflare.com/rss/`                | feed 전문 · 보안 공지 글은 필터 |
| 해외  | Airbnb         | `https://medium.com/feed/airbnb-engineering`      | feed 전문               |


해외 4곳은 대규모 트래픽 · 인프라 결정 사례를 보강하려고 넣었다.

### 제외


| 회사                                      | 이유                          |
| --------------------------------------- | --------------------------- |
| 하이퍼커넥트 · Slack                          | 최근 새 글 없음                   |
| 쿠팡                                      | 오랫동안 새 글 없음                 |
| 뱅크샐러드                                   | 최근 글이 디자인 위주                |
| Meta · Pinterest                        | 광고 · 추천 ML 모델링 위주           |
| AWS Architecture · Google Cloud         | 고객 사례 · 제품 소개 위주            |
| Lyft · Dropbox                          | 글이 적고 BE · FE 비중 낮음         |
| Stripe · Discord · Spotify · Shopify    | feed에 요약만 있고 회사 소식 글 비중이 높음 |
| Uber · LinkedIn · DoorDash · 컬리 · 카카오페이 | feed 주소를 찾지 못했거나 자동 요청 차단   |


실제 수집 설정은 위키 레포의 `config/sources.yaml`에서 관리한다.

## 문서

- [계획](docs/plan.md)
- [메뉴바 앱](app/README.md) · [엔진](engine/README.md)
- [상세 아키텍처](docs/architecture/architecture.png)
- [화면 시안](docs/ui/screens/) · [디자인 시스템](docs/ui/screens/design-system.png)