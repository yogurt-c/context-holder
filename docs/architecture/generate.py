"""context-holder 아키텍처 다이어그램(SVG) 생성기.

사용법:
    python3 docs/architecture/generate.py
    rsvg-convert -z 1.5 docs/architecture/architecture.svg -o docs/architecture/architecture.png
"""
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1840, 1145
FONT = "Pretendard, 'Apple SD Gothic Neo', sans-serif"
MONO = "'JetBrains Mono', Menlo, monospace"

# key: (panel bg, accent stroke, title ink)
PALETTE = {
    "src": ("#F1F3F5", "#495057", "#343A40"),
    "cloud": ("#EEF3FF", "#3B6FD8", "#1E3A8A"),
    "local": ("#EAF7EF", "#2F9E5B", "#14532D"),
    "ing": ("#E7F0FF", "#3B6FD8", "#1E3A8A"),
    "cur": ("#FFF4E0", "#D98A00", "#7A4A00"),
    "sto": ("#E6F6EC", "#2F9E5B", "#14532D"),
    "srv": ("#F1ECFF", "#7048E8", "#3B1F8C"),
    "con": ("#FFEAF1", "#D6336C", "#7A1238"),
    "ops": ("#F8F9FA", "#868E96", "#343A40"),
}
C_ING, C_CUR, C_STO, C_SRV, C_CON = "#3B6FD8", "#D98A00", "#2F9E5B", "#7048E8", "#D6336C"

out: list[str] = []
Point = tuple[float, float]


def text(x: float, y: float, s: str, size: float = 13, weight: int = 400, fill: str = "#212529",
         anchor: str = "start", family: str = FONT) -> None:
    out.append(
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>'
    )


def badge(x: float, y: float, n: int) -> None:
    out.append(f'<circle cx="{x}" cy="{y}" r="11" fill="#212529"/>')
    text(x, y + 4.5, str(n), size=12, weight=700, fill="#fff", anchor="middle")


def badges_at(right_x: float, y: float, nums: Sequence[int]) -> None:
    for i, n in enumerate(reversed(nums)):
        badge(right_x - i * 26, y, n)


def panel(x: float, y: float, w: float, h: float, key: str, title: str,
          nums: Sequence[int] = (), sub: str = "", sub_dx: float = 0) -> None:
    bg, stroke, ink = PALETTE[key]
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{bg}" '
        f'stroke="{stroke}" stroke-opacity="0.45" stroke-width="1.6"/>'
    )
    text(x + 16, y + 27, title, size=16, weight=800, fill=ink)
    if sub:
        text(x + 16 + sub_dx, y + 27, sub, size=11.5, fill="#6C757D")
    badges_at(x + w - 20, y + 22, nums)


def subpanel(x: float, y: float, w: float, h: float, key: str, title: str) -> None:
    _, stroke, ink = PALETTE[key]
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#FFFFFF" fill-opacity="0.55" '
        f'stroke="{stroke}" stroke-opacity="0.6" stroke-dasharray="5 4"/>'
    )
    text(x + 14, y + 22, title, size=12.5, weight=700, fill=ink)


def box(x: float, y: float, w: float, h: float, key: str, title: str, lines: Sequence[str],
        nums: Sequence[int] = ()) -> None:
    """lines 중 공백으로 시작하는 줄은 고정폭(코드) 글꼴로 그린다."""
    _, stroke, ink = PALETTE[key]
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#fff" '
        f'stroke="{stroke}" stroke-width="1.6" filter="url(#shadow)"/>'
    )
    out.append(f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="2.5" fill="{stroke}"/>')
    text(x + 16, y + 25, title, size=14.5, weight=700, fill=ink)
    badges_at(x + w - 18, y + 20, nums)
    for i, line in enumerate(lines):
        is_code = line.startswith(" ")
        text(x + 16, y + 49 + i * 19, line.strip(), size=11.8 if is_code else 12.5,
             fill="#3F454B" if is_code else "#495057", family=MONO if is_code else FONT)


def arrow(points: Sequence[Point], color: str = "#495057", dashed: bool = False, both: bool = False,
          label: str | None = None, label_at: Point | None = None, head: bool = True) -> None:
    d = "M " + " L ".join(f"{px} {py}" for px, py in points)
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    start = ' marker-start="url(#arrow)"' if both else ""
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8"{dash}'
               f'{' marker-end="url(#arrow)"' if head else ""}{start}/>')
    if label and label_at:
        lx, ly = label_at
        text(lx, ly, label, size=11, weight=700, fill=color, anchor="middle")


