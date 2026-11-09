"""context-holder 개요 다이어그램(SVG) 생성기 — 주요 액터 4개 중심.

사용법:
    python3 docs/architecture/generate_overview.py
    rsvg-convert -z 1.5 docs/architecture/overview.svg -o docs/architecture/overview.png
"""
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1600, 840
FONT = "Pretendard, 'Apple SD Gothic Neo', sans-serif"
Point = tuple[float, float]

# key: (bg, accent, ink)
ACTORS = {
    "blog": ("#F1F3F5", "#495057", "#212529"),
    "worker": ("#E7F0FF", "#3B6FD8", "#1E3A8A"),
    "store": ("#E6F6EC", "#2F9E5B", "#14532D"),
    "app": ("#F1ECFF", "#7048E8", "#3B1F8C"),
    "claude": ("#FBEEE8", "#C2532B", "#7A2E12"),
}

out: list[str] = []


def text(x: float, y: float, s: str, size: float = 14, weight: int = 400, fill: str = "#212529",
         anchor: str = "start") -> None:
    out.append(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
               f'fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>')


def badge(x: float, y: float, n: int) -> None:
    out.append(f'<circle cx="{x}" cy="{y}" r="12" fill="#212529"/>')
    text(x, y + 5, str(n), size=13, weight=700, fill="#fff", anchor="middle")


def actor(x: float, y: float, w: float, h: float, key: str, role: str, title: str,
          lines: Sequence[str], nums: Sequence[int] = (), chip: str = "") -> None:
    bg, accent, ink = ACTORS[key]
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{bg}" '
               f'stroke="{accent}" stroke-width="2" filter="url(#shadow)"/>')
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="8" rx="4" fill="{accent}"/>')
    text(x + 24, y + 42, role, size=12.5, weight=700, fill=accent)
    text(x + 24, y + 74, title, size=24, weight=800, fill=ink)
    for i, n in enumerate(reversed(nums)):
        badge(x + w - 28 - i * 30, y + 38, n)
    for i, line in enumerate(lines):
        cy = y + 114 + i * 30
        out.append(f'<circle cx="{x + 30}" cy="{cy - 5}" r="3" fill="{accent}"/>')
        text(x + 42, cy, line, size=15, fill="#343A40")
    if chip:
        cw = len(chip) * 8.2 + 28
        out.append(f'<rect x="{x + 24}" y="{y + h - 46}" width="{cw}" height="28" rx="14" fill="#fff" stroke="{accent}"/>')
        text(x + 24 + cw / 2, y + h - 27, chip, size=12.5, weight=600, fill=ink, anchor="middle")


def store(cx: float, top: float, w: float, h: float) -> None:
    bg, accent, ink = ACTORS["store"]
    rx, ry = w / 2, 16
    left, right, bottom = cx - rx, cx + rx, top + h
    out.append(f'<path d="M {left} {top} L {left} {bottom} A {rx} {ry} 0 0 0 {right} {bottom} L {right} {top}" '
               f'fill="{bg}" stroke="{accent}" stroke-width="2" filter="url(#shadow)"/>')
    out.append(f'<ellipse cx="{cx}" cy="{top}" rx="{rx}" ry="{ry}" fill="#fff" stroke="{accent}" stroke-width="2"/>')
    text(cx, top + 48, "위키 + 인덱스", size=18, weight=800, fill=ink, anchor="middle")
    for i, line in enumerate(["Markdown 위키 (Git)", "GitHub이 원본 · 기기 간 동기화", "검색 인덱스는 기기마다"]):
        text(cx, top + 78 + i * 22, line, size=13, fill="#343A40", anchor="middle")
    badge(left + 4, top - 14, 2)


