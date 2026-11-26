"""Draws the overlays that give her expressions, laid over any of her faces by the shell:

    alice-blink.png      closed eyes, for blinking
    alice-happy.png      closed, smiling ^^ eyes
    alice-halflid.png    drooping lids, for sad, sleepy and shy
    alice-blush.png      rosy cheeks

Lids are filled from the skin around each eye and lash lines are drawn across. Positions are
tuned for the current art (art/set/alice_neutral.png, 832x1216). Every outfit shares her face,
so one set serves them all. Run from the repository:

    uv run --with opencv-python-headless --with numpy python tools/make_faces.py
"""

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "art/set/alice_neutral.png"
ASSETS = ROOT / "shell/assets"
SHELL_SIZE = (480, 702)

# Ellipse around each eye (centre x, centre y, radius x, radius y), and the outer and inner
# corners a closed lash runs between, on the 832 px art.
EYES = [
    dict(ellipse=(335, 390, 60, 33), outer=(279, 390), inner=(369, 401)),
    dict(ellipse=(510, 389, 60, 33), outer=(565, 388), inner=(481, 398)),
]
# Centre and radii of each blushing cheek.
CHEEKS = [(322, 440, 40, 17), (522, 440, 40, 17)]
LASH_INK = np.array([50, 58, 96], np.float32)  # dark brown, BGR
BLUSH_INK = np.array([150, 140, 250], np.float32)  # rose, BGR
HATCH_INK = np.array([110, 90, 215], np.float32)