# ---------- canvas ----------
out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
out.append("""<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/>
  </marker>
  <filter id="shadow" x="-5%" y="-5%" width="110%" height="120%">
    <feDropShadow dx="0" dy="1.5" stdDeviation="1.5" flood-color="#000" flood-opacity="0.08"/>
  </filter>
</defs>""")
out.append(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')

text(40, 50, "context-holder", size=28, weight=800, fill="#111")
text(262, 50, "빅테크 기술 결정 사례 위키 · 하네스 엔지니어링 문맥 관리 아키텍처", size=17, weight=500, fill="#495057")
text(40, 78, "전부 로컬: 메뉴바에 상주하는 데스크톱 앱이 엔진을 돌려 매일 수집 → 로컬 모델 분류 + Claude 구독 요약 → 위키 · 인덱스 → 앱 화면 · MCP · CLI",
     size=13, fill="#868E96")

TOP, BOT = 100, 805

# ---------- 외부 소스 ----------
panel(30, TOP, 200, BOT - TOP, "src", "외부 소스")
box(45, 150, 170, 330, "src", "빅테크 기술블로그", [
    "Netflix TechBlog", "Uber Engineering", "Meta Engineering", "AWS Architecture",
    "Google Cloud Blog", "Airbnb Tech", "LinkedIn Eng", "Discord / Slack Eng",
    "Toss Tech", "우아한형제들", "Kakao Tech", "NAVER D2", "LINE Engineering", "…",
])
box(45, 505, 170, 140, "src", "수집 방식", ["RSS / Atom feed 우선", "HTML (feed 없는 곳)", "D2: d2.atom (본문 포함)", "robots.txt 준수"])
box(45, 665, 170, 120, "src", "세션 영상 (M4)", ["네이버 클립 · YouTube", "슬라이드 (PDF 등)"])

# ---------- 내 Mac ----------
panel(255, TOP, 1245, BOT - TOP, "local", "내 Mac (전부 로컬)", nums=(1, 2, 3, 4, 5),
      sub="API 키 · 서버 · CI 없음 · 위키 레포는 GitHub private에 백업", sub_dx=150)

# 수집 워커
subpanel(270, 160, 340, 630, "ing", "ctx 엔진 (Python) · 수집 워커")
X_A, W_A = 285, 310
box(X_A, 195, W_A, 101, "ing", "Scheduler", ["앱 시작 · 잠자기 해제 · 평일 09:30", "→ 오늘 미수집이면 수집", "완료 시 앱이 macOS 알림"])
box(X_A, 310, W_A, 82, "ing", "Fetcher", ["RSS/Atom 우선 → HTML fallback", "마지막 본 글 이후만 (따라잡기)"], nums=(1,))
box(X_A, 406, W_A, 82, "ing", "Extract · Dedup", ["Readability → Markdown · 중복 제거", "영상 글 감지 → summary_only"], nums=(1,))
box(X_A, 502, W_A, 101, "cur", "Relevance Classifier", [
    "1차: profile.yaml 키워드", "2차: 로컬 모델 + feedback 예시", "≥0.7 저장 · 0.4~0.7 inbox",
], nums=(5,))
box(X_A, 617, W_A, 101, "cur", "Enricher", [
    "요약 · 영문 태그 · Decision Record", "evidence 원문 대조 (코드 검증)", "→ wiki/ 쓰기 · git commit",
], nums=(4,))
text(X_A + 4, 765, "하루 10분 이내 · 모델은 작업 때만 로드", size=11.5, fill="#6C757D")

# 저장
subpanel(630, 160, 320, 260, "sto", "저장")
box(645, 195, 290, 101, "sto", "wiki/ (Git 레포)", [
    "wiki/ decisions/ inbox/ archive/", "feedback/ · config/ · Obsidian vault", "git push → GitHub private (백업)",
], nums=(1,))
box(645, 312, 290, 82, "sto", "index.db (SQLite)", ["Indexer: FTS5 trigram · 용어 사전", "decision > tags > 요약 가중치"], nums=(2,))

# LLM Gateway
subpanel(630, 440, 320, 350, "cur", "LLM Gateway · 설정으로 전환")
box(645, 475, 290, 101, "cur", "로컬 모델 (Ollama)", ["Qwen3 8B Q4 (~5GB)", "관련도 분류 · 주간 Pruner", "keep_alive 0 → 끝나면 언로드"])
box(645, 600, 290, 101, "srv", "Claude 구독 (claude -p)", ["Sonnet: 요약 · Decision Record", "하루 2~3건 · 별도 비용 없음", "한도 초과 → 로컬 모델로 대체"], nums=(4,))
text(649, 765, "품질이 중요한 곳만 Claude, 나머지는 로컬", size=11.5, fill="#6C757D")

# 화면 · 인터페이스
subpanel(970, 160, 515, 630, "srv", "데스크톱 앱 (Tauri) · 인터페이스")
box(985, 195, 485, 139, "srv", "context-holder.app — 메뉴바 + 메인 창", [
    "메뉴바: 상태 색 · 지금 수집 · 일시정지 · ⌘⇧B / ⌘⇧K",
    "창: Today · Search · Decisions · Inbox · Sources",
    "창을 닫아도 메뉴바 상주 · 로그인 시 자동 실행",
    "엔진 sidecar 실행 · 감시 · 재시작",
    "화면 = 엔진이 렌더링한 HTML (127.0.0.1 + 토큰)",
], nums=(3, 5))
box(985, 420, 240, 82, "srv", "SessionStart Hook", ["오늘의 브리프 출력", "앱 꺼져도 index.db 직접 읽기"], nums=(3,))
box(985, 520, 240, 120, "srv", "ctx CLI", [" ctx search <q>", " ctx digest", " ctx sync", " ctx enrich <id>"], nums=(3,))
box(1240, 420, 230, 196, "srv", "MCP Server (stdio)", [
    " list_topics()", " search_wiki(queries[])", " find_decision_cases()", " get_decision(id)",
    " get_article(id)", " suggest_source(url)", "RRF · 0건 → 유사 토픽", "앱과 독립 실행 (WAL 읽기)",
], nums=(4,))

# ---------- 사용 채널 ----------
panel(1525, TOP, 285, BOT - TOP, "con", "사용 채널")
X_C, W_C = 1540, 255
box(X_C, 195, W_C, 101, "con", "나 (메뉴바 · 창)", ["출근 후 오늘의 브리프 확인", "검색 · 결정 사례 비교", "피드백 버튼으로 정리 (⑤)"], nums=(3,))
box(X_C, 315, W_C, 63, "con", "macOS 알림", ["수집 완료 · 클릭 → 브리프 창"])
box(X_C, 420, W_C, 120, "con", "Claude Code", [
    "결정 전 MCP 검색", "질의를 한·영으로 확장해 전송", "→ 사례를 출처와 함께 인용", "세션 시작 시 Digest 표시",
], nums=(4,))
box(X_C, 660, W_C, 63, "con", "Terminal", ["ctx 명령"])

# ---------- arrows ----------
arrow([(215, 315), (245, 315), (245, 350), (285, 350)], C_ING, label="feed", label_at=(245, 333))
for y1, y2, c in [(296, 310, C_ING), (392, 406, C_ING), (488, 502, C_CUR), (603, 617, C_CUR)]:
    arrow([(440, y1), (440, y2)], c)
arrow([(595, 540), (645, 540)], C_CUR, both=True)
arrow([(595, 660), (645, 660)], C_SRV, both=True)
arrow([(595, 705), (618, 705), (618, 240), (645, 240)], C_STO, label="commit", label_at=(618, 728))
arrow([(790, 296), (790, 312)], C_STO)
# index.db -> 인터페이스
arrow([(935, 353), (960, 353), (960, 265), (985, 265)], C_STO)
arrow([(960, 353), (960, 405), (1355, 405), (1355, 420)], C_STO)
arrow([(960, 405), (960, 580), (985, 580)], C_STO)
# 피드백: 웹 → wiki/feedback
arrow([(1330, 195), (1330, 150), (790, 150), (790, 195)], C_CON, dashed=True,
      label="피드백 · 북마크 · suggest_source", label_at=(1060, 145))
# 인터페이스 → 사용 채널
arrow([(1470, 245), (1540, 245)], C_SRV, both=True)
arrow([(1470, 480), (1540, 480)], C_SRV, both=True, label="MCP", label_at=(1505, 472))
arrow([(1105, 640), (1105, 690), (1540, 690)], C_SRV, both=True)

# ---------- 하단 ----------
BAND_Y = 840
panel(30, BAND_Y, 1780, 285, "ops", "외부 · 운영")
out.append(f'<rect x="45" y="{BAND_Y + 45}" width="225" height="185" rx="10" fill="#fff" stroke="#CED4DA"/>')
text(60, BAND_Y + 70, "요구사항 매핑", size=14, weight=700)
for i, s in enumerate(["수집 · 위키 저장", "위키 인덱싱", "검색 · 당일 주요 이슈", "기술결정 사례 문맥", "불필요 기술 정리"]):
    badge(70, BAND_Y + 95 + i * 27, i + 1)
    text(90, BAND_Y + 100 + i * 27, s, size=12.5, fill="#343A40")

BY, BH, BW = BAND_Y + 45, 130, 285
box(290, BY, BW, BH, "sto", "GitHub private repo", ["위키 레포 백업 · 변경 이력", "다른 Mac은 clone 후 사용", "Actions · Secrets 사용 안 함"])
box(595, BY, BW, BH, "ops", "로컬 리소스", ["모델은 작업 때만 로드 (8B 안팎)", "앱 셸은 Tauri (수십 MB)", "Whisper 등은 필요할 때 설치"])
box(900, BY, BW, BH, "ing", "M4 확장", ["Media Worker: 슬라이드·자막·Whisper", "로컬 임베딩 bge-m3 (평가 후)", "네이버 클립 추출은 검증 전"])
box(1205, BY, BW, BH, "con", "저작권 · 보안 (개인용)", ["위키 레포는 private", "전문 미저장 (요약 · 링크)", "구독 자동 사용은 약관 확인"])
box(1510, BY, BW, BH, "ops", "Feedback Loop", ["웹의 '관련없음' → feedback/", "→ 다음 수집 Classifier 반영", "→ 주간 Pruner 재평가"], nums=(5,))
text(920, BAND_Y + 212, "데이터 흐름: 외부 블로그 → ctx 엔진 (로컬 모델 · claude -p) → wiki/ (GitHub 백업) → index.db → 앱 화면 · MCP · CLI · Hook",
     size=12.5, weight=600, fill="#495057", anchor="middle")
text(920, BAND_Y + 237, "실선 = 데이터 흐름 · 점선 = 피드백 · (M4) = 후순위", size=11.5, fill="#868E96", anchor="middle")

out.append("</svg>")

dest = Path(__file__).with_name("architecture.svg")
dest.write_text("\n".join(out), encoding="utf-8")
print(f"wrote {dest}")
