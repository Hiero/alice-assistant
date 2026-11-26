"""Crop her smiling face from the shell's portrait into the phone app's launcher icon.

    uv run --with pillow python tools/make_phone_icon.py

Writes android/icon/drawable-*/ic_launcher_fg.png, which is local art like shell/assets and not in git.
Without it, the app builds with the icon of her default art in android/default-icon/; --default remakes that one
from shell/default-art/.
"""

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = "--default" in sys.argv[1:]
ASSETS = ROOT / ("shell/default-art" if DEFAULT else "shell/assets")
OUT = ROOT / ("android/default-icon" if DEFAULT else "android/icon")

# Head and bow in the 480x702 portrait.
CROP = (70, 8, 410, 348)
# An adaptive icon is 108dp, and launchers may mask anything outside the middle 72dp.
DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


def main() -> None:
    portrait = Image.open(ASSETS / "alice-neutral.png").convert("RGBA")
    portrait.alpha_composite(Image.open(ASSETS / "alice-mood-happy.png").convert("RGBA"))
    face = portrait.crop(CROP)
    for name, scale in DENSITIES.items():
        canvas = round(108 * scale)
        inner = round(80 * scale)  # A little past the safe zone, so the round mask trims hair, not her face.
        icon = Image.new("RGBA", (canvas, canvas))
        offset = (canvas - inner) // 2
        icon.alpha_composite(face.resize((inner, inner), Image.LANCZOS), (offset, offset))
        folder = OUT / f"drawable-{name}"
        folder.mkdir(parents=True, exist_ok=True)
        icon.save(folder / "ic_launcher_fg.png")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
