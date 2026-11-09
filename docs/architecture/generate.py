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
text(40, 78, "메뉴바에 상주하는 데스크톱 앱이 엔진을 돌려 매일 수집 → Claude 구독으로 분류 · 요약 → 위키 · 인덱스 → 앱 화면 · MCP · CLI",
     size=13, fill="#868E96")

TOP, BOT = 100, 805

# ---------- 외부 소스 ----------
panel(30, TOP, 200, BOT - TOP, "src", "외부 소스")
box(45, 150, 170, 330, "src", "빅테크 기술블로그", [
    "NAVER D2", "Toss", "우아한형제들", "LINE (LY Corp)", "당근", "올리브영", "무신사",
    "Kakao", "NHN Cloud", "Netflix", "GitHub", "Cloudflare", "Airbnb",
])
box(45, 505, 170, 140, "src", "수집 방식", ["RSS / Atom feed 우선", "요약만 있으면 원문 요청", "SPA 블로그는 본문 API", "5xx는 재시도"])
box(45, 665, 170, 120, "src", "세션 영상 (M4)", ["네이버 클립 · YouTube", "슬라이드 (PDF 등)"])

# ---------- 내 Mac ----------
panel(255, TOP, 1245, BOT - TOP, "local", "각 기기 (모두 동등한 사본)", nums=(1, 2, 3, 4, 5),
      sub="API 키 · 서버 · CI 없음 · 어느 기기든 수집 가능 · GitHub 레포가 원본", sub_dx=200)

# 수집 워커
subpanel(270, 160, 340, 630, "ing", "ctx 엔진 (Python) · 수집 워커")
X_A, W_A = 285, 310
box(X_A, 195, W_A, 101, "ing", "Scheduler · 수집 잠금", ["앱 시작 · 잠자기 해제 · 평일 09:30", "pull → 오늘 기록 없으면 잠금 push", "push 성공한 기기만 수집 · 완료 알림"])
box(X_A, 310, W_A, 82, "ing", "Fetcher", ["RSS/Atom · ETag · 원문 · 본문 API", "마지막 본 글 이후만 (따라잡기)"], nums=(1,))
box(X_A, 406, W_A, 82, "ing", "Extract · Dedup", ["trafilatura → Markdown · 중복 제거", "영상 글 감지 → summary_only"], nums=(1,))
box(X_A, 502, W_A, 101, "cur", "Relevance Classifier", [
    "profile.yaml 주제 기준 (5.1)", "Claude 판정 + feedback 예시", "≥0.6 저장 · 0.3~0.6 inbox",
], nums=(5,))
box(X_A, 617, W_A, 101, "cur", "Enricher", [
    "요약 · 핵심 내용 · 태그 · Decision Record", "evidence 원문 대조 · 단락 벡터", "→ wiki/ · vectors/ 쓰기 · commit",
], nums=(4,))
text(X_A + 4, 765, "하루 수 분 이내 · 실패한 글은 pending으로 보관", size=11.5, fill="#6C757D")

# 저장
subpanel(630, 160, 320, 260, "sto", "저장")
box(645, 195, 290, 101, "sto", "wiki/ (Git 레포)", [
    "wiki/ vectors/ decisions/ inbox/", "feedback/ · config/ · state/", "pull · push ⇄ GitHub (원본)",
], nums=(1,))
box(645, 312, 290, 82, "sto", "index.db (SQLite)", ["FTS5 trigram + 다국어 임베딩 (RRF)", "원문 단락 벡터는 vectors/에서 읽기"], nums=(2,))

# LLM Gateway
subpanel(630, 440, 320, 350, "srv", "LLM · Claude 구독")
box(645, 475, 290, 240, "srv", "Claude (claude -p)", ["분류: 그날 새 글을 묶어 1회", "요약 · Decision Record: 글마다 1회", "주간 Pruner: 요약 50건씩 묶음", "", "실패 (한도 · 로그인 만료 · 네트워크)", "→ status: pending 저장 후 재처리", "API 키 없음 · 구독 외 비용 없음"], nums=(4, 5))
text(649, 765, "하루 2~3건 · 구독 사용량 영향 작음", size=11.5, fill="#6C757D")

