# context-holder

빅테크 기술블로그의 기술 결정 사례를 모아, 나와 Claude의 문맥으로 쓰는 개인용 도구.

![context-holder 개요](docs/architecture/overview.png)

## 무엇을 하나

- **수집 · 정리 워커**: 백그라운드에서 매일 빅테크 기술블로그를 수집한다. 업무와 무관한 기술은 걸러 내고, 요약과 기술 결정 기록(문제 · 선택지 · 결정 · 트레이드오프)을 만들어 위키에 저장한다.
- **화면**: 메뉴바에 상주하는 데스크톱 앱이다. 오늘의 브리프, 검색, 회사별 결정 사례 비교를 보여 주고, 관련없는 글은 피드백으로 정리한다.
- **Claude**: 기술 결정 전에 MCP로 위키를 검색해 빅테크 사례를 출처와 함께 문맥으로 가져온다.

## 문서

- [계획](docs/plan.md)
- [상세 아키텍처](docs/architecture/architecture.png)
- [화면 시안](docs/ui/mockup-today.png)
