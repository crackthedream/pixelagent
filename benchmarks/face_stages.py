"""Follow faces through every pipeline stage (same grid, same crop) and measure what each stage does to them.
Free: reads saved stage images only (no model calls).

  python benchmarks/face_stages.py OUT.png ASSET_DIR [ASSET_DIR ...]

Per stage: colours in the face area, dark eye pixels, the darkest eye value, and the mean colour distance to the
plain reduction of the concept (each grid cell's exact concept area averaged in linear light)."""
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

REPO = os.path.expanduser("~/pixelagent")
sys.path.insert(0, REPO)
from pixelagent.artist import face_region  # noqa: E402

OUT = sys.argv[1]
CASES = sys.argv[2:]
STAGES = [("pixelize", "04_pixelize/v001/pixel_base.png"), ("palette", "06_rebuild/v001/rebuild.png"),
          ("outline", "07_lines/v001/lines.png"), ("trace (polish)", "09_polish/v001/polish.png"),
          ("model draw", "10_artist/v001/agent/face_measure.png"), ("final (artist step)", "10_artist/v001/artist.png")]


def lin(a):
    a = a / 255.0
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def unlin(a):
    a = np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(np.clip(a, 0, None), 1 / 2.4) - 0.055)
    return np.clip(a * 255, 0, 255)


def lum(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def ideal(concept, geom, W, H, box):
    """Each grid cell's exact concept area, averaged in linear light: the best a plain reduction can do. Also the
    'feature-keeping' variant: a cell takes its darkest quarter when a dark feature covers enough of it."""
    s, m = geom["s"], geom["margin"]
    cx0, cy0 = geom["crop"][0], geom["crop"][1]
    arr = np.asarray(concept.convert("RGBA")).astype(float)
    x, y, w, h = box
    avg = np.zeros((h, w, 3)); feat = np.zeros((h, w, 3)); alpha = np.zeros((h, w))
    for j in range(h):
        for i in range(w):
            X0 = geom["gx0"] + (x + i - m + cx0) * s
            Y0 = geom["gy0"] + (y + j - m + cy0) * s
            blk = arr[max(0, int(round(Y0))):max(0, int(round(Y0 + s))), max(0, int(round(X0))):max(0, int(round(X0 + s)))].reshape(-1, 4)
            blk = blk[blk[:, 3] > 128]
            if not len(blk):
                continue
            alpha[j, i] = 1
            rgb = blk[:, :3]
            avg[j, i] = unlin(lin(rgb).mean(0))
            L = lum(rgb)
            dark = L < np.median(L) - 25
            feat[j, i] = rgb[dark].mean(0) if dark.mean() >= 0.15 else avg[j, i]
    return avg, feat, alpha


def stats(rgb, alpha, eye_rows):
    m = alpha > 0
    px = rgb[m].reshape(-1, 3)
    cols = len({tuple(np.round(p).astype(int)) for p in px})
    L = lum(rgb)
    skinL = np.median(L[m])
    er = L[eye_rows[0]:eye_rows[1]][m[eye_rows[0]:eye_rows[1]]]
    dark = int((er < 0.55 * skinL).sum())
    return cols, dark, float(er.min()) if len(er) else 0


rows = []
report = []
for d in CASES:
    case = os.path.basename(os.path.normpath(d))[:12]
    st = json.load(open(os.path.join(d, "state.json"), encoding="utf-8"))
    lm = json.load(open(os.path.join(d, st["steps"]["materials"]["outputs"]["labelmap"]), encoding="utf-8"))
    geom = json.load(open(os.path.join(d, "steps/04_pixelize/v001/grid_geom.json")))
    W, H = lm["width"], lm["height"]
    fx, fy, fw, fh = face_region(lm["labels"], W, H)
    pad = 3
    box = (max(0, fx - pad), max(0, fy - pad), min(W, fx + fw + pad) - max(0, fx - pad), min(H, fy + fh + pad) - max(0, fy - pad))
    concept = Image.open(os.path.join(d, st["steps"]["concept"]["outputs"]["concept"]))
    avg, feat, alpha = ideal(concept, geom, W, H, box)
    # eye rows from the labelmap (eye / iris materials) inside the box
    ys = [j for j in range(box[3]) for i in range(box[2])
          if (lm["labels"][(box[1] + j) * W + box[0] + i] or "").startswith(("eye", "iris"))]
    eye_rows = (min(ys), max(ys) + 1) if ys else (box[3] // 3, box[3] // 2)
    x, y, w, h = box
    # concept crop of the same area
    s, m = geom["s"], geom["margin"]
    X0 = geom["gx0"] + (x - m + geom["crop"][0]) * s
    Y0 = geom["gy0"] + (y - m + geom["crop"][1]) * s
    ccrop = concept.convert("RGB").crop((int(X0), int(Y0), int(X0 + w * s), int(Y0 + h * s)))
    tiles = [("concept", ccrop)]
    imgs = [("plain reduction", avg), ("feature-keeping reduction", feat)]
    for name, rel in STAGES:
        if not os.path.exists(os.path.join(d, "steps", rel)):   # e.g. no drawing in the reduce finish
            continue
        a = np.asarray(Image.open(os.path.join(d, "steps", rel)).convert("RGBA")).astype(float)[y:y + h, x:x + w]
        imgs.append((name, a[..., :3]))
    for name, rgb in imgs:
        cols, dark, minL = stats(rgb, alpha, eye_rows)
        diff = float(np.abs(rgb - avg)[alpha > 0].mean())
        report.append(f"{case:6s} {name:26s} colours {cols:4d}   dark eye px {dark:3d}   darkest eye L {minL:5.0f}   mean |diff| vs plain {diff:5.1f}")
        im = Image.fromarray(np.uint8(np.clip(rgb, 0, 255)))
        tiles.append((name, im))
    report.append("")
    rows.append((case, tiles, eye_rows))

Z = 9
TW = max(t.size[0] for _, ts, _ in rows for _, t in ts[1:]) * Z
TH = max(t.size[1] for _, ts, _ in rows for _, t in ts[1:]) * Z
sheet = Image.new("RGB", (70 + len(rows[0][1]) * (TW + 10), len(rows) * (TH + 30)), (250, 250, 252))
dr = ImageDraw.Draw(sheet)
for k, (case, tiles, _) in enumerate(rows):
    y0 = k * (TH + 30)
    dr.text((4, y0 + 20), case, fill=(0, 0, 0))
    for i, (name, t) in enumerate(tiles):
        tt = t.resize((TW, round(t.size[1] * TW / t.size[0])), Image.LANCZOS) if i == 0 else t.resize((t.size[0] * Z, t.size[1] * Z), Image.NEAREST)
        x0 = 70 + i * (TW + 10)
        dr.text((x0, y0 + 2), name, fill=(60, 60, 60))
        sheet.paste(tt, (x0, y0 + 16))
sheet.save(OUT)
print("\n".join(report))
print(sheet.size)
