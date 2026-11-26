"""Turns her base portrait into the four pictures the shell switches between, with a local ComfyUI:

    art/set/alice_<state>.png      832x1216 with the background cut away, the source for every other art tool
    shell/assets/alice-<state>.png the same at the shell's size

for the states neutral, listening, thinking and speaking. Neutral is the base itself. Listening and thinking
repaint her eyes and brows like tools/make_expressions.py does, and keep her mouth; speaking repaints only her
mouth, so the shell can flick between it and neutral for lip sync. All four line up pixel for pixel.

The base (art/set/base.png, 832x1216 on white) was painted on 2026-11-26 from the first art (art/v1/set/base.png):
repainted at denoise 0.80, seed 1, with the LoRA in tools/make_expressions.py at 0.8 and the tags
"<CHARACTER>, white hairband, blue dress, collared dress, puffy short sleeves, black neck ribbon, upper body,
front view, facing viewer, straight-on, looking at viewer, light smile, closed mouth, arms at sides".
After a new base, run this, then tools/make_faces.py, tools/make_expressions.py and tools/make_outfit.py.

    uv run --with pillow --with numpy python tools/make_base.py                       # write the pictures
    uv run --with pillow --with numpy python tools/make_base.py --try 4 listening     # candidates into art/base/
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_expressions import BOX, Face, is_skin, paint, sheet
from make_outfit import cut_out

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "art/set/base.png"
OUT = ROOT / "art/set"
ASSETS = ROOT / "shell/assets"
TRIALS = ROOT / "art/base"
SHELL_SIZE = (480, 702)
# Bottom-edge columns that are background: her hair and arms reach the bottom everywhere in between.
SIDE_MARGIN = 100
# Background closed off from the edges by a strand of hair, one point in each pocket (measured on base.png).
POCKETS = [(173, 580), (668, 560)]

# Each state: its tags, how much of the face is repainted, the seed that was picked, and what may change.
# Her eyes hold their shape firmly: "looking up" and "closed eyes" never took, and above about 0.9 the irises melt.
STATES = {
    "listening": ("attentive, interested, wide-eyed, raised eyebrows, light smile, closed mouth", 0.55, 4, "eyes"),
    "thinking": ("thinking, pensive, looking to the side, looking away, closed mouth", 0.6, 2, "eyes"),
    "speaking": ("open mouth, talking, :o, smile", 0.8, 2, "mouth"),
}


def painted(face: Face, state: str, seed: int | None = None) -> np.ndarray:
    tags, denoise, picked, part = STATES[state]
    new = np.asarray(paint(tags, denoise, picked if seed is None else seed).resize(face.crop.size, Image.LANCZOS)).astype(np.float32)
    cover = face.cover * (1 - face.mouth) if part == "eyes" else face.mouth
    # Repainting drifts the skin tone; one shift per colour channel brings it back to hers.
    both = is_skin(new) & is_skin(face.rgb) & (face.cover > 0.5)
    if both.sum() > 500:
        new += np.median((face.rgb - new)[both], axis=0)
    mixed = new.clip(0, 255) * cover[..., None] + face.rgb * (1 - cover[..., None])
    whole = np.asarray(face.art.convert("RGB")).copy()
    whole[BOX[1]:BOX[3], BOX[0]:BOX[2]] = mixed.astype(np.uint8)
    return whole


def main() -> None:
    arguments = sys.argv[1:]
    tries = 0
    if arguments[:1] == ["--try"]:
        tries, arguments = int(arguments[1]), arguments[2:]
    states = arguments or list(STATES)
    if unknown := [s for s in states if s not in STATES]:
        sys.exit(f"Unknown state {unknown[0]}. States: {', '.join(STATES)}")
    face = Face(SOURCE)
    base = np.asarray(face.art.convert("RGB"))
    crop = (BOX[0] - 40, BOX[1] - 40, BOX[2] + 40, BOX[3] + 40)
    tiles = [("neutral", Image.fromarray(base).crop(crop))]
    if tries:
        for state in states:
            for seed in range(STATES[state][2], STATES[state][2] + tries):
                tiles.append((f"{state}, seed {seed}", Image.fromarray(painted(face, state, seed)).crop(crop)))
        sheet(tiles, TRIALS / "candidates.png")
        return
    height, width = base.shape[:2]
    hint = np.full((height, width), 255, np.uint8)
    hint[-1, :SIDE_MARGIN] = hint[-1, width - SIDE_MARGIN:] = 0
    alpha = cut_out(Image.fromarray(base), hint)
    fill = Image.fromarray(((base.min(axis=-1) > 238) * 255).astype(np.uint8)).copy()
    for point in POCKETS:
        if fill.getpixel(point) == 255:
            ImageDraw.floodfill(fill, point, 128)
    pockets = Image.fromarray(((np.asarray(fill) == 128) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    alpha = np.minimum(alpha, 255 - np.asarray(pockets)).astype(np.uint8)
    pictures = {"neutral": base} | {state: painted(face, state) for state in states}
    for state, rgb in pictures.items():
        picture = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
        picture.save(OUT / f"alice_{state}.png")
        picture.resize(SHELL_SIZE, Image.LANCZOS).save(ASSETS / f"alice-{state}.png")
        print(f"wrote {OUT / f'alice_{state}.png'} and {ASSETS / f'alice-{state}.png'}")
        tiles.append((state, picture.convert("RGB").crop(crop)))
    sheet(tiles[1:], TRIALS / "sheet.png")


if __name__ == "__main__":
    main()
