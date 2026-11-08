"""Procedural stand-in 'concept art' for dry runs and benchmarks: a soft-shaded character lit from the
top-left, painted at high resolution. Not art, just realistic input for the pixelization pipeline."""
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

PALETTES = [
    {"skin": (238, 196, 160), "hair": (122, 64, 44), "coat": (52, 92, 160), "pants": (60, 52, 70), "boots": (90, 58, 40), "eyes": (40, 60, 120)},
    {"skin": (226, 176, 140), "hair": (232, 206, 120), "coat": (170, 48, 60), "pants": (70, 70, 84), "boots": (52, 40, 40), "eyes": (40, 110, 70)},
    {"skin": (190, 140, 110), "hair": (40, 36, 52), "coat": (70, 130, 90), "pants": (110, 92, 70), "boots": (60, 46, 36), "eyes": (110, 60, 30)},
    {"skin": (245, 210, 185), "hair": (200, 90, 140), "coat": (235, 235, 240), "pants": (60, 60, 110), "boots": (120, 80, 140), "eyes": (150, 60, 160)},
]


def _shade(mask, color, light=(-0.6, -0.8)):
    """Fill a boolean mask with `color`, lit like a rounded volume from the top-left."""
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return np.zeros((h, w, 4), np.float32)
    cx, cy = xs.mean(), ys.mean()
    rx, ry = max(1.0, (xs.max() - xs.min()) / 2), max(1.0, (ys.max() - ys.min()) / 2)
    yy, xx = np.mgrid[0:h, 0:w]
    nx, ny = (xx - cx) / rx, (yy - cy) / ry
    nz = np.sqrt(np.clip(1 - nx ** 2 - ny ** 2, 0, 1))
    lx, ly = light
    lz = 0.6
    ln = np.sqrt(lx * lx + ly * ly + lz * lz)
    lam = np.clip((nx * lx + ny * ly + nz * lz) / ln, 0, 1)
    k = 0.45 + 0.75 * lam
    out = np.zeros((h, w, 4), np.float32)
    out[..., :3] = np.clip(np.array(color, np.float32)[None, None, :] * k[..., None], 0, 255)
    out[..., 3] = mask * 255.0
    return out


def draw_character(seed=0, size=768, background=None):
    rnd = random.Random(seed)
    pal = PALETTES[seed % len(PALETTES)]
    W = H = size
    s = size / 768.0
    parts = []

    def ell(box):
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).ellipse([v * s for v in box], fill=255)
        return np.asarray(m) > 0

    def poly(pts):
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).polygon([(x * s, y * s) for x, y in pts], fill=255)
        return np.asarray(m) > 0

    def rrect(box, r):
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).rounded_rectangle([v * s for v in box], radius=r * s, fill=255)
        return np.asarray(m) > 0

    lean = rnd.uniform(-12, 12)
    parts.append(("boots", rrect((300, 640, 370, 712), 18) | rrect((400, 640, 470, 712), 18)))
    parts.append(("pants", rrect((305, 470, 375, 660), 22) | rrect((395, 470, 465, 660), 22)))
    parts.append(("coat", poly([(280, 300), (490, 300), (520 + lean, 520), (250 + lean, 520)])))
    parts.append(("coat", rrect((225, 300, 285, 500), 28) | rrect((485, 300, 545, 500), 28)))  # sleeves
    parts.append(("skin", ell((222, 480, 282, 540)) | ell((488, 480, 548, 540))))                # hands
    parts.append(("skin", ell((280, 100, 490, 320))))                                            # head
    parts.append(("hair", ell((265, 70, 505, 230)) | poly([(270, 150), (300, 250), (330, 170), (380, 220), (420, 160), (470, 240), (500, 150)])))
    parts.append(("eyes", ell((330, 215, 362, 255)) | ell((408, 215, 440, 255))))

    canvas = np.zeros((H, W, 4), np.float32)
    for name, mask in parts:
        layer = _shade(mask, pal[name])
        a = layer[..., 3:4] / 255.0
        canvas[..., :3] = layer[..., :3] * a + canvas[..., :3] * (1 - a)
        canvas[..., 3:4] = np.maximum(canvas[..., 3:4], layer[..., 3:4])
    # eye highlights
    for cx in (338, 416):
        m = ell((cx, 222, cx + 10, 232))
        canvas[m] = (255, 255, 255, 255)
    im = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGBA")
    rgb = im.filter(ImageFilter.GaussianBlur(1.5 * s))
    out = np.asarray(rgb).copy()
    out[..., 3] = np.asarray(im)[..., 3]
    im = Image.fromarray(out, "RGBA")
    if background is not None:
        bg = Image.new("RGBA", im.size, tuple(background) + (255,))
        bg.alpha_composite(im)
        im = bg
    return im, pal
