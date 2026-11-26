"""Paints a wardrobe outfit she can put on and take off, with a local ComfyUI, from her usual art:

    shell/assets/outfits/<name>/alice-neutral.png, -listening, -thinking, -speaking

Everything around her face is repainted: dress, hair, and room above her head for a crown. Her face
from the brows to the chin is kept from art/set/, so each of the four faces, the painted moods and
the lip sync still line up pixel for pixel. An outfit with a new look (evil: black hair, red eyes)
also gets its own recoloured copy of every face overlay in its folder; the shell prefers those.
It needs ComfyUI running with an Illustrious (SDXL anime) checkpoint, like tools/make_expressions.py:

    uv run --with pillow --with numpy python tools/make_outfit.py queen            # write the assets
    uv run --with pillow --with numpy python tools/make_outfit.py --try 4 evil     # 4 candidates into art/wardrobe/
"""

import io
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_expressions import API, CHARACTER, NEGATIVE, call, loaders, sheet, upload

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "art/set"
ASSETS = ROOT / "shell/assets"
TRIALS = ROOT / "art/wardrobe"
STATES = ("neutral", "listening", "thinking", "speaking")
WIDTH, HEIGHT, SHELL_WIDTH, SHELL_HEIGHT = 832, 1216, 480, 702

# Measured on the 832 px art: the face that is kept, and the eyes that a new look recolours.
KEEP = (262, 318, 590, 592)
EYES = [(335, 393, 62, 34), (510, 392, 62, 34)]
# Face overlays from tools/make_faces.py and tools/make_expressions.py, copied recoloured for a new look.
MOODS = ("happy", "excited", "surprised", "curious", "worried", "sad", "pout", "embarrassed", "sleepy")
OVERLAYS = ("blink", "happy", "halflid", "blush", *(f"mood-{m}{e}" for m in MOODS for e in ("", "-eyes")))

STYLE = "masterpiece, best quality, amazing quality, very aesthetic, absurdres, white background, simple background"
# Each outfit: its tags, what to keep out, room added above her head (832 px scale), the seed that was
# picked, and whether she gets black hair and red eyes.
OUTFITS = {
    "queen": (
        (
            f"{CHARACTER}, upper body, looking at viewer, "
            "small golden crown with jewels on head, queen, royal, regal white and gold gown, red velvet cape, "
            "ermine fur trim, gold embroidery, jeweled brooch, puffy sleeves, chess queen, no hair bow, no hairband"
        ),
        "hair bow, hairband, hands, holding",
        128, 3, False,
    ),
    "evil": (
        (
            "alice (megami tensei), 1girl, solo, black hair, long hair, blunt bangs, red eyes, upper body, looking at viewer, "
            "gothic lolita, black dress, black puffy sleeves, dark red ribbon, black apron, black frills, crimson trim, "
            "large black hair bow with red trim, dark clothes, crimson accents"
        ),
        "white apron, white frills, white clothes, white hairband, maid headdress, blue, hands",
        0, 1, True,
    ),
    # Holiday outfits, worn on the days in backend/alice/occasions.py.
    "christmas": (
        (
            f"{CHARACTER}, upper body, looking at viewer, christmas, green capelet with white fur trim, red dress, "
            "holly brooch with red berries, green plaid hair bow with holly, white fur trim"
        ),
        "blue dress, blue, white apron, hands",
        0, 1, False,
    ),
    "easter": (
        (
            f"{CHARACTER}, upper body, looking at viewer, easter, pastel light blue dress, white apron with bunny print, "
            "light blue hair bow, flower-shaped buttons, frilled collar, pastel colors"
        ),
        "dark blue, black ribbon, hands",
        0, 2, False,
    ),
    "halloween": (
        (
            f"{CHARACTER}, upper body, looking at viewer, halloween, witch, (large pointy witch hat:1.3), hat with tall cone, "
            "black witch hat, purple ribbon on hat, orange hat band, black dress, purple vest, orange trim, jack-o'-lantern brooch, black and orange ribbon"
        ),
        "blue dress, blue, white hairband, hair bow, halo, beret, round hat, hands, holding",
        224, 2, False,
    ),
    "new_year": (
        (
            f"{CHARACTER}, upper body, looking at viewer, new year, starry deep blue dress with sparkling stars, "
            "gold star brooch, cream apron with gold trim, blue hair bow with stars, glitter, night sky pattern"
        ),
        "black ribbon, hands",
        0, 2, False,
    ),
    "sweethearts": (
        (
            f"{CHARACTER}, upper body, looking at viewer, valentine, navy dress, pink apron, pink hair bow, "
            "heart-shaped brooch, pink neck ribbon, white frills, hearts"
        ),
        "blue dress, black ribbon, white hairband, hands",
        0, 1, False,
    ),
}