def arrow(points: Sequence[Point], color: str, dashed: bool = False,
          label: str | None = None, label_at: Point | None = None) -> None:
    d = "M " + " L ".join(f"{px} {py}" for px, py in points)
    dash = ' stroke-dasharray="7 6"' if dashed else ""
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.4"{dash} marker-end="url(#arrow)"/>')
    if label and label_at:
        lx, ly = label_at
        tw = len(label) * 8 + 18
        out.append(f'<rect x="{lx - tw / 2}" y="{ly - 15}" width="{tw}" height="22" rx="11" fill="#fff"/>')
        text(lx, ly + 1, label, size=13, weight=700, fill=color, anchor="middle")


out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
out.append("""<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/>
  </marker>
  <filter id="shadow" x="-5%" y="-5%" width="110%" height="120%">
    <feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#000" flood-opacity="0.08"/>
  </filter>
</defs>""")
out.append(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')

text(50, 58, "context-holder", size=32, weight=800, fill="#111")
text(312, 58, "빅테크 기술 결정 사례를 모아 나와 Claude의 문맥으로", size=19, weight=500, fill="#495057")

# 액터
actor(50, 300, 300, 230, "blog", "외부 소스", "빅테크 기술블로그", [
    "Netflix · Uber · Meta · AWS", "Toss · 우아한형제들 · Kakao", "NAVER D2 · LINE …", "RSS/Atom feed · 세션 영상",
])
actor(430, 270, 350, 290, "worker", "백그라운드 상주", "수집 · 정리 워커", [
    "매일 아침 자동 수집", "관련도 분류 → 불필요 기술 제외", "요약 + 기술 결정 기록 추출", "영상 글은 본문 요약 우선",
], nums=(1, 5), chip="Claude 구독으로 분류 · 요약")
store(970, 345, 220, 150)
actor(1160, 130, 390, 240, "app", "사람이 보는 곳", "화면 (데스크톱 앱)", [
    "메뉴바 상주 · 수집 상태", "오늘의 브리프 (주요 이슈 Top 5)", "검색 · 결정 사례 비교", "Inbox · 관련없음 피드백",
], nums=(3, 5))
actor(1160, 460, 390, 240, "claude", "에이전트가 쓰는 곳", "Claude (Claude Code)", [
    "기술 결정 전 MCP로 사례 검색", "결정 사례를 출처와 함께 인용", "맞는 사례가 없으면 없다고 답함", "좋은 새 글은 소스로 추천",
], nums=(4,))

# 주 흐름
arrow([(350, 415), (428, 415)], "#495057", label="수집", label_at=(389, 400))
arrow([(780, 415), (858, 415)], "#3B6FD8", label="저장", label_at=(819, 400))
arrow([(1080, 390), (1115, 390), (1115, 250), (1158, 250)], "#7048E8", label="조회 · 검색", label_at=(1115, 320))
arrow([(1080, 445), (1115, 445), (1115, 580), (1158, 580)], "#C2532B", label="MCP 검색", label_at=(1115, 515))

# 피드백 루프
arrow([(1355, 130), (1355, 100), (605, 100), (605, 268)], "#7048E8", dashed=True,
      label="관련없음 · 북마크 → 다음 수집에 반영", label_at=(980, 100))
arrow([(1355, 700), (1355, 745), (605, 745), (605, 562)], "#C2532B", dashed=True,
      label="suggest_source → 새 글 수집 요청", label_at=(980, 745))

# 요구사항 범례
legend = ["수집 · 저장", "인덱싱", "검색 · 오늘의 브리프", "기술 결정 문맥", "불필요 기술 정리"]
x = 50
text(x, 808, "요구사항", size=13, weight=700, fill="#495057")
x += 80
for i, s in enumerate(legend):
    badge(x, 803, i + 1)
    text(x + 20, 808, s, size=13, fill="#495057")
    x += 52 + sum(13.5 if ord(c) > 0x3000 else (4 if c == ' ' else 8) for c in s)

out.append("</svg>")
dest = Path(__file__).with_name("overview.svg")
dest.write_text("\n".join(out), encoding="utf-8")
print(f"wrote {dest}")
