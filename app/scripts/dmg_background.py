"""dmg 창 배경(660×400, 레티나 2배 포함)을 만든다. 앱 → Applications로 끌어 놓으라는 화살표와 안내.

실행: uv run --with pillow python app/scripts/dmg_background.py
결과: app/src-tauri/icons/dmg-background.tiff (1배 · 2배를 tiffutil로 합친다)
아이콘 위치는 tauri.conf.json의 bundle.macOS.dmg(appPosition · applicationFolderPosition)와 맞춘다.
"""

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 660, 400
APP_X, FOLDER_X, ICON_Y = 180, 480, 190  # 아이콘 가운데 (포인트)
PAPER = (247, 243, 234)  # --paper
RULE = (226, 219, 204)  # --rule
INK = (28, 27, 24)  # --ink
SOFT = (110, 103, 91)  # --ink-faint
TODAY = (180, 68, 30)  # --today
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
OUT = Path(__file__).resolve().parents[1] / "src-tauri" / "icons" / "dmg-background.tiff"


def render(scale: int) -> Image.Image:
    def s(v: float) -> int:
        return round(v * scale)

    image = Image.new("RGB", (s(WIDTH), s(HEIGHT)), PAPER)
    draw = ImageDraw.Draw(image)
    title = ImageFont.truetype(FONT, s(20), index=6)  # Bold
    body = ImageFont.truetype(FONT, s(13), index=0)
    # 위: 이름과 이중선 (사이드바 · 브리프 머리와 같은 결)
    draw.text((s(40), s(34)), "Context Holder", font=title, fill=INK)
    end = s(40) + draw.textlength("Context Holder", font=title) + s(3)  # 이름 끝 위에 붉은 점
    draw.ellipse((end, s(34), end + s(7), s(41)), fill=TODAY)
    draw.line((s(40), s(72), s(WIDTH - 40), s(72)), fill=INK, width=s(1))
    draw.line((s(40), s(76), s(WIDTH - 40), s(76)), fill=INK, width=s(1))
    # 가운데: 앱 → Applications 화살표 (아이콘 128pt 사이)
    left, right, y = APP_X + 84, FOLDER_X - 84, ICON_Y
    for x in range(left, right - 16, 12):  # 점선
        draw.line((s(x), s(y), s(min(x + 6, right - 16)), s(y)), fill=SOFT, width=s(2))
    draw.polygon(
        [(s(right), s(y)), (s(right - 14), s(y - 8)), (s(right - 14), s(y + 8))], fill=SOFT
    )
    # 아래: 안내
    text = "Context Holder를 Applications 폴더로 끌어 놓으세요"
    width = draw.textlength(text, font=body)
    draw.text(((s(WIDTH) - width) / 2, s(318)), text, font=body, fill=SOFT)
    draw.line((s(40), s(HEIGHT - 28), s(WIDTH - 40), s(HEIGHT - 28)), fill=RULE, width=s(1))
    return image


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        one, two = Path(tmp) / "bg.png", Path(tmp) / "bg@2x.png"
        render(1).save(one, dpi=(72, 72))
        render(2).save(two, dpi=(144, 144))
        OUT.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["tiffutil", "-cathidpicheck", str(one), str(two), "-out", str(OUT)], check=True
        )


if __name__ == "__main__":
    main()
