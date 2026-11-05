# context-holder

빅테크 기술블로그의 기술 결정 사례를 모아, 나와 Claude의 문맥으로 쓰는 개인용 도구.

![context-holder 개요](docs/architecture/overview.png)

## 무엇을 하나

- **수집 · 정리 워커**: 백그라운드에서 매일 빅테크 기술블로그를 수집한다. 업무와 무관한 기술은 걸러 내고, 요약과 기술 결정 기록(문제 · 선택지 · 결정 · 트레이드오프)을 만들어 위키에 저장한다.
- **화면**: 메뉴바에 상주하는 데스크톱 앱이다. 오늘의 브리프, 검색, 회사별 결정 사례 비교를 보여 주고, 관련없는 글은 피드백으로 정리한다.
- **Claude**: 기술 결정 전에 MCP로 위키를 검색해 빅테크 사례를 출처와 함께 문맥으로 가져온다.

## 시작하기

준비물:
- macOS
- GitHub 계정
- Claude Pro/Max 구독

git과 Python은 앱에 들어 있어 따로 설치할 필요가 없다.

1. 앱을 설치하고 실행한다.
2. **GitHub로 로그인**한다. 브라우저에서 승인하면 위키 레포(`context-wiki`, private)를 찾거나 새로 만든다.
3. **Claude 연결**. Claude Code가 없으면 앱이 설치하고, 브라우저에서 로그인을 승인한다.
4. 완료. 바로 첫 수집을 시작한다.

다른 기기에서도 같은 순서로 설치하면 같은 위키를 쓴다. 상세는 [계획 3.2](docs/plan.md#32-초기-설정--인증).

## 수집 대상 블로그

기준: **백엔드 · 프론트엔드 · AI 엔지니어링 글이 꾸준히 나오는 곳.** AI는 엔지니어링에 쓰는 글(LLM 비용 · 서빙, 에이전트 · MCP · 하네스, 컨텍스트 설계, AI 개발 자동화)만 수집하고, 모델을 만드는 글(임베딩 학습, 추천 모델 구조 등)은 거른다. 상세 기준은 [계획 5.1](docs/plan.md#51-관련도-기준) 참고.

| 구분 | 회사 | feed | 수집 방식 |
|---|---|---|---|
| 국내 | NAVER D2 | `https://d2.naver.com/d2.atom` | feed 전문 · 세션 영상 글 포함 |
| 국내 | Toss | `https://toss.tech/rss.xml` | feed 전문 |
| 국내 | 우아한형제들 | `https://techblog.woowahan.com/feed/` | feed 전문 |
| 국내 | LINE (LY Corp) | `https://techblog.lycorp.co.jp/ko/feed/index.xml` | feed + 본문 요청 |
| 국내 | 당근 | `https://medium.com/feed/daangn` | feed 전문 |
| 국내 | 올리브영 | `https://oliveyoung.tech/rss.xml` | feed + 본문 요청 |
| 국내 | 무신사 | `https://medium.com/feed/musinsa-tech` | feed 전문 |
| 국내 | Kakao | `https://tech.kakao.com/feed/` | feed + 본문 요청 |
| 국내 | NHN Cloud | `https://meetup.nhncloud.com/rss` | feed 전문 |
| 해외 | Netflix | `https://netflixtechblog.com/feed` | feed 전문 |
| 해외 | GitHub | `https://github.blog/engineering/feed/` | feed 전문 |
| 해외 | Cloudflare | `https://blog.cloudflare.com/rss/` | feed 전문 · 보안 공지 글은 필터 |
| 해외 | Airbnb | `https://medium.com/feed/airbnb-engineering` | feed 전문 |

해외 4곳은 대규모 트래픽 · 인프라 결정 사례를 보강하려고 넣었다.

### 제외

| 회사 | 이유 |
|---|---|
| 하이퍼커넥트 · Slack | 최근 새 글 없음 |
| 쿠팡 | 오랫동안 새 글 없음 |
| 뱅크샐러드 | 최근 글이 디자인 위주 |
| Meta · Pinterest | 광고 · 추천 ML 모델링 위주 |
| AWS Architecture · Google Cloud | 고객 사례 · 제품 소개 위주 |
| Lyft · Dropbox | 글이 적고 BE · FE 비중 낮음 |
| Stripe · Discord · Spotify · Shopify | feed에 요약만 있고 회사 소식 글 비중이 높음 |
| Uber · LinkedIn · DoorDash · 컬리 · 카카오페이 | feed 주소를 찾지 못했거나 자동 요청 차단 |

실제 수집 설정은 위키 레포의 `config/sources.yaml`에서 관리한다.

## 문서

- [계획](docs/plan.md)
- [상세 아키텍처](docs/architecture/architecture.png)
- [화면 시안](docs/ui/screens/) · [디자인 시스템](docs/ui/screens/design-system.png)
