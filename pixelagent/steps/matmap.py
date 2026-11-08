"""Step 3: semantic material map of the chosen concept.

1. A vision model lists the materials visible in the concept (fills gaps in the spec: tights, inner shirt...).
2. The image model repaints the concept as flat key colours, one per material, same framing.
3. The map is decoded to material indices, aligned to the concept (placement + bounding-box fit), checked
   (overlap with the concept silhouette, share of undecodable pixels) and saved at concept resolution.
The materials step downsamples it onto the picked pixel grid. If the map is unreliable, `usable` is false and
the materials step falls back to colour clusters."""
import json

import numpy as np
from PIL import Image

from .. import imaging, prompts
from ..schemas import Material, MaterialList

KEY_COLORS = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff", "#ff8000", "#8000ff",
              "#00ff80", "#ff0080", "#80ff00", "#0080ff", "#800000", "#008000", "#000080", "#808000"]


def _rgb(hx):
    hx = hx.lstrip("#")
    return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))


def _size(cfg):
    w, h = (int(v) for v in cfg.get("images", {}).get("draft_size", "1024x1536").split("x"))
    return w, h


def _place(concept, size, fill=0.9, bg=(222, 222, 226)):
    """Concept centred on a flat canvas of the generation size. Returns (canvas RGB, scale, ox, oy)."""
    W, H = size
    k = min(W * fill / concept.width, H * fill / concept.height)
    cw, ch = max(1, round(concept.width * k)), max(1, round(concept.height * k))
    ox, oy = (W - cw) // 2, (H - ch) // 2
    canvas = Image.new("RGBA", (W, H), bg + (255,))
    canvas.alpha_composite(concept.resize((cw, ch), Image.LANCZOS), (ox, oy))
    return canvas.convert("RGB"), k, ox, oy


def decode(map_rgb, keys, max_dist=100):
    """RGB map -> index array (k = material, -1 = background/undecodable) and the undecodable mask."""
    arr = np.asarray(map_rgb.convert("RGB")).astype(np.int32)
    pal = np.array([_rgb(k) for k in keys] + [(255, 255, 255)], np.int32)
    H = arr.shape[0]
    idx = np.empty(arr.shape[:2], np.int32)
    unknown = np.zeros(arr.shape[:2], bool)
    for y0 in range(0, H, 128):
        a = arr[y0:y0 + 128]
        d = ((a[:, :, None, :] - pal[None, None, :, :]) ** 2).sum(-1)
        best = d.argmin(-1)
        ok = d.min(-1) <= max_dist * max_dist
        best[best == len(keys)] = -1
        best[~ok] = -1
        idx[y0:y0 + 128] = best
        unknown[y0:y0 + 128] = ~ok
    return idx, unknown


def align(idx_canvas, unknown_canvas, concept, k, ox, oy):
    """Map from generation-canvas coordinates back onto the concept, then fit the map's silhouette bbox to the
    concept's (the image model may drift slightly). Returns (idx at concept size, unknown ratio, iou)."""
    cw, ch = max(1, round(concept.width * k)), max(1, round(concept.height * k))
    region = idx_canvas[oy:oy + ch, ox:ox + cw]
    unk = unknown_canvas[oy:oy + ch, ox:ox + cw]
    to_img = lambda a: Image.fromarray((a + 1).astype(np.uint8), "L")  # noqa: E731  (-1..15 -> 0..16)
    idx = np.asarray(to_img(region).resize(concept.size, Image.NEAREST)).astype(np.int32) - 1
    unk = np.asarray(Image.fromarray(unk.astype(np.uint8) * 255, "L").resize(concept.size, Image.NEAREST)) > 0
    alpha = np.asarray(concept)[..., 3] >= 128
    fg = (idx >= 0) | unk
    if fg.any() and alpha.any():
        ys, xs = np.nonzero(fg)
        my0, my1, mx0, mx1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        ys, xs = np.nonzero(alpha)
        cy0, cy1, cx0, cx1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        if (my0, my1, mx0, mx1) != (cy0, cy1, cx0, cx1):
            crop = to_img(idx[my0:my1, mx0:mx1]).resize((cx1 - cx0, cy1 - cy0), Image.NEAREST)
            ucrop = Image.fromarray(unk[my0:my1, mx0:mx1].astype(np.uint8) * 255, "L").resize((cx1 - cx0, cy1 - cy0), Image.NEAREST)
            idx = np.full(idx.shape, -1, np.int32)
            idx[cy0:cy1, cx0:cx1] = np.asarray(crop).astype(np.int32) - 1
            unk = np.zeros(unk.shape, bool)
            unk[cy0:cy1, cx0:cx1] = np.asarray(ucrop) > 0
    fg = (idx >= 0) | unk
    inter = (fg & alpha).sum()
    union = (fg | alpha).sum()
    iou = float(inter / union) if union else 0.0
    unknown_ratio = float((unk & alpha).sum() / max(1, alpha.sum()))
    idx[~alpha] = -1
    return idx, unknown_ratio, iou