# 화면 · 인터페이스
subpanel(970, 160, 515, 630, "srv", "데스크톱 앱 (Tauri) · 인터페이스")
box(985, 195, 485, 139, "srv", "Context Holder.app — 메뉴바 + 메인 창", [
    "메뉴바: 상태 아이콘 · 지금 수집 · 일시정지",
    "창: Today · Search · Decisions · Inbox · Sources",
    "창을 닫아도 메뉴바 상주 · 로그인 시 자동 실행",
    "엔진 sidecar 실행 · 감시 · 재시작",
    "화면 = 엔진이 렌더링한 HTML (127.0.0.1 + 토큰)",
], nums=(3, 5))
box(985, 420, 240, 120, "srv", "ctx CLI", [" ctx search <q>", " ctx sync", " ctx init", " ctx pull"], nums=(3,))
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
    "결정 전 MCP 검색", "질의를 한·영으로 확장해 전송", "→ 사례를 출처와 함께 인용", "맞는 사례가 없으면 없다고 답함",
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
arrow([(960, 405), (960, 480), (985, 480)], C_STO)
# 피드백: 웹 → wiki/feedback
arrow([(1330, 195), (1330, 150), (790, 150), (790, 195)], C_CON, dashed=True,
      label="피드백 · 북마크 · suggest_source", label_at=(1060, 145))
# 인터페이스 → 사용 채널
arrow([(1470, 245), (1540, 245)], C_SRV, both=True)
arrow([(1470, 480), (1540, 480)], C_SRV, both=True, label="MCP", label_at=(1505, 472))
arrow([(1105, 540), (1105, 690), (1540, 690)], C_SRV, both=True)

# ---------- 하단 ----------
BAND_Y = 840
panel(30, BAND_Y, 1780, 285, "ops", "외부 · 운영")
out.append(f'<rect x="45" y="{BAND_Y + 45}" width="225" height="185" rx="10" fill="#fff" stroke="#CED4DA"/>')
text(60, BAND_Y + 70, "요구사항 매핑", size=14, weight=700)
for i, s in enumerate(["수집 · 위키 저장", "위키 인덱싱", "검색 · 당일 주요 이슈", "기술결정 사례 문맥", "불필요 기술 정리"]):
    badge(70, BAND_Y + 95 + i * 27, i + 1)
    text(90, BAND_Y + 100 + i * 27, s, size=12.5, fill="#343A40")

BY, BH, BW = BAND_Y + 45, 130, 285
box(290, BY, BW, BH, "sto", "GitHub private repo (원본)", ["모든 기기가 pull · push로 동기화", "push 성공 = 오늘 수집 담당 (잠금)", "새 기기: clone → 전체 인덱싱"])
box(595, BY, BW, BH, "ops", "운영 · 장애 대응", ["Claude 실패 → pending 후 재처리", "메뉴바에 '요약 대기 N건' 표시", "앱 셸은 Tauri (수십 MB)"])
box(900, BY, BW, BH, "ing", "M4 확장", ["Media Worker: 슬라이드·자막·Whisper", "네이버 클립 추출은 검증 전"])
box(1205, BY, BW, BH, "con", "저작권 · 보안 (개인용)", ["위키 레포는 private", "전문 미저장 (요약 · 링크)", "구독 자동 사용은 약관 확인"])
box(1510, BY, BW, BH, "ops", "Feedback Loop", ["웹의 '관련없음' → feedback/", "→ 다음 수집 Classifier 반영", "→ 주간 Pruner 재평가"], nums=(5,))
text(920, BAND_Y + 212, "데이터 흐름: 외부 블로그 → ctx 엔진 (claude -p) → wiki/ ⇄ GitHub (원본) → index.db → 앱 화면 · MCP · CLI",
     size=12.5, weight=600, fill="#495057", anchor="middle")
text(920, BAND_Y + 237, "실선 = 데이터 흐름 · 점선 = 피드백 · (M4) = 후순위", size=11.5, fill="#868E96", anchor="middle")

out.append("</svg>")

dest = Path(__file__).with_name("architecture.svg")
dest.write_text("\n".join(out), encoding="utf-8")
print(f"wrote {dest}")
