"""High-resolution image work for the pipeline (numpy + Pillow): background removal, cropping,
linear-light area downscaling with sub-pixel grid offsets, and contact sheets."""
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def load_rgba(path):
    im = Image.open(path)
    im.load()
    return im.convert("RGBA")


def has_transparency(im):
    if im.mode != "RGBA":
        return False
    a = np.asarray(im)[..., 3]
    return a.min() < 250


def remove_background(im, tolerance=28):
    """Make a plain, edge-connected background transparent (for concepts on flat backgrounds).
    Images that already have transparency are returned unchanged."""
    if has_transparency(im):
        return im.copy()
    rgb = im.convert("RGB")
    arr = np.asarray(rgb).astype(np.int16)
    h, w = arr.shape[:2]
    sentinel = (1, 254, 3)
    marked = rgb.copy()
    border = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    for xy in border:
        if tuple(marked.getpixel(xy)) != sentinel:
            ImageDraw.floodfill(marked, xy, sentinel, thresh=tolerance)
    m = np.asarray(marked)
    bg = np.all(m == sentinel, axis=-1) & ~np.all(arr == sentinel, axis=-1)
    out = np.asarray(im).copy()
    out[..., 3] = np.where(bg, 0, 255)
    return Image.fromarray(out, "RGBA")


def content_bbox(im, alpha_threshold=128):
    a = np.asarray(im)[..., 3] >= alpha_threshold
    ys, xs = np.nonzero(a)
    if len(xs) == 0:
        raise ValueError("image has no opaque content")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def crop_to_content(im, pad_frac=0.02, alpha_threshold=128):
    x0, y0, x1, y1 = content_bbox(im, alpha_threshold)
    pad = int(max(x1 - x0, y1 - y0) * pad_frac)
    return im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))


def _to_lin(a):
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def _to_srgb(a):
    a = np.clip(a, 0, 1)
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * a ** (1 / 2.4) - 0.055)


def pixelize(im, height_px, offset=(0.0, 0.0), margin=2, alpha_threshold=128, return_geom=False, scale=None):
    """Downscale the content of `im` so it is `height_px` tall, using a linear-light, alpha-weighted box
    filter. `offset` shifts the target pixel grid by a fraction of one target pixel (grid-alignment search).
    scale: source pixels per target pixel, overriding height_px (animation frames share one scale so a crouch
    stays shorter than a stand). Returns an RGBA PIL image (binary alpha, unquantized colours) with `margin`
    transparent pixels around; with return_geom also the grid geometry (see block_labels)."""
    x0, y0, x1, y1 = content_bbox(im, alpha_threshold=8)
    s = float(scale) if scale else (y1 - y0) / float(height_px)
    out_w = int(np.ceil((x1 - x0) / s)) + 1
    out_h = int(np.ceil((y1 - y0) / s)) + 1
    pad = int(np.ceil(2 * s)) + 2  # the sampling box may extend past the image: pad with transparency
    bx0, by0 = x0 - offset[0] * s + pad, y0 - offset[1] * s + pad
    box = (bx0, by0, bx0 + out_w * s, by0 + out_h * s)
    arr = np.pad(np.asarray(im).astype(np.float32) / 255.0, ((pad, pad), (pad, pad), (0, 0)))
    alpha = arr[..., 3]
    prem = _to_lin(arr[..., :3]) * alpha[..., None]
    chans = []
    for c in range(3):
        chans.append(np.asarray(Image.fromarray(prem[..., c], "F").resize((out_w, out_h), Image.BOX, box=box)))
    a = np.asarray(Image.fromarray(alpha, "F").resize((out_w, out_h), Image.BOX, box=box))
    rgb = np.stack(chans, -1) / np.maximum(a[..., None], 1e-6)
    out = np.zeros((out_h, out_w, 4), np.uint8)
    out[..., :3] = np.round(_to_srgb(rgb) * 255).astype(np.uint8)
    out[..., 3] = np.where(a * 255 >= alpha_threshold, 255, 0).astype(np.uint8)
    out[out[..., 3] == 0] = 0
    small = Image.fromarray(out, "RGBA")
    # trim empty rows/cols the extra sample row may have added, then add the margin
    bx = (0, 0, out_w, out_h)
    try:
        bx = content_bbox(small, 1)
        small = small.crop(bx)
    except ValueError:
        pass
    canvas = Image.new("RGBA", (small.width + 2 * margin, small.height + 2 * margin), (0, 0, 0, 0))
    canvas.paste(small, (margin, margin))
    if return_geom:
        # target pixel (X, Y) of `canvas` samples source box [gx0 + (X - margin + crop_x) * s, ... + s) in `im`
        geom = {"s": s, "gx0": bx0 - pad, "gy0": by0 - pad, "crop": [int(v) for v in bx], "margin": margin,
                "size": [canvas.width, canvas.height], "source_size": [im.width, im.height]}
        return canvas, geom
    return canvas


def block_labels(idx, geom):
    """Downsample a per-pixel label array (H x W ints of the source image, -1 = none) onto the sprite grid
    described by `geom`: each sprite pixel takes the most common label inside its source box."""
    W, H = geom["size"]
    s, m = geom["s"], geom["margin"]
    cx, cy = geom["crop"][0], geom["crop"][1]
    sh, sw = idx.shape
    out = np.full((H, W), -1, np.int32)
    for Y in range(H):
        y0 = geom["gy0"] + (Y - m + cy) * s
        ys, ye = max(0, int(np.floor(y0))), min(sh, int(np.ceil(y0 + s)))
        if ys >= ye:
            continue
        for X in range(W):
            x0 = geom["gx0"] + (X - m + cx) * s
            xs, xe = max(0, int(np.floor(x0))), min(sw, int(np.ceil(x0 + s)))
            if xs >= xe:
                continue
            block = idx[ys:ye, xs:xe]
            block = block[block >= 0]
            if block.size:
                out[Y, X] = int(np.bincount(block).argmax())
    return out


