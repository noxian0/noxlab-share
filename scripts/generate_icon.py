"""Export the approved NoxLab Share artwork for Windows and the mobile UI."""

from pathlib import Path

from PIL import Image


ASSETS = Path(__file__).resolve().parents[1] / "assets"
WINDOWS_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def main() -> None:
    with Image.open(ASSETS / "noxlab_share-master.png") as source:
        master = source.convert("RGBA")
    sizes = [(edge, edge) for edge in WINDOWS_SIZES]
    frames = [master.resize(size, Image.Resampling.LANCZOS) for size in sizes]
    frames[-1].save(ASSETS / "noxlab_share.ico", sizes=sizes, append_images=frames[:-1])
    master.resize((128, 128), Image.Resampling.LANCZOS).save(ASSETS / "noxlab_share.png", optimize=True)


if __name__ == "__main__":
    main()