def stub_map(concept_canvas, keys, n):
    """Dry-run 'image model': flat-colour the concept by colour clusters (a crude segmentation)."""
    small = concept_canvas.resize((concept_canvas.width // 8, concept_canvas.height // 8), Image.BILINEAR)
    arr = np.asarray(concept_canvas).astype(np.int32)
    sarr = np.asarray(small).reshape(-1, 3).astype(np.float32)
    bg = np.array(concept_canvas.getpixel((0, 0)), np.float32)
    fgpx = sarr[np.abs(sarr - bg).sum(1) > 30]
    rng = np.random.default_rng(0)
    cents = fgpx[rng.choice(len(fgpx), size=min(n, len(fgpx)), replace=False)]
    for _ in range(10):
        lab = ((fgpx[:, None, :] - cents[None]) ** 2).sum(-1).argmin(1)
        for j in range(len(cents)):
            if (lab == j).any():
                cents[j] = fgpx[lab == j].mean(0)
    out = np.full(arr.shape, 255, np.uint8)
    flat = arr.reshape(-1, 3).astype(np.float32)
    is_fg = np.abs(flat - bg).sum(1) > 30
    lab = ((flat[:, None, :] - cents[None]) ** 2).sum(-1).argmin(1)
    keyarr = np.array([_rgb(k) for k in keys], np.uint8)
    o = out.reshape(-1, 3)
    o[is_fg] = keyarr[lab[is_fg] % len(keys)]
    return Image.fromarray(out, "RGB")


def run(ctx):
    spec = ctx.spec()
    concept = imaging.load_rgba(ctx.input("concept", "concept"))
    concept_path = ctx.input("concept", "concept")

    def list_stub():
        mats = spec.materials or [Material(name=f"mat{i}") for i in range(6)]
        return MaterialList(materials=mats[:16])

    mlist = ctx.backend.llm.structured(
        "director", prompts.LIST_MATERIALS.format(asset_type=spec.asset_type,
                                                  spec_materials=", ".join(m.name for m in spec.materials) or "-"),
        spec.summary, [concept_path], MaterialList, list_stub)
    mats = []
    for m in mlist.materials:
        if m.name not in [x.name for x in mats] and len(mats) < len(KEY_COLORS):
            mats.append(m)
    if len(mats) < 2:
        raise ValueError("the material list needs at least 2 materials")
    keys = KEY_COLORS[:len(mats)]
    legend = "\n".join(f"- {m.name} ({m.description or 'see image'}) = {k}" for m, k in zip(mats, keys))
    size = _size(ctx.cfg)
    canvas, k, ox, oy = _place(concept, size)
    canvas_path = ctx.save_image("placed", "concept_placed.png", canvas)

    def img_stub(n, out_dir):
        p = f"{out_dir}/gen00.png"
        stub_map(canvas, keys, len(keys)).save(p)
        return [p]

    quality = ctx.cfg.get("images", {}).get("matmap_quality", "medium")
    out = ctx.backend.images.generate("image_final", prompts.MATMAP.format(legend=legend), 1, ctx.path("generated"),
                                      [canvas_path], img_stub, quality=quality)
    raw = Image.open(out[0]).convert("RGB")
    if raw.size != canvas.size:  # model returned another size: scale to the placement frame
        raw = raw.resize(canvas.size, Image.NEAREST)
    ctx.save_image("raw_map", "matmap_raw.png", raw)
    idx_c, unk_c = decode(raw, keys)
    idx, unknown_ratio, iou = align(idx_c, unk_c, concept, k, ox, oy)
    alpha = np.asarray(concept)[..., 3] >= 128
    idx = imaging.fill_unlabelled(idx, alpha)
    present = {i: int((idx == i).sum()) for i in range(len(mats))}
    min_iou = float(ctx.cfg.get("matmap", {}).get("min_iou", 0.85))
    max_unknown = float(ctx.cfg.get("matmap", {}).get("max_unknown", 0.15))
    usable = iou >= min_iou and unknown_ratio <= max_unknown and sum(1 for v in present.values() if v) >= 2
    # outputs: index map (L image: 0 = none, k+1 = material k), preview in key colours, legend
    Image.fromarray((idx + 1).astype(np.uint8), "L").save(ctx.path("matmap_idx.png"))
    ctx.outputs["idx"] = ctx.path("matmap_idx.png")
    prev = np.zeros(idx.shape + (4,), np.uint8)
    for i, kc in enumerate(keys):
        prev[idx == i] = _rgb(kc) + (255,)
    ctx.save_image("map", "matmap.png", Image.fromarray(prev, "RGBA"))
    ctx.save_json("legend", "matmap.json", {
        "materials": [m.model_dump() for m in mats], "keys": keys, "usable": usable, "iou": round(iou, 3),
        "unknown_ratio": round(unknown_ratio, 3), "pixels": {mats[i].name: v for i, v in present.items()}})
    ctx.info.update(usable=usable, iou=round(iou, 3), unknown_ratio=round(unknown_ratio, 3),
                    materials=[m.name for m in mats], missing=[mats[i].name for i, v in present.items() if not v])
    if not usable:
        ctx.info["fallback"] = "materials step will use colour clusters"


def load(ctx):
    """For later steps: (index array at concept size, legend dict) or (None, None) if unusable/absent."""
    rec = ctx.asset.state.steps.get("matmap")
    if not rec or rec.status != "done" or "legend" not in rec.outputs:
        return None, None
    with open(ctx.input("matmap", "legend"), encoding="utf-8") as f:
        legend = json.load(f)
    if not legend.get("usable"):
        return None, legend
    idx = np.asarray(Image.open(ctx.input("matmap", "idx"))).astype(np.int32) - 1
    return idx, legend
