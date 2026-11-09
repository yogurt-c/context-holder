"""메뉴바 아이콘(template 이미지)을 만든다. 실행: uv run --with pillow python app/scripts/tray_icons.py

단색(검정 + 투명)이라 macOS가 라이트 · 다크 메뉴바에 맞춰 칠한다 (docs/ui/menubar.html).
- normal: 문서 틀과 세 줄   - busy: 흐린 틀 + 호   - error: 틀 + 채운 점   - paused: 흐린 틀 + 정지 표시
"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 44  # 22pt @2x
OUT = Path(__file__).resolve().parents[1] / "src-tauri" / "icons"
INK = (0, 0, 0, 255)
DIM = (0, 0, 0, 110)


def frame(draw: ImageDraw.ImageDraw, color) -> None:
    draw.rounded_rectangle((6, 8, 36, 36), radius=4, outline=color, width=3)
    for y, right in ((16, 30), (22, 30), (28, 24)):
        draw.line((12, y, right, y), fill=color, width=3)


def icon(name: str) -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if name == "normal":
        frame(draw, INK)
    elif name == "busy":
        frame(draw, DIM)
        draw.arc((24, 2, 42, 20), start=200, end=470, fill=INK, width=4)
    elif name == "error":
        frame(draw, INK)
        draw.ellipse((28, 2, 42, 16), fill=INK)
    elif name == "paused":
        frame(draw, DIM)
        draw.rectangle((27, 4, 31, 16), fill=INK)
        draw.rectangle((35, 4, 39, 16), fill=INK)
    image.save(OUT / f"tray-{name}.png")


if __name__ == "__main__":
    for state in ("normal", "busy", "error", "paused"):
        icon(state)
