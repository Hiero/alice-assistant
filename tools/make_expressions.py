"""Paints a real face for each of her moods, with a local ComfyUI, from her neutral art:

    alice-mood-sad.png         the whole expression: eyes, cheeks and mouth
    alice-mood-sad-eyes.png    the same without the mouth, shown while her mouth is moving

Only her face from the eyes to the chin is repainted, at three times its size, and laid back over
the original with soft edges, so every picture lines up with the others pixel for pixel. Her bangs
are never touched, and the new skin is tinted to match the old. Like tools/make_faces.py, one set
serves every outfit. It needs ComfyUI running with an Illustrious (SDXL anime) checkpoint:

    uv run --with pillow --with numpy python tools/make_expressions.py            # write the assets
    uv run --with pillow --with numpy python tools/make_expressions.py --try 4 sad pout
        # paint 4 candidates of two moods into art/expressions/ to pick a seed from, writing no assets

COMFYUI_URL (default http://127.0.0.1:8188) and ALICE_ART_CHECKPOINT choose the server and the model;
ALICE_ART_LORA and ALICE_ART_LORA_WEIGHT the LoRA that keeps her look (an empty ALICE_ART_LORA turns it off).
The LoRA file goes in ComfyUI's models/loras/.
"""

import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "art/set/alice_neutral.png"
ASSETS = ROOT / "shell/assets"
TRIALS = ROOT / "art/expressions"
SHELL_SIZE = (480, 702)
API = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
CHECKPOINT = os.environ.get("ALICE_ART_CHECKPOINT", "waiIllustriousSDXL_v170.safetensors")
# Her look comes from this LoRA; it keeps faces and outfits painted in one style. It is a starting point for
# her own design, not a copy of the character it was trained on.
LORA = os.environ.get("ALICE_ART_LORA", "kp_AliceShinMegamiTenseiOfficialArtIllustrious_v1_0.safetensors")
LORA_WEIGHT = float(os.environ.get("ALICE_ART_LORA_WEIGHT", "0.8"))
CHARACTER = "alice (megami tensei), 1girl, solo, blonde hair, long hair, blunt bangs, yellow eyes"

# Measured on the 832 px art.
BOX = (224, 304, 608, 576)  # The crop that is painted, which gives the model her bangs and jaw to go by.
AREA = (270, 356, 580, 540)  # The part that may change: eyes to chin.
MOUTH = (421, 503, 82, 38)  # Centre and radii of the mouth, left out of the -eyes picture.
EYES = [(335, 393, 60, 30), (510, 392, 60, 30)]  # Centre and radii of each eye, never mistaken for hair.
SCALE = 3

BASE = (
    f"{CHARACTER}, portrait, close-up, face, looking at viewer, "
    "white background, simple background, masterpiece, best quality, amazing quality, very aesthetic, absurdres"
)
NEGATIVE = "bad quality, worst quality, worst detail, sketch, censored, signature, watermark, text, teeth, tongue, extra eyes, lipstick"
# Each mood: its tags, how much of the face is repainted (0 keeps it, 1 ignores it), and the seed that was picked.
MOODS = {
    "happy": ("happy, smile, closed mouth, blush", 0.6, 12),
    "excited": ("excited, :d, open mouth, sparkling eyes, wide-eyed, blush, smile", 0.6, 11),
    "surprised": ("surprised, wide-eyed, :o, open mouth, small pupils, raised eyebrows", 0.6, 11),
    "curious": ("curious, :o, parted lips, raised eyebrow, wide-eyed, light smile", 0.6, 13),
    "worried": ("worried, furrowed brow, frown, wavy mouth, nervous, closed mouth", 0.6, 13),
    "sad": ("sad, frown, teary eyes, half-closed eyes, downcast eyes, closed mouth", 0.6, 11),
    "pout": ("pout, pouting, puffed cheeks, :<, annoyed, glaring, looking to the side, blush, closed mouth", 0.7, 23),
    "embarrassed": ("embarrassed, full-face blush, wavy mouth, looking to the side, nose blush, closed mouth", 0.6, 11),
    "sleepy": ("sleepy, half-closed eyes, narrowed eyes, drowsy, heavy eyelids, parted lips, tired", 0.78, 25),
}