def witch_hat(canvas: Image.Image, room: int) -> None:
    """A rough witch hat for the painter to finish: a tall cone on a wide brim, with an orange band."""
    draw = ImageDraw.Draw(canvas)
    brim = room + 125
    draw.polygon([(282, brim), (548, brim), (470, 6), (440, 0)], fill=(38, 28, 48))
    draw.ellipse([150, brim - 34, 682, brim + 40], fill=(38, 28, 48))
    draw.polygon([(276, brim - 4), (554, brim - 4), (544, brim - 40), (288, brim - 40)], fill=(226, 110, 30))


# Outfits that need a rough shape to start from, like a hat taller than any hair.
SKETCHES = {"halloween": witch_hat}


def paint(image: str, mask: str, tags: str, avoid: str, seed: int, size: tuple[int, int]) -> Image.Image:
    graph, model = loaders()
    graph |= {
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": f"{tags}, {STYLE}"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": f"{NEGATIVE}, {avoid}"}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image}},
        "6": {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}},
        "7": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["7", 0], "mask": ["6", 0]}},
        "9": {"class_type": "KSampler", "inputs": {
            "model": model, "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["8", 0], "seed": seed,
            "steps": 30, "cfg": 6.0, "sampler_name": "euler_ancestral", "scheduler": "normal", "denoise": 0.88,
        }},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["1", 2]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "alice_outfit/try"}},
    }
    job = call("/prompt", {"prompt": graph})["prompt_id"]
    while True:
        with urllib.request.urlopen(f"{API}/history/{job}", timeout=30) as reply:
            entry = json.load(reply).get(job)
        if entry is None:
            time.sleep(0.5)
            continue
        if entry.get("status", {}).get("status_str") == "error":
            sys.exit(f"ComfyUI failed: {json.dumps(entry['status'])[:600]}")
        picture = entry["outputs"]["11"]["images"][0]
        with urllib.request.urlopen(f"{API}/view?{urllib.parse.urlencode(picture)}", timeout=30) as reply:
            return Image.open(io.BytesIO(reply.read())).convert("RGB").resize(size, Image.LANCZOS)


