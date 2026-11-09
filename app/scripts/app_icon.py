"""앱 아이콘 원본(1024px)을 만든다. 사이드바 브랜드 마크: 종이 위 문서 틀과 세 줄, 붉은 점.

실행: uv run --with pillow python app/scripts/app_icon.py
그다음: cd app && npx @tauri-apps/cli@2 icon icons/app-icon.png -o /tmp/ch-icons (필요한 것만 복사)
"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
PAPER = (247, 243, 234, 255)  # --paper
INK = (28, 27, 24, 255)  # --ink
TODAY = (180, 68, 30, 255)  # --today
OUT = Path(__file__).resolve().parents[1] / "icons" / "app-icon.png"


def main() -> None:
    scale = 4  # 크게 그려 줄이면 가장자리가 부드럽다
    big = SIZE * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = lambda v: int(v * scale)  # noqa: E731
    # macOS 아이콘 격자: 824px 둥근 사각형, 사방 100px 여백
    draw.rounded_rectangle((s(100), s(100), s(924), s(924)), radius=s(185), fill=PAPER)
    draw.rounded_rectangle((s(250), s(270), s(774), s(790)), radius=s(48), outline=INK, width=s(46))
    for y, right in ((420, 662), (530, 662), (640, 560)):
        draw.line((s(352), s(y), s(right), s(y)), fill=INK, width=s(46))
        draw.ellipse((s(352) - s(23), s(y) - s(23), s(352) + s(23), s(y) + s(23)), fill=INK)
        draw.ellipse((s(right) - s(23), s(y) - s(23), s(right) + s(23), s(y) + s(23)), fill=INK)
    draw.ellipse((s(680), s(178), s(868), s(366)), fill=TODAY, outline=PAPER, width=s(30))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.resize((SIZE, SIZE), Image.LANCZOS).save(OUT)


if __name__ == "__main__":
    main()
