"""Sprites designed AS pixel art (the native finish): the image model draws the character as a hand-pixelled sprite
with every sprite pixel shown as a crisp square block; this module finds that block grid and reads the sprite back
at 1x, pixel for pixel.

Why: shrinking a painted concept (the reduce finish) gives soft gradients and a painted face shrunk down; hand-made
sprites in the target style (Alice in Cradle) are built from deliberate clusters and pixel-designed faces
(docs/FACE_ROOT_CAUSE.md section 7)."""
import numpy as np
from PIL import Image


def _period(profile, lo=4, hi=16):
    """Block size (fractional) and phase for a 1-D edge profile. For each candidate period the edge energy is binned
    by phase, one bin per image pixel of the period; at the true block size all block edges fall in one bin. Every
    multiple of the block size scores about as well (6 and 12 both ~6) and smaller periods score lower, so the
    SMALLEST period close to the best score wins, refined to the best value within half a pixel of it."""
    n = len(profile)
    pos = np.arange(n)
    found = []
    for p10 in range(lo * 10, hi * 10 + 1):
        p = p10 / 10.0
        nb = int(np.ceil(p))
        bins = np.minimum(nb - 1, np.floor(np.mod(pos, p) / p * nb).astype(int))
        acc = np.bincount(bins, weights=profile, minlength=nb)
        found.append((acc.max() / (acc.mean() + 1e-6), p, (int(acc.argmax()) + 0.5) / nb * p))
    top = max(f[0] for f in found)
    first = min(f[1] for f in found if f[0] >= 0.85 * top)
    _, p, phase = max((f for f in found if abs(f[1] - first) <= 0.5), key=lambda f: f[0])
    return p, phase


def _autocorr_period(profile, lo, hi):
    """Mean block size from the edge profile's autocorrelation: the lag k (within lo..hi) whose multiples k, 2k, 3k...
    carry the most correlation, refined to a tenth of a pixel. Robust to the slight drift of an image model's
    'pixels' (comb filters on a fixed period score weakly there: 1.2-1.5 instead of ~8 on clean blocks)."""
    pr = profile - profile.mean()
    ac = np.correlate(pr, pr, "full")[len(pr) - 1:]
    ac = ac / (ac[0] + 1e-9)
    n = len(ac)

    def at(x):
        i = int(x)
        return ac[i] + (ac[i + 1] - ac[i]) * (x - i) if i + 1 < n else 0.0
    best = (-1e9, None)
    for p10 in range(int(lo * 10), int(hi * 10) + 1):
        q = p10 / 10.0
        ks = [k for k in range(1, 8) if k * q < n - 1]
        score = sum(at(k * q) for k in ks) / max(1, len(ks))
        if score > best[0]:
            best = (score, q)
    return best[1]


def _walk(profile, p, length):
    """Block edges along one axis, following the drift: start at the strongest comb phase, then step one block at a
    time and snap each edge to the strongest edge within +-30% of a block (flat areas keep the plain step)."""
    pos = np.arange(len(profile))
    nb = max(2, int(np.ceil(p)))
    bins = np.minimum(nb - 1, np.floor(np.mod(pos, p) / p * nb).astype(int))
    acc = np.bincount(bins, weights=profile, minlength=nb)
    start = ((int(acc.argmax()) + 0.5) / nb * p + 1) % p       # an edge at phase o lies between o and o+1
    strong = np.median(profile) * 2.0 + 1e-6
    edges = [start]
    while edges[-1] + p < length:
        guess = edges[-1] + p
        lo_, hi_ = int(round(guess - 0.3 * p)), int(round(guess + 0.3 * p))
        win = profile[max(0, lo_ - 1):max(0, hi_ - 1) + 1]       # profile[i] is the edge between i and i+1
        if len(win) and win.max() >= strong:
            edges.append(float(max(0, lo_ - 1) + int(win.argmax()) + 1))
        else:
            edges.append(guess)
    return edges


def find_grid(img, expected=None):
    """The block grid of an image of crisp blocks: {'p': mean block size, 'x0', 'y0': the first block edges, 'xs',
    'ys': every block edge (following the drift of an image model's pixels)}. `expected`: the block size asked for;
    the search stays within 0.7-1.45x of it (rules out half and double: image models sometimes draw small details at
    half size, ref02 and ref08 read back at twice the size, 2026-11-10)."""
    a = np.asarray(img.convert("RGB")).astype(np.int32)
    H, W = a.shape[:2]
    lo, hi = (max(3.0, expected * 0.7), expected * 1.45) if expected else (4.0, 16.0)
    profx = np.abs(np.diff(a, axis=1)).sum(-1).sum(0).astype(float)
    profy = np.abs(np.diff(a, axis=0)).sum(-1).sum(1).astype(float)
    px, py = _autocorr_period(profx, lo, hi), _autocorr_period(profy, lo, hi)
    p = (px + py) / 2.0                                   # square blocks
    xs, ys = _walk(profx, p, W), _walk(profy, p, H)
    return {"p": round(p, 3), "x0": round(xs[0], 3), "y0": round(ys[0], 3),
            "xs": [round(v, 2) for v in xs], "ys": [round(v, 2) for v in ys]}