def call(path: str, payload: dict) -> dict:
    request = urllib.request.Request(API + path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as reply:
        return json.load(reply)


def upload(name: str, image: Image.Image) -> None:
    """Puts a picture in ComfyUI's input folder through its API, so the server can be anywhere."""
    data = io.BytesIO()
    image.save(data, "PNG")
    edge = uuid.uuid4().hex
    body = (
        f'--{edge}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n'.encode()
        + data.getvalue()
        + f'\r\n--{edge}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n--{edge}--\r\n'.encode()
    )
    request = urllib.request.Request(f"{API}/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={edge}"})
    urllib.request.urlopen(request, timeout=30).close()


def loaders() -> tuple[dict, list]:
    """The checkpoint with her LoRA on it, and the model to sample with. The text encoder is ["2", 0], the VAE ["1", 2]."""
    graph = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CHECKPOINT}}}
    model, clip = ["1", 0], ["1", 1]
    if LORA:
        graph["l"] = {"class_type": "LoraLoader", "inputs": {
            "model": model, "clip": clip, "lora_name": LORA, "strength_model": LORA_WEIGHT, "strength_clip": LORA_WEIGHT,
        }}
        model, clip = ["l", 0], ["l", 1]
    graph["2"] = {"class_type": "CLIPSetLastLayer", "inputs": {"clip": clip, "stop_at_clip_layer": -2}}
    return graph, model


def paint(tags: str, denoise: float, seed: int, source: str = "alice_face.png", mask: str = "alice_face_mask.png") -> Image.Image:
    graph, model = loaders()
    graph |= {
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": f"{BASE}, {tags}"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": NEGATIVE}},
        "5": {"class_type": "LoadImage", "inputs": {"image": source}},
        "6": {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}},
        "7": {"class_type": "VAEEncode", "inputs": {"pixels": ["5", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["7", 0], "mask": ["6", 0]}},
        "9": {"class_type": "KSampler", "inputs": {
            "model": model, "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["8", 0], "seed": seed,
            "steps": 26, "cfg": 5.5, "sampler_name": "euler_ancestral", "scheduler": "normal", "denoise": denoise,
        }},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["1", 2]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "alice_face/try"}},
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
            return Image.open(io.BytesIO(reply.read())).convert("RGB")


def soft(mask: Image.Image, radius: float) -> np.ndarray:
    return np.asarray(mask.filter(ImageFilter.GaussianBlur(radius))).astype(np.float32) / 255


def is_skin(rgb: np.ndarray) -> np.ndarray:
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (0.299 * red + 0.587 * green + 0.114 * blue > 205) & (red - blue < 60)