def fill_unlabelled(labels, mask, max_iter=64):
    """Give every masked pixel without a label (-1) the label of a labelled 4-neighbour, growing inward."""
    lab = labels.copy()
    lab[~mask] = -1
    for _ in range(max_iter):
        todo = (lab < 0) & mask
        if not todo.any():
            break
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            src = np.full_like(lab, -1)
            ys = slice(max(0, dy), lab.shape[0] + min(0, dy))
            yd = slice(max(0, -dy), lab.shape[0] + min(0, -dy))
            xs = slice(max(0, dx), lab.shape[1] + min(0, dx))
            xd = slice(max(0, -dx), lab.shape[1] + min(0, -dx))
            src[yd, xd] = lab[ys, xs]
            take = todo & (src >= 0)
            lab[take] = src[take]
            todo &= ~take
    return lab


def zoom(im, factor):
    return im.resize((im.width * factor, im.height * factor), Image.NEAREST)


def checker(w, h, cell=8):
    yy, xx = np.mgrid[0:h, 0:w]
    v = np.where(((xx // cell) + (yy // cell)) % 2 == 0, 236, 214).astype(np.uint8)
    return Image.fromarray(np.stack([v, v, v, np.full_like(v, 255)], -1), "RGBA")


def contact_sheet(images, labels=None, zoom_factor=4, cols=None, gap=8, label_h=16):
    """Images side by side (nearest-neighbour zoom) on a checkerboard, each with a small label."""
    tiles = [zoom(im, zoom_factor) if zoom_factor > 1 else im for im in images]
    cols = cols or len(tiles)
    rows = (len(tiles) + cols - 1) // cols
    cw = max(t.width for t in tiles)
    ch = max(t.height for t in tiles) + label_h
    sheet = Image.new("RGBA", (cols * cw + (cols + 1) * gap, rows * ch + (rows + 1) * gap), (255, 255, 255, 255))
    font = ImageFont.load_default()
    draw = ImageDraw.Draw(sheet)
    for i, t in enumerate(tiles):
        x = gap + (i % cols) * (cw + gap)
        y = gap + (i // cols) * (ch + gap)
        bg = checker(t.width, t.height)
        bg.alpha_composite(t)
        sheet.paste(bg, (x, y + label_h))
        draw.text((x, y), labels[i] if labels else str(i), fill=(20, 20, 20, 255), font=font)
    return sheet


def to_bytes(im):
    return bytearray(im.convert("RGBA").tobytes())


def from_bytes(w, h, data):
    return Image.frombytes("RGBA", (w, h), bytes(data))


def split_sheet(im, n, alpha_threshold=128, min_gap=3):
    """Cut a horizontal pose sheet (figures in one row on a plain background) into n figures.
    Uses empty columns between figures; merges the closest neighbours if there are too many pieces (detached
    props). Too few pieces mean figures that touch (a held item reaching the next figure): such a piece is cut at
    its thinnest columns, one cut near each place where a figure of the usual width would end. Cutting it in
    halves cut a figure in two when three figures touched (ref08's attack, 2026-11-12). Falls back to n equal
    columns.
    Returns [(figure RGBA image cropped to content, (x0, y0, x1, y1) in sheet coordinates)], left to right."""
    rgba = remove_background(im.convert("RGBA"))
    a = np.asarray(rgba)[..., 3] >= alpha_threshold
    cols = a.any(axis=0)
    spans, x = [], 0
    W = a.shape[1]
    while x < W:
        if cols[x]:
            s = x
            while x < W and (cols[x] or (x + min_gap < W and cols[x:x + min_gap].any())):
                x += 1
            spans.append([s, x])
        x += 1
    spans = [s for s in spans if s[1] - s[0] >= 4]
    while len(spans) > n:  # merge the pair separated by the smallest gap
        gaps = [spans[i + 1][0] - spans[i][1] for i in range(len(spans) - 1)]
        i = int(np.argmin(gaps))
        spans[i] = [spans[i][0], spans[i + 1][1]]
        del spans[i + 1]
    if 0 < len(spans) < n:  # figures that touch: share the missing cuts among the widest spans
        occ = a.sum(axis=0)
        holds = [1] * len(spans)
        for _ in range(n - len(spans)):
            i = max(range(len(spans)), key=lambda k: (spans[k][1] - spans[k][0]) / holds[k])
            holds[i] += 1
        cut = []
        for (s0, s1), k in zip(spans, holds):
            edges = [s0]
            for j in range(1, k):
                at, reach = s0 + (s1 - s0) * j / k, (s1 - s0) / k * 0.3
                lo, hi = max(edges[-1] + 4, int(at - reach)), min(s1 - 4, int(at + reach))
                xs = np.arange(lo, max(lo + 1, hi))
                # the thinnest column near the expected place (ties: the nearest to it)
                edges.append(int(xs[np.lexsort((np.abs(xs - at), occ[xs]))[0]]))
            cut += [[e0, e1] for e0, e1 in zip(edges, edges[1:] + [s1])]
        spans = cut
    if not spans:
        spans = [[k * W // n, (k + 1) * W // n] for k in range(n)]
    out = []
    for s0, s1 in spans:
        col = rgba.crop((s0, 0, s1, rgba.height))
        try:
            bx = content_bbox(col, alpha_threshold)
        except ValueError:
            raise ValueError("a pose-sheet column has no figure; regenerate the sheet")
        out.append((col.crop(bx), (s0 + bx[0], bx[1], s0 + bx[2], bx[3])))
    return out
