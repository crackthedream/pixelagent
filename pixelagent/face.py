"""Faces at sprite size.

Root cause found on six real 96-px characters: the trace (an averaging downscale of the concept) dissolves eyes,
brows and mouth into skin, because each feature is only 1-3 sprite pixels. The artist then had no exact-size guide
for the face and invented one from the high-resolution concept, with uneven results.

face_trace() rebuilds the trace inside the face from the concept with a feature-keeping downscale: for each sprite
cell, if a clearly darker sub-feature (eye, lash line, brow, mouth) covers enough of the cell, the cell takes that
feature's colour; otherwise the cell's typical colour (stray outliers ignored). Only cells labelled as face
materials change, so hair strands and clothing keep the normal trace. Colours are snapped to the trace palette."""
import numpy as np
from PIL import Image

FACE_WORDS = ("skin", "face", "eye", "cheek", "mouth", "blush", "tongue", "lip", "nose")


def is_face_material(m):
    """'skin', 'eyes_and_mouth', 'face_shadow' yes; 'shield_face', 'clock_face' no (an object's face), no hair."""
    n = m.lower().replace("-", "_").replace(" ", "_")
    return n.split("_")[0].startswith(FACE_WORDS) and "hair" not in n


def _lum(rgb):
    return (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]) / 255.0


def _cell_pixels(concept_arr, geom, i, j):
    """Opaque concept pixels that map onto sprite cell (i, j) via the pixel-grid geometry of the pixelize step."""
    s, m = geom["s"], geom["margin"]
    cx, cy = geom["crop"][0], geom["crop"][1]
    x0 = geom["gx0"] + (i - m + cx) * s
    y0 = geom["gy0"] + (j - m + cy) * s
    blk = concept_arr[max(0, int(round(y0))):max(0, int(round(y0 + s))),
                      max(0, int(round(x0))):max(0, int(round(x0 + s)))].reshape(-1, 4)
    return blk[blk[:, 3] > 128][:, :3]


def feature_colour(px, dark_t=0.10, min_frac=0.15):
    """The colour a sprite cell should take from its concept pixels, keeping small dark features."""
    if len(px) == 0:
        return None
    L = _lum(px)
    med = np.median(L)
    dark = L < med - dark_t
    if dark.mean() >= min_frac:  # an eye, lash line, brow or mouth inside this cell
        return px[dark].mean(0)
    keep = np.abs(L - med) <= dark_t
    return px[keep].mean(0) if keep.any() else px.mean(0)


def _snap(rgb, palette):
    pal = np.asarray(palette, dtype=np.float32)
    d = (((pal - np.asarray(rgb, dtype=np.float32)) * np.array([0.3, 0.59, 0.11])) ** 2).sum(-1)
    return tuple(int(v) for v in pal[int(d.argmin())])


def face_trace(trace, concept, geom, labels, width, box, pad=2):
    """The trace with its face cells rebuilt from the concept (see module doc). Returns (image, changed_cells)."""
    out = trace.convert("RGBA").copy()
    px = np.asarray(out).reshape(-1, 4)
    palette = sorted({tuple(int(v) for v in p[:3]) for p in px[px[:, 3] > 0]})
    if not palette:
        return out, 0
    arr = np.asarray(concept.convert("RGBA"), dtype=np.float32)
    x, y, w, h = box
    height = len(labels) // width
    changed = 0
    for j in range(max(0, y - pad), min(height, y + h + pad)):
        for i in range(max(0, x - pad), min(width, x + w + pad)):
            m = labels[j * width + i]
            if not m or not is_face_material(m):
                continue
            c = feature_colour(_cell_pixels(arr, geom, i, j))
            if c is None:
                continue
            new = (*_snap(c, palette), 255)
            if out.getpixel((i, j)) != new:
                out.putpixel((i, j), new)
                changed += 1
    return out, changed


def reset_face(sprite, trace, labels, width, box, pad=2):
    """The sprite with its face cells put back to the trace (a neutral start for comparing face passes)."""
    out = sprite.convert("RGBA").copy()
    tr = trace.convert("RGBA")
    x, y, w, h = box
    height = len(labels) // width
    for j in range(max(0, y - pad), min(height, y + h + pad)):
        for i in range(max(0, x - pad), min(width, x + w + pad)):
            m = labels[j * width + i]
            if m and is_face_material(m):
                out.putpixel((i, j), tr.getpixel((i, j)))
    return out
