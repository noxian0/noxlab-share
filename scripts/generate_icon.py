from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ICON_PATH = ASSETS / "noxlab_share.ico"
PNG_PATH = ASSETS / "noxlab_share.png"


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/consolab.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def make_icon(size: int) -> Image.Image:
    scale = size / 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    bg = (12, 14, 18, 255)
    panel = (23, 26, 32, 255)
    border = (64, 69, 78, 255)
    red = (229, 57, 53, 255)
    text = (248, 248, 248, 255)

    radius = max(2, int(28 * scale))
    pad = int(18 * scale)
    draw.rounded_rectangle((pad, pad, size - pad, size - pad), radius=radius, fill=bg, outline=border, width=max(1, int(3 * scale)))
    draw.rounded_rectangle((int(34 * scale), int(40 * scale), size - int(34 * scale), size - int(38 * scale)), radius=max(2, int(18 * scale)), fill=panel)
    draw.rectangle((int(34 * scale), int(40 * scale), int(52 * scale), size - int(38 * scale)), fill=red)

    font = load_font(max(10, int(76 * scale)))
    label = "NL"
    bbox = draw.textbbox((0, 0), label, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (size - text_width) / 2 + int(7 * scale)
    y = (size - text_height) / 2 - int(8 * scale)
    draw.text((x, y), label, font=font, fill=text)

    bar_y = int(174 * scale)
    draw.rounded_rectangle(
        (int(78 * scale), bar_y, int(178 * scale), bar_y + max(2, int(8 * scale))),
        radius=max(1, int(4 * scale)),
        fill=red,
    )
    return image


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    base = make_icon(256)
    base.save(PNG_PATH)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(ICON_PATH, sizes=sizes)
    print(f"Wrote {ICON_PATH}")


if __name__ == "__main__":
    main()