class Art:
    def __init__(self) -> None:
        art = cv2.imread(str(SOURCE), cv2.IMREAD_UNCHANGED)
        if art is None:
            sys.exit(f"missing {SOURCE}")
        self.alpha = art[..., 3].astype(np.float32) / 255
        self.image = art[..., :3].astype(np.float32)
        blue, green, red = self.image[..., 0], self.image[..., 1], self.image[..., 2]
        self.luma = 0.299 * red + 0.587 * green + 0.114 * blue
        height, width = self.luma.shape
        self.yy, self.xx = np.mgrid[0:height, 0:width].astype(np.float32)
        # Bangs stay on top. Her gold irises are as yellow as her hair, so the middle of each eye never counts.
        self.hair = (red - blue > 45) & (green - blue > 25) & (self.luma > 150)
        for eye in EYES:
            cx, cy, rx, ry = eye["ellipse"]
            self.hair &= ((self.xx - cx) / (rx * 0.8)) ** 2 + ((self.yy - cy - 4) / (ry * 0.7)) ** 2 > 1
        # Her usual skin, for any part of a lid with no plain skin near enough to copy.
        self.skin = np.median(self.image[(self.luma > 225) & ~self.hair & (self.alpha > 0.99) & (self.yy > 400) & (self.yy < 520)], axis=0)

    def lids(self, upper_part: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
        """Skin over the eyes. upper_part < 1 covers only that share of each eye, from the top."""
        colour = self.image.copy()
        cover = np.zeros(self.luma.shape, np.float32)
        for eye in EYES:
            cx, cy, rx, ry = eye["ellipse"]
            distance = ((self.xx - cx) / rx) ** 2 + ((self.yy - cy) / ry) ** 2
            region = (distance <= 1) & ~self.hair
            if upper_part < 1:
                region &= self.yy <= self.lid_line(eye, upper_part)
            region = cv2.dilate(region.astype(np.float32), np.ones((3, 3), np.uint8))
            lid = cv2.GaussianBlur(region, (0, 0), 1.6)
            # Filled from the skin around the eye only, so blush and light carry across the lid.
            known = ((distance > 1.25) & (distance < 4) & ~self.hair & (self.luma > 200)).astype(np.float32)
            near = cv2.GaussianBlur(known, (0, 0), 14)[..., None]
            skin = cv2.GaussianBlur(self.image * known[..., None], (0, 0), 14) / (near + 1e-4)
            sure = np.clip(near / 0.15, 0, 1)
            skin = skin * sure + self.skin * (1 - sure)
            depth = np.clip((self.yy - (cy - ry)) / (2 * ry), 0, 1)[..., None]
            colour = colour * (1 - lid[..., None]) + skin * (0.975 + 0.025 * depth) * lid[..., None]
            cover = np.maximum(cover, lid)
        return colour, cover

    def lid_line(self, eye: dict, upper_part: float) -> np.ndarray:
        """Where a half-closed lid's edge crosses each column: a gentle curve over the eye."""
        cx, cy, rx, ry = eye["ellipse"]
        top = cy - ry + 2 * ry * upper_part
        return top + 3 * (((self.xx - cx) / rx) ** 2)


def stroke(lash: np.ndarray, start: tuple[float, float], end: tuple[float, float], sag: float, width: float) -> None:
    """A lash line from start to end, bowed down by sag pixels (up when negative), thickest mid-way."""
    (sx, sy), (ex, ey) = start, end
    steps = 48

    def point(t: float) -> tuple[int, int]:
        return int((sx + (ex - sx) * t) * 8), int((sy + (ey - sy) * t + sag * 4 * t * (1 - t)) * 8)

    for k in range(steps):
        t = k / steps
        thickness = width * (0.4 + 0.6 * np.sin(np.pi * min(1.0, (t + 0.05) * 1.1)))
        cv2.line(lash, point(t), point((k + 1) / steps), 1.0, max(1, int(thickness)), cv2.LINE_AA, shift=3)


def ink(colour: np.ndarray, alpha: np.ndarray, lash: np.ndarray, tint: np.ndarray = LASH_INK) -> tuple[np.ndarray, np.ndarray]:
    lash = cv2.GaussianBlur(lash, (0, 0), 0.5)
    return colour * (1 - lash[..., None]) + tint * lash[..., None], np.maximum(alpha, lash)


def save(name: str, art: Art, colour: np.ndarray, alpha: np.ndarray) -> None:
    alpha = alpha * art.alpha  # only where she is drawn
    # Premultiplied while scaling down, so the transparent edges don't darken.
    premultiplied = np.dstack([colour * alpha[..., None], alpha * 255])
    small = cv2.resize(premultiplied, SHELL_SIZE, interpolation=cv2.INTER_AREA)
    a = small[..., 3:4] / 255
    rgb = np.where(a > 0, small[..., :3] / np.maximum(a, 1e-4), 0)
    target = ASSETS / name
    cv2.imwrite(str(target), np.dstack([np.clip(rgb, 0, 255), np.clip(small[..., 3:4], 0, 255)]).astype(np.uint8))
    print(f"wrote {target}")


def blink(art: Art) -> None:
    colour, alpha = art.lids()
    lash = np.zeros(alpha.shape, np.float32)
    for eye in EYES:
        (ox, oy), (ix, iy) = eye["outer"], eye["inner"]
        stroke(lash, (ox, oy), (ix, iy), 8, 4.6)
        flick = -1 if ox < ix else 1
        cv2.line(lash, (int(ox * 8), int(oy * 8)), (int((ox + flick * 7) * 8), int((oy - 3) * 8)), 1.0, 2, cv2.LINE_AA, shift=3)
    save("alice-blink.png", art, *ink(colour, alpha, lash))


def happy(art: Art) -> None:
    colour, alpha = art.lids()
    lash = np.zeros(alpha.shape, np.float32)
    for eye in EYES:
        (ox, oy), (ix, iy) = eye["outer"], eye["inner"]
        # Arched up like ^, sitting a little higher than a closed eye.
        stroke(lash, (ox + (ix - ox) * 0.05, oy + 2), (ix, iy - 2), -17, 5.0)
    save("alice-happy.png", art, *ink(colour, alpha, lash))


def halflid(art: Art) -> None:
    share = 0.48
    colour, alpha = art.lids(upper_part=share)
    lash = np.zeros(alpha.shape, np.float32)
    for eye in EYES:
        cx, cy, rx, ry = eye["ellipse"]
        line = art.lid_line(eye, share)
        # The lid's edge, across the eye where the eye is actually drawn.
        columns = range(int(cx - rx * 0.92), int(cx + rx * 0.92))
        points = [(x, float(line[0, x])) for x in columns]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            t = (x0 - (cx - rx)) / (2 * rx)
            thickness = 4.2 * (0.5 + 0.5 * np.sin(np.pi * t))
            cv2.line(lash, (int(x0 * 8), int(y0 * 8)), (int(x1 * 8), int(y1 * 8)), 1.0, max(1, int(thickness)), cv2.LINE_AA, shift=3)
    save("alice-halflid.png", art, *ink(colour, alpha, lash))


def blush(art: Art) -> None:
    alpha = np.zeros(art.luma.shape, np.float32)
    for cx, cy, rx, ry in CHEEKS:
        distance = ((art.xx - cx) / rx) ** 2 + ((art.yy - cy) / ry) ** 2
        alpha = np.maximum(alpha, 0.5 * np.exp(-2.2 * distance))
    colour = np.broadcast_to(BLUSH_INK, art.image.shape).copy()
    # A few short diagonal strokes over each cheek, as anime blushes have.
    hatch = np.zeros(alpha.shape, np.float32)
    for cx, cy, rx, _ in CHEEKS:
        for offset in (-0.45, -0.15, 0.15, 0.45):
            x = cx + offset * rx
            cv2.line(hatch, (int((x + 4) * 8), int((cy - 7) * 8)), (int((x - 4) * 8), int((cy + 7) * 8)), 1.0, 2, cv2.LINE_AA, shift=3)
    colour, alpha = ink(colour, alpha, hatch * 0.55, HATCH_INK)
    save("alice-blush.png", art, colour, alpha)


def main() -> None:
    art = Art()
    for make in (blink, happy, halflid, blush):
        make(art)


if __name__ == "__main__":
    main()