def recolour(rgba: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Blonde hair to black with a cool sheen, and gold eyes to red. Skin, lines and whites stay."""
    out = rgba.astype(np.float32).copy()
    red, green, blue = out[..., 0], out[..., 1], out[..., 2]
    luma = 0.299 * red + 0.587 * green + 0.114 * blue
    yy, xx = np.mgrid[0:out.shape[0], 0:out.shape[1]].astype(np.float32)
    eye = np.zeros(luma.shape, np.float32)
    for cx, cy, rx, ry in EYES:
        eye = np.maximum(eye, ((((xx - cx * scale) / (rx * scale)) ** 2 + ((yy - cy * scale) / (ry * scale)) ** 2) <= 1).astype(np.float32))
    # Gold irises have far more red than blue; skin and eye whites have little more.
    iris = np.clip((red - blue - 75) / 25, 0, 1) * eye
    # Blonde has far more green than blue; skin, even in shadow, doesn't.
    hair = np.clip((green - blue - 16) / 10, 0, 1) * np.clip((red - green + 5) / 20, 0, 1) * np.clip((120 - (red - green)) / 25, 0, 1) * (1 - iris)
    dark, sheen = np.array([14, 12, 20], np.float32), np.array([92, 84, 118], np.float32)
    shade = np.clip((luma - 90) / 165, 0, 1)[..., None] ** 2.6
    rgb = out[..., :3]
    rgb = rgb * (1 - hair[..., None]) + (dark + (sheen - dark) * shade) * hair[..., None]
    crimson = np.stack([red * 0.88 + 8, blue * 0.25, blue * 0.35], -1)
    rgb = rgb * (1 - iris[..., None]) + crimson * iris[..., None]
    out[..., :3] = rgb
    return out.clip(0, 255).astype(np.uint8)


def load(state: str, evil: bool) -> np.ndarray:
    art = np.asarray(Image.open(SOURCES / f"alice_{state}.png").convert("RGBA"))
    return recolour(art) if evil else art


def padded(art: np.ndarray, room: int) -> np.ndarray:
    canvas = np.zeros((HEIGHT + room, WIDTH, 4), np.uint8)
    canvas[room:] = art
    return canvas


def flatten(art: np.ndarray) -> Image.Image:
    alpha = art[..., 3:4].astype(np.float32) / 255
    return Image.fromarray((art[..., :3] * alpha + 255 * (1 - alpha)).astype(np.uint8))


def keep_mask(room: int, size: tuple[int, int]) -> np.ndarray:
    mask = Image.new("L", size, 0)
    left, top, right, bottom = KEEP
    ImageDraw.Draw(mask).rounded_rectangle([left, top + room, right, bottom + room], radius=90, fill=255)
    return np.asarray(mask.filter(ImageFilter.GaussianBlur(12))).astype(np.float32) / 255


def cut_out(painted: Image.Image, original_alpha: np.ndarray) -> np.ndarray:
    """Her outline on the plain white background: white reached from the top and sides, plus the old background
    along the bottom edge, so a white apron touching the bottom isn't mistaken for background, plus the pockets
    of background that her hair encloses."""
    rgb = np.asarray(painted).astype(np.int16)
    white = (rgb.min(axis=-1) > 238).astype(np.uint8) * 255
    fill = Image.fromarray(white).copy()  # Pillow ignores a flood fill on an image still sharing the array.
    height, width = white.shape
    seeds = [(x, 0) for x in range(0, width, 4)] + [(0, y) for y in range(0, height, 4)] + [(width - 1, y) for y in range(0, height, 4)]
    seeds += [(x, height - 1) for x in range(0, width, 4) if original_alpha[height - 1, x] < 10]
    for seed in seeds:
        if fill.getpixel(seed) == 255:
            ImageDraw.floodfill(fill, seed, 128)
    # Strands of hair can close around a patch of background, which no fill from the edges reaches. A white
    # patch that lies mostly where the old picture was background is such a pocket; white clothes lie on her.
    was_background = original_alpha < 10
    while True:
        left = np.argwhere((np.asarray(fill) == 255) & was_background)
        if not len(left):
            break
        y, x = left[0]
        ImageDraw.floodfill(fill, (int(x), int(y)), 64)
        patch = np.asarray(fill) == 64
        pocket = was_background[patch].mean() > 0.5
        fill = Image.fromarray(np.where(patch, 128 if pocket else 200, np.asarray(fill)).astype(np.uint8)).copy()
    background = (np.asarray(fill) == 128).astype(np.uint8) * 255
    edge = Image.fromarray(background).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    return 255 - np.asarray(edge)


def build(name: str, seed: int | None = None) -> tuple[dict[str, np.ndarray], Image.Image]:
    tags, avoid, room, picked, evil = OUTFITS[name]
    faces = {state: padded(load(state, evil), room) for state in STATES}
    size = (WIDTH, HEIGHT + room)
    keep = keep_mask(room, size)
    start = flatten(faces["neutral"])
    if name in SKETCHES:
        SKETCHES[name](start, room)
    upload("alice_outfit.png", start)
    upload("alice_outfit_mask.png", Image.fromarray(((1 - keep) * 255).astype(np.uint8)).convert("RGB"))
    painted = paint("alice_outfit.png", "alice_outfit_mask.png", tags, avoid, picked if seed is None else seed, size)
    alpha = cut_out(painted, faces["neutral"][..., 3])
    new = np.asarray(painted).astype(np.float32)
    out = {}
    for state, face in faces.items():
        rgb = face[..., :3] * keep[..., None] + new * (1 - keep[..., None])
        a = alpha.astype(np.float32) * (1 - keep) + face[..., 3] * keep
        out[state] = np.dstack([rgb, a]).clip(0, 255).astype(np.uint8)
    preview = Image.new("RGB", size, (40, 30, 48))
    preview.paste(Image.fromarray(out["neutral"]), mask=Image.fromarray(out["neutral"][..., 3]))
    return out, preview


def save(name: str, pictures: dict[str, np.ndarray]) -> None:
    folder = ASSETS / "outfits" / name
    folder.mkdir(parents=True, exist_ok=True)
    room = OUTFITS[name][2]
    shell_size = (SHELL_WIDTH, round(SHELL_HEIGHT * (HEIGHT + room) / HEIGHT))
    for state, picture in pictures.items():
        Image.fromarray(picture, "RGBA").resize(shell_size, Image.LANCZOS).save(folder / f"alice-{state}.png")
    print(f"wrote {folder}/alice-*.png")
    if OUTFITS[name][4]:
        scale = SHELL_WIDTH / WIDTH
        for overlay in OVERLAYS:
            source = ASSETS / f"alice-{overlay}.png"
            if source.exists():
                Image.fromarray(recolour(np.asarray(Image.open(source).convert("RGBA")), scale), "RGBA").save(folder / source.name)
        print(f"wrote recoloured face overlays to {folder}")


def main() -> None:
    arguments = sys.argv[1:]
    tries = 0
    if arguments[:1] == ["--try"]:
        tries, arguments = int(arguments[1]), arguments[2:]
    names = arguments or list(OUTFITS)
    if unknown := [n for n in names if n not in OUTFITS]:
        sys.exit(f"Unknown outfit {unknown[0]}. Outfits: {', '.join(OUTFITS)}")
    try:
        urllib.request.urlopen(f"{API}/system_stats", timeout=5).close()
    except OSError as error:
        sys.exit(f"Couldn't reach ComfyUI at {API}: {error}")
    tiles = []
    for name in names:
        if tries:
            for seed in range(OUTFITS[name][3], OUTFITS[name][3] + tries):
                tiles.append((f"{name}, seed {seed}", build(name, seed)[1].resize((416, (HEIGHT + OUTFITS[name][2]) // 2))))
            continue
        pictures, preview = build(name)
        save(name, pictures)
        tiles.append((name, preview.resize((416, (HEIGHT + OUTFITS[name][2]) // 2))))
    sheet(tiles, TRIALS / (f"candidates-{'-'.join(names)}.png" if tries else f"sheet-{'-'.join(names)}.png"))


if __name__ == "__main__":
    main()