class Face:
    """Her neutral face, and what is needed to lay a repainted one back over it."""

    def __init__(self, source: Path = SOURCE) -> None:
        if not source.exists():
            sys.exit(f"missing {source}")
        self.art = Image.open(source).convert("RGBA")
        flat = Image.new("RGB", self.art.size, "white")
        flat.paste(self.art, mask=self.art.getchannel("A"))
        self.crop = flat.crop(BOX)
        self.rgb = np.asarray(self.crop).astype(np.float32)
        size = (self.crop.width * SCALE, self.crop.height * SCALE)
        area = Image.new("L", self.crop.size, 0)
        ImageDraw.Draw(area).rounded_rectangle([AREA[0] - BOX[0], AREA[1] - BOX[1], AREA[2] - BOX[0], AREA[3] - BOX[1]], radius=60, fill=255)
        try:
            upload("alice_face.png", self.crop.resize(size, Image.LANCZOS))
            upload("alice_face_mask.png", area.resize(size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(12)).convert("RGB"))
        except OSError as error:
            sys.exit(f"Couldn't reach ComfyUI at {API}: {error}")
        # Bangs stay on top: the blonde of the original is never replaced.
        red, green, blue = self.rgb[..., 0], self.rgb[..., 1], self.rgb[..., 2]
        luma = 0.299 * red + 0.587 * green + 0.114 * blue
        hair = (red - blue > 45) & (green - blue > 25) & (luma > 150)
        # Her gold irises are as yellow as her hair; inside the eyes nothing is hair.
        yy, xx = np.mgrid[0:self.crop.height, 0:self.crop.width]
        for cx, cy, rx, ry in EYES:
            hair &= ((xx - cx + BOX[0]) / rx) ** 2 + ((yy - cy + BOX[1]) / ry) ** 2 > 1
        hair = Image.fromarray((hair * 255).astype(np.uint8))
        self.cover = soft(area, 10) * (1 - soft(hair, 1.2))
        mouth = Image.new("L", self.crop.size, 0)
        cx, cy, rx, ry = MOUTH
        ImageDraw.Draw(mouth).ellipse([cx - rx - BOX[0], cy - ry - BOX[1], cx + rx - BOX[0], cy + ry - BOX[1]], fill=255)
        self.mouth = soft(mouth, 8)

    def painted(self, mood: str, seed: int | None = None) -> np.ndarray:
        tags, denoise, picked = MOODS[mood]
        new = np.asarray(paint(tags, denoise, picked if seed is None else seed).resize(self.crop.size, Image.LANCZOS)).astype(np.float32)
        # Repainting drifts the skin tone; one shift per colour channel brings it back to hers.
        both = is_skin(new) & is_skin(self.rgb) & (self.cover > 0.5)
        if both.sum() > 500:
            new += np.median((self.rgb - new)[both], axis=0)
        return new.clip(0, 255)

    def preview(self, new: np.ndarray) -> Image.Image:
        cover = self.cover[..., None]
        return Image.fromarray((new * cover + self.rgb * (1 - cover)).astype(np.uint8))

    def save(self, name: str, new: np.ndarray, cover: np.ndarray) -> None:
        layer = np.zeros((self.art.height, self.art.width, 4), np.uint8)
        layer[BOX[1]:BOX[3], BOX[0]:BOX[2], :3] = new.astype(np.uint8)
        layer[BOX[1]:BOX[3], BOX[0]:BOX[2], 3] = (cover * 255).astype(np.uint8)
        layer[..., 3] = (layer[..., 3].astype(np.float32) * np.asarray(self.art.getchannel("A")) / 255).astype(np.uint8)  # only where she is drawn
        Image.fromarray(layer, "RGBA").resize(SHELL_SIZE, Image.LANCZOS).save(ASSETS / name)
        print(f"wrote {ASSETS / name}")


def sheet(tiles: list[tuple[str, Image.Image]], path: Path, columns: int = 4) -> None:
    width, height = tiles[0][1].size
    rows = -(-len(tiles) // columns)
    page = Image.new("RGB", (columns * width, rows * (height + 16)), "white")
    draw = ImageDraw.Draw(page)
    for i, (label, tile) in enumerate(tiles):
        x, y = (i % columns) * width, (i // columns) * (height + 16)
        page.paste(tile, (x, y + 16))
        draw.text((x + 4, y + 2), label, fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    page.save(path)
    print(f"wrote {path}")


def main() -> None:
    arguments = sys.argv[1:]
    tries = 0
    if arguments[:1] == ["--try"]:
        tries, arguments = int(arguments[1]), arguments[2:]
    moods = arguments or list(MOODS)
    if unknown := [m for m in moods if m not in MOODS]:
        sys.exit(f"Unknown mood {unknown[0]}. Moods: {', '.join(MOODS)}")
    face = Face()
    tiles = [("neutral", face.crop)]
    for mood in moods:
        if tries:
            for seed in range(MOODS[mood][2], MOODS[mood][2] + tries):
                tiles.append((f"{mood}, seed {seed}", face.preview(face.painted(mood, seed))))
            continue
        new = face.painted(mood)
        face.save(f"alice-mood-{mood}.png", new, face.cover)
        face.save(f"alice-mood-{mood}-eyes.png", new, face.cover * (1 - face.mouth))
        tiles.append((mood, face.preview(new)))
    sheet(tiles, TRIALS / ("candidates.png" if tries else "sheet.png"))


if __name__ == "__main__":
    main()