def read_blocks(img, grid):
    """Each block's colour: the dominant colour of its inner half (edges between blocks may be blurred)."""
    a = np.asarray(img.convert("RGB")).astype(np.int32)
    H, W = a.shape[:2]
    p = grid["p"]
    xs = grid.get("xs") or [grid["x0"] + i * p for i in range(int((W - grid["x0"]) // p) + 1)]
    ys = grid.get("ys") or [grid["y0"] + j * p for j in range(int((H - grid["y0"]) // p) + 1)]
    out = np.zeros((len(ys) - 1, len(xs) - 1, 3), np.uint8)
    for j in range(len(ys) - 1):
        cy, ry = (ys[j] + ys[j + 1]) / 2, max(1, int((ys[j + 1] - ys[j]) * 0.25))
        for i in range(len(xs) - 1):
            cx, rx = (xs[i] + xs[i + 1]) / 2, max(1, int((xs[i + 1] - xs[i]) * 0.25))
            y0, x0 = int(round(cy)), int(round(cx))
            blk = a[max(0, y0 - ry):y0 + ry + 1, max(0, x0 - rx):x0 + rx + 1].reshape(-1, 3)
            q = blk // 4
            vals, counts = np.unique(q, axis=0, return_counts=True)
            sel = np.all(q == vals[counts.argmax()], axis=1)
            out[j, i] = blk[sel].mean(0).round().astype(np.uint8)
    return out


def cut_background(cells, tol=24):
    """Mask of the figure: the flat background colour (the border's most common colour), and every cell connected to
    the border through near-background cells, is not part of it."""
    H, W = cells.shape[:2]
    border = np.concatenate([cells[0], cells[-1], cells[:, 0], cells[:, -1]]).astype(int)
    vals, counts = np.unique(border, axis=0, return_counts=True)
    bgc = vals[counts.argmax()]
    near = np.abs(cells.astype(int) - bgc).sum(-1) <= tol
    bg = np.zeros((H, W), bool)
    stack = [(y, x) for y in range(H) for x in (0, W - 1)] + [(y, x) for x in range(W) for y in (0, H - 1)]
    while stack:
        y, x = stack.pop()
        if 0 <= y < H and 0 <= x < W and not bg[y, x] and near[y, x]:
            bg[y, x] = True
            stack += [(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)]
    return ~bg


def sprite_from_pixel_art(img, margin=2, grid=None, expected=None):
    """An image of a pixel-art sprite drawn as crisp blocks -> (the sprite at 1x as RGBA with `margin` transparent
    pixels around it, geometry in the pipeline's convention: sprite pixel (X, Y) comes from the image area starting
    at gx0 + (X - margin + crop_x) * s, gy0 + (Y - margin + crop_y) * s). `grid` reuses a known grid (an area
    revision keeps the previous one)."""
    img = img.convert("RGBA")
    if img.getchannel("A").getextrema()[0] < 255:          # transparent background: put it on flat grey
        flat = Image.new("RGBA", img.size, (222, 222, 226, 255))
        flat.alpha_composite(img)
        img = flat
    grid = grid or find_grid(img, expected)
    cells = read_blocks(img, grid)
    fig = cut_background(cells)
    ys, xs = np.nonzero(fig)
    if not len(ys):
        raise ValueError("no figure found in the pixel-art image")
    bx0, by0, bx1, by1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    rgba = np.zeros((by1 - by0, bx1 - bx0, 4), np.uint8)
    rgba[..., :3] = cells[by0:by1, bx0:bx1]
    rgba[..., 3] = np.where(fig[by0:by1, bx0:bx1], 255, 0)
    small = Image.fromarray(rgba, "RGBA")
    canvas = Image.new("RGBA", (small.width + 2 * margin, small.height + 2 * margin), (0, 0, 0, 0))
    canvas.paste(small, (margin, margin))
    geom = {"s": grid["p"], "gx0": grid["x0"], "gy0": grid["y0"], "crop": [bx0, by0, bx1, by1], "margin": margin,
            "size": [canvas.width, canvas.height], "source_size": list(img.size), "grid": grid}
    return canvas, geom


def enlarge(sprite, block=8, size=(1024, 1536), bg=(222, 222, 226)):
    """A 1x sprite shown as crisp blocks on a flat background (the stand-in image model's 'pixel art' in test runs,
    and a check for find_grid)."""
    big = sprite.convert("RGBA").resize((sprite.width * block, sprite.height * block), Image.NEAREST)
    out = Image.new("RGBA", size, bg + (255,))
    out.alpha_composite(big, ((size[0] - big.width) // 2 // block * block + 3, (size[1] - big.height) // 2 // block * block + 5))
    return out.convert("RGB")
