"""Scene backdrops: the parallax layers behind the action (docs/BACKGROUNDS_DESIGN.md, section 3A).

  plan    the director turns the prompt into 3-5 layers, far to near (sky first), with one horizon, light and palette
  layers  the image model draws each layer on its own (sky opaque, the others on a transparent background), from text
          only: an image edit given the layers behind would keep and redraw them into the new layer
  seams   each layer is cut where its pixel-art reading repeats best (best_wrap: a gap in a treeline, two alike
          stretches of sky), so it scrolls without a seam; no model call
  pixel   each layer is read at ~1.9 image pixels per sprite pixel (a 1536x1024 drawing -> 810x540, one screen high at
          the characters' 1x scale), alpha made binary, colours cut per layer by k-means in OKLab and graded like the
          characters; a still composite and a scrolling parallax preview
  review  gate S: the owner approves the backdrop (a note redraws the layers)
  export  layer PNGs, composite, meta.json, a Godot ParallaxBackground scene, a Tiled map with parallax image layers,
          a Unity parallax script and a README

Spike (2026-11-11): transparent layers come back slightly see-through (alpha 192-254), so alpha is thresholded; a
2:1 reading of a scene reads as pixel art next to a 160-px character. The spike's seam repaint (a masked edit over a
band) worked on a full opaque scene but left blocks in transparent layers, so seams are cut instead (scene.seams)."""
import json
import os
from typing import List

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from .. import prompts
from ..names import safe_filename
from .build import GRADES, _from_oklab, _kmeans, _oklab, grade_colours

DRAW_SIZE = (1536, 1024)


class LayerPlan(BaseModel):
    name: str = Field(description="short layer name: sky, far, mid, near, ...")
    content: str = Field(description="what this layer shows, concretely (shapes, objects, where they sit)")
    depth: float = Field(description="0 = farthest (the sky) to 1 = nearest")
    opaque: bool = Field(default=False, description="true only for the sky: it fills the whole image")
    colours: int = Field(default=24, description="colour budget: 12-16 far away, 24-40 near")


class ScenePlan(BaseModel):
    name: str
    summary: str = Field(description="the place, time of day and mood in one sentence")
    light: str = Field(description="where the light comes from and its colour")
    palette: str = Field(description="the scene's palette in a few words (shared by every layer)")
    horizon: float = Field(default=0.6, description="the horizon's height, 0 = top, 1 = bottom")
    layers: List[LayerPlan]


def _cfg(ctx):
    return ctx.cfg.get("scene", {})


def load_plan(ctx):
    with open(ctx.input("scene_plan", "plan"), encoding="utf-8") as f:
        return ScenePlan.model_validate_json(f.read())


def size(ctx):
    """The pixel-art size of a layer: one screen high at 1x (default 540) and as wide as the drawing allows."""
    h = int(_cfg(ctx).get("height", 540))
    return round(DRAW_SIZE[0] * h / DRAW_SIZE[1]), h


def scroll_factor(depth):
    """How fast a layer scrolls against the camera: the sky barely moves, the nearest layer moves with it."""
    return round(0.05 + 0.95 * max(0.0, min(1.0, depth)) ** 1.5, 3)


# ------------------------------------------------------------------ plan

def plan(ctx):
    inp = ctx.asset.state.inputs
    request = inp.get("prompt") or "a quiet forest"

    def stub():
        return ScenePlan(name=inp.get("name") or ctx.asset.id, summary=request, light="soft light from the left",
                         palette="muted greens and warm dusk tones", horizon=0.6,
                         layers=[LayerPlan(name="sky", content="evening sky with a few clouds", depth=0, opaque=True,
                                           colours=12),
                                 LayerPlan(name="far", content="distant hills", depth=0.3, colours=16),
                                 LayerPlan(name="mid", content="trees", depth=0.65, colours=24),
                                 LayerPlan(name="near", content="bushes and grass", depth=1.0, colours=32)])
    text = request + "".join(f"\n{n}" for n in ctx.notes())
    spec = ctx.backend.llm.structured("director", prompts.SCENE_PLAN, text, [], ScenePlan, stub)
    if inp.get("name"):
        spec.name = inp["name"]
    spec.layers = sorted(spec.layers, key=lambda l: l.depth)[:5]
    if not spec.layers:
        raise ValueError("the plan has no layers")
    for i, layer in enumerate(spec.layers):
        layer.opaque = i == 0                       # the farthest layer is the sky; every other one has transparency
        layer.colours = max(8, min(48, int(layer.colours)))
    ctx.save_json("plan", "plan.json", spec)
    ctx.info.update(name=spec.name, layers=[l.name for l in spec.layers], summary=spec.summary[:200])


# ------------------------------------------------------------------ layers

def _stub_layer(i, n, opaque):
    def stub(k, out_dir):
        W, H = DRAW_SIZE
        im = Image.new("RGBA", (W, H), (150, 180, 220, 255) if opaque else (0, 0, 0, 0))
        if not opaque:
            top = int(H * (0.45 + 0.4 * i / max(1, n)))
            band = Image.new("RGBA", (W, H - top), (60 + 30 * i, 120 - 10 * i, 80, 230))
            im.paste(band, (0, top))
            for x in range(0, W, 96):                   # a little shape so the seam step has something to look at
                im.paste((40 + 20 * i, 90, 60, 230), (x, top - 40 - (x // 96 % 3) * 20, x + 48, top))
        p = os.path.join(out_dir, "gen00.png")
        im.save(p)
        return [p]
    return stub


def layers(ctx):
    sp = load_plan(ctx)
    icfg = ctx.cfg.setdefault("images", {})
    old = {k: icfg.get(k) for k in ("draft_size", "background")}
    icfg["draft_size"] = f"{DRAW_SIZE[0]}x{DRAW_SIZE[1]}"
    notes = "; ".join(ctx.notes())
    try:
        for i, layer in enumerate(sp.layers):
            icfg["background"] = "opaque" if layer.opaque else "transparent"
            prompt = prompts.SCENE_LAYER.format(
                summary=sp.summary, light=sp.light, palette=sp.palette, horizon=round(sp.horizon * 100),
                name=layer.name, content=layer.content, distance=_distance(layer.depth),
                fill=prompts.SCENE_SKY if layer.opaque else prompts.SCENE_TRANSPARENT)
            if notes:
                prompt += "\nChanges requested: " + notes
            with open(ctx.path(f"prompt_{i:02d}_{safe_filename(layer.name, 'layer')}.txt"), "w", encoding="utf-8") as f:
                f.write(prompt)
            ctx.log(f"[scene_layers] {layer.name}")
            out = ctx.backend.images.generate("image_final", prompt, 1, ctx.path(f"gen_{i:02d}"),
                                              stub=_stub_layer(i, len(sp.layers), layer.opaque),
                                              quality=_cfg(ctx).get("quality", "medium"))
            im = Image.open(out[0]).convert("RGBA")
            if im.size != DRAW_SIZE:
                im = im.resize(DRAW_SIZE, Image.LANCZOS)
            ctx.save_image(f"layer{i}", f"layer_{i:02d}.png", im)
    finally:
        for k, v in old.items():
            if v is None:
                icfg.pop(k, None)
            else:
                icfg[k] = v
    comp = _composite([Image.open(ctx.outputs[f"layer{i}"]) for i in range(len(sp.layers))])
    ctx.save_image("composite", "composite.png", comp)
    ctx.info["layers"] = len(sp.layers)


def _distance(depth):
    if depth <= 0.05:
        return "the sky, infinitely far"
    if depth < 0.45:
        return ("far away: simple shapes, few tones, low contrast, lighter and cooler (atmospheric perspective), "
                "almost no detail")
    if depth < 0.8:
        return "the middle distance: clear shapes, moderate detail and contrast"
    return ("close to the viewer, in front of the play area: the most detail and contrast, darker silhouettes. ONLY "
            "along the bottom edge and at the far left and right: the middle of the image (where the characters walk) "
            "stays fully transparent")


def _composite(ims):
    out = Image.new("RGBA", ims[0].size, (0, 0, 0, 255))
    for im in ims:
        out.alpha_composite(im.convert("RGBA").resize(out.size))
    return out


# ------------------------------------------------------------------ seams

def seam_error(im):
    """Mean colour difference across the wrap (last column against the first), on the pixel-art reading; columns
    where both ends are transparent count as matching."""
    a = np.asarray(im.convert("RGBA")).astype(float)
    l, r = a[:, 0], a[:, -1]
    both_clear = (l[:, 3] < 128) & (r[:, 3] < 128)
    d = np.abs(l[:, :3] - r[:, :3]).mean(1)
    d[np.abs(l[:, 3] - r[:, 3]) >= 128] = 255.0          # opaque against clear: the worst mismatch
    d[both_clear] = 0.0
    return round(float(d.mean()), 2)


BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32) / 16.0


def wrap_layer(im, x0, P, blend):
    """Cut [x0, x0+P) of a pixel-art layer so it repeats, with its first `blend` columns dithered from the drawing's
    continuation (the columns right after the cut) into its own start. Where it wraps, column P-1 runs into what
    the drawing had next, so there is no edge; the ordered dither picks whole pixels from one or the other, so no new
    colours appear (pixel-art style, no blur)."""
    a = np.asarray(im.convert("RGBA"))
    H = a.shape[0]
    out = a[:, x0:x0 + P].copy()
    b = min(blend, a.shape[1] - (x0 + P))
    for c in range(b):
        t = (c + 0.5) / b                                  # 0 at the wrap: the continuation; 1: the layer's own start
        own = BAYER4[np.arange(H) % 4, c % 4] < t
        out[~own, c] = a[~own, x0 + P + c]
    return Image.fromarray(out)


def best_wrap(px, min_frac=0.75, blend=0):
    """Where to cut a pixel-art layer so that it repeats without a seam: the columns x0 and x0+P (P >= min_frac of the
    width) that match best -- a gap in a treeline (clear on both), or two stretches of sky alike -- judged on two
    neighbouring columns. Cutting [x0, x0+P) then wraps from column x0+P-1 to x0 just as the drawing ran from x0+P-1
    to x0+P. Returns (x0, P, error); a larger P wins near-ties (fewer visible repeats)."""
    a = np.asarray(px.convert("RGBA")).astype(np.float32)[::2]       # every other row: plenty to judge columns by
    H, W = a.shape[:2]
    pmin = max(2, int(W * min_frac))
    W_end = W - blend                                      # the blend reads `blend` columns past the cut
    clear = a[..., 3] < 128

    def diff(i, js):
        d = np.abs(a[:, js, :3] - a[:, i, None, :3]).mean(-1)
        ci, cj = clear[:, i, None], clear[:, js]
        d[ci != cj] = 255.0
        d[ci & cj] = 0.0
        return d.mean(0)
    best = (float("inf"), 0, W, 0.0)
    for i in range(0, W_end - pmin - 1):
        js = np.arange(i + pmin, W_end - 1)
        if not len(js):
            break
        e = (diff(i, js) + diff(i + 1, js + 1)) / 2
        score = e + 3.0 * (W - (js - i)) / W
        k = int(score.argmin())
        if score[k] < best[0]:
            best = (float(score[k]), i, int(js[k] - i), float(e[k]))
    _, x0, P, err = best
    return x0, P, round(err, 2)


def seams(ctx):
    """Find each layer's repeat: the best cut of its pixel-art reading (no model call). A model repaint of a band
    over the seam was tried first and left opaque blocks in transparent layers and a lighter stripe in the sky
    (2026-11-11), so the layer is cut where it already matches instead; each layer gets its own repeat width."""
    sp = load_plan(ctx)
    min_frac = float(_cfg(ctx).get("min_repeat", 0.75))
    limit = float(_cfg(ctx).get("seam_limit", 30.0))
    report = []
    for i, layer in enumerate(sp.layers):
        src = Image.open(ctx.input("scene_layers", f"layer{i}")).convert("RGBA")
        px = to_pixel(src, size(ctx), layer.colours, layer.opaque, None)
        before = seam_error(px)
        # a sky has no detail to ghost: a wide, gentle blend; detailed layers: a narrow one
        blend = int(_cfg(ctx).get("sky_blend", 96)) if layer.opaque else int(_cfg(ctx).get("seam_blend", 32))
        x0, P, err = best_wrap(px, min_frac, blend)
        if before <= err:                                  # the full width already repeats as well: keep it whole
            x0, P, err = 0, px.width, before
        # the cut's two ends still differ by `err`; the dithered blend hides moderate differences, and only very
        # different ends (half a tree against open sky) may show as a ghost: flagged for the owner
        report.append({"layer": layer.name, "x0": x0, "width": P, "blend": blend, "before": before, "after": err,
                       "visible": err > limit})
    ctx.save_json("report", "seams.json", report)
    ctx.info["seams"] = report


# ------------------------------------------------------------------ pixel art

def to_pixel(img, size_, colours, opaque, grade):
    """A drawn layer -> pixel art at size_: box-downsampled (colours weighted by alpha), alpha made binary (the image
    model's transparent layers are slightly see-through), colours cut by k-means in OKLab, then graded."""
    a = np.asarray(img.convert("RGBA")).astype(float) / 255.0
    rgb, al = a[..., :3], a[..., 3:4]
    pre = Image.fromarray(np.uint8(np.concatenate([rgb * al, al], -1) * 255))
    small = np.asarray(pre.resize(size_, Image.BOX)).astype(float) / 255.0
    alpha = small[..., 3]
    col = np.where(alpha[..., None] > 1e-3, small[..., :3] / np.maximum(alpha[..., None], 1e-3), 0)
    keep = np.ones(alpha.shape, bool) if opaque else alpha >= 0.5
    out = np.zeros(alpha.shape + (4,), np.uint8)
    if keep.any():
        px = np.clip(col[keep] * 255, 0, 255)
        lab = _oklab(px)
        rng = np.random.default_rng(0)
        sample = lab[rng.choice(len(lab), min(len(lab), 20000), replace=False)]
        k = max(1, min(colours, len(np.unique(np.round(sample, 3), axis=0))))
        assign = _kmeans(sample, k)
        centres = np.array([sample[assign == j].mean(0) for j in range(k) if (assign == j).any()])
        idx = ((lab[:, None, :] - centres[None]) ** 2).sum(-1).argmin(1)
        pal = _from_oklab(centres)
        if grade:
            pal = grade_colours(pal, **grade)
        out[keep, :3] = pal[idx]
        out[keep, 3] = 255
    return Image.fromarray(out)


def tiled(im, W):
    """A layer repeated sideways to fill width W."""
    out = Image.new("RGBA", (W, im.height))
    for x in range(0, W, im.width):
        out.alpha_composite(im, (x, 0))
    return out


def pixel(ctx):
    sp = load_plan(ctx)
    W, H = size(ctx)
    gname = str(ctx.cfg.get("finish", {}).get("grade", "medium"))
    grade = GRADES.get(gname)
    with open(ctx.input("scene_seams", "report"), encoding="utf-8") as f:
        cuts = json.load(f)
    ims = []
    for i, layer in enumerate(sp.layers):
        src = Image.open(ctx.input("scene_layers", f"layer{i}"))
        im = to_pixel(src, (W, H), layer.colours, layer.opaque, grade)
        c = cuts[i]
        im = wrap_layer(im, c["x0"], c["width"], c.get("blend", 0))   # its repeat (scene_seams)
        ctx.save_image(f"layer{i}", f"layer_{i:02d}.png", im)
        ims.append(im)
    comp = Image.new("RGBA", (W, H))
    for im in ims:
        comp.alpha_composite(tiled(im, W))
    ctx.save_image("composite", "composite.png", comp)
    ctx.save_image("composite_x2", "composite_x2.png", comp.resize((W * 2, H * 2), Image.NEAREST))
    _parallax_gif(ctx, sp, ims)
    ctx.info.update(size=[W, H], grade=gname, colours={l.name: int(len(np.unique(np.asarray(im).reshape(-1, 4)[
        np.asarray(im).reshape(-1, 4)[:, 3] > 0], axis=0))) for l, im in zip(sp.layers, ims)},
        scroll={l.name: scroll_factor(l.depth) for l in sp.layers})


def _character(ctx):
    """The first finished character of the project (to stand in the preview), or None."""
    proj = ctx.project
    for aid in proj.asset_ids():
        a = proj.asset(aid)
        if a.state.inputs.get("kind") in (None, "character") and a.state.inputs.get("asset_type", "character") \
                == "character" and a.state.status == "complete":
            try:
                im = Image.open(a.output("export", "png")).convert("RGBA")
                return im.crop(im.getbbox()) if im.getbbox() else None
            except (KeyError, OSError):
                continue
    return None


def _parallax_gif(ctx, sp, ims, frames=48):
    """A scrolling preview: each layer moves by its scroll factor (the camera pans one screen), a character stands
    in front if the project has one."""
    W, H = size(ctx)
    ch = _character(ctx)
    strips = [tiled(im, W + im.width) for im in ims]
    out = []
    for t in range(frames):
        cam = t * W / frames
        f = Image.new("RGBA", (W, H))
        for layer, strip in zip(sp.layers, strips):
            w = strip.width - W                            # each layer repeats at its own width
            x = int(round(cam * scroll_factor(layer.depth))) % w
            f.alpha_composite(strip.crop((x, 0, x + W, H)))
        if ch is not None:
            f.alpha_composite(ch, ((W - ch.width) // 2, int(H * 0.93) - ch.height))
        out.append(f.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT))
    p = ctx.path("parallax.gif")
    out[0].save(p, save_all=True, append_images=out[1:], duration=60, loop=0)
    ctx.outputs["gif"] = p


# ------------------------------------------------------------------ review and export

def review(ctx):
    ctx.info["suggested"] = None


def export(ctx):
    sp = load_plan(ctx)
    W, H = size(ctx)
    name = safe_filename(sp.name, ctx.asset.id)
    ldir = ctx.path("layers")
    os.makedirs(ldir, exist_ok=True)
    meta_layers = []
    for i, layer in enumerate(sp.layers):
        fn = f"{i:02d}_{safe_filename(layer.name, 'layer').lower()}.png"
        im = Image.open(ctx.input("scene_pixel", f"layer{i}"))
        im.save(os.path.join(ldir, fn))
        meta_layers.append({"name": layer.name, "file": f"layers/{fn}", "scroll": scroll_factor(layer.depth),
                            "depth": layer.depth, "opaque": layer.opaque, "width": im.width, "height": H})
    ctx.outputs["layers"] = ldir
    comp = Image.open(ctx.input("scene_pixel", "composite"))
    ctx.save_image("png", f"{name}.png", comp)
    ctx.save_image("png_x2", f"{name}_x2.png", comp.resize((W * 2, H * 2), Image.NEAREST))
    import shutil
    shutil.copyfile(ctx.input("scene_pixel", "gif"), ctx.path(f"{name}_parallax.gif"))
    ctx.outputs["parallax"] = ctx.path(f"{name}_parallax.gif")
    meta = {"name": sp.name, "summary": sp.summary, "size": [W, H], "layers": meta_layers}
    ctx.save_json("meta", "meta.json", meta)
    with open(ctx.path(f"{name}.tscn"), "w", encoding="utf-8") as f:
        f.write(_godot(name, meta_layers))
    ctx.outputs["godot"] = ctx.path(f"{name}.tscn")
    with open(ctx.path(f"{name}.tmx"), "w", encoding="utf-8") as f:
        f.write(_tiled(meta_layers, W, H))
    ctx.outputs["tiled"] = ctx.path(f"{name}.tmx")
    with open(ctx.path("ParallaxLayerScroll.cs"), "w", encoding="utf-8") as f:
        f.write(UNITY_SCRIPT)
    ctx.outputs["unity"] = ctx.path("ParallaxLayerScroll.cs")
    with open(ctx.path("README.txt"), "w", encoding="utf-8") as f:
        f.write(_readme(name, meta_layers, W, H))
    ctx.outputs["readme"] = ctx.path("README.txt")
    ctx.info.update(layers=len(meta_layers), size=[W, H])


def _godot(name, layers):
    n = len(layers)
    lines = [f"[gd_scene load_steps={n + 1} format=3]", ""]
    for i, l in enumerate(layers):
        lines.append(f'[ext_resource type="Texture2D" path="res://{name}/{l["file"]}" id="{i + 1}"]')
    lines += ["", f'[node name="{name}" type="ParallaxBackground"]', ""]
    for i, l in enumerate(layers):
        node = safe_filename(l["name"], f"layer{i}")
        lines += [f'[node name="{node}" type="ParallaxLayer" parent="."]',
                  f'motion_scale = Vector2({l["scroll"]}, {l["scroll"]})',
                  f"motion_mirroring = Vector2({l['width']}, 0)", "",
                  f'[node name="Sprite2D" type="Sprite2D" parent="{node}"]',
                  "texture_filter = 1", f'texture = ExtResource("{i + 1}")', "centered = false", ""]
    return "\n".join(lines)


def _tiled(layers, W, H):
    t = 32
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<map version="1.10" tiledversion="1.10.2" orientation="orthogonal" renderorder="right-down" '
           f'width="{-(-W // t)}" height="{-(-H // t)}" tilewidth="{t}" tileheight="{t}" infinite="0" '
           f'nextlayerid="{len(layers) + 1}" nextobjectid="1">']
    for i, l in enumerate(layers):
        out += [f' <imagelayer id="{i + 1}" name="{l["name"]}" parallaxx="{l["scroll"]}" parallaxy="1" repeatx="1">',
                f'  <image source="{l["file"]}" width="{l["width"]}" height="{H}"/>', " </imagelayer>"]
    out.append("</map>")
    return "\n".join(out) + "\n"


UNITY_SCRIPT = """using UnityEngine;

// PixelAgent backdrop layer. Put it on a GameObject that holds the layer's sprite three times side by side (or one
// SpriteRenderer in Tiled draw mode, 3 x repeatWidth wide), centred on the camera's start.
// scroll: the layer's factor from meta.json / README.txt (0 = fixed to the screen, 1 = moves with the play area).
public class ParallaxLayerScroll : MonoBehaviour
{
    public Transform cam;
    public float scroll = 0.5f;
    public float repeatWidth = 8.1f;   // the layer's width in world units (its pixels / Pixels Per Unit)
    float startX;

    void Start()
    {
        if (cam == null) cam = Camera.main.transform;
        startX = transform.position.x;
    }

    void LateUpdate()
    {
        float follow = cam.position.x * (1f - scroll);   // a far layer follows the camera: it seems to move slowly
        float travelled = cam.position.x * scroll;
        transform.position = new Vector3(startX + follow, transform.position.y, transform.position.z);
        if (travelled > startX + repeatWidth) startX += repeatWidth;
        else if (travelled < startX - repeatWidth) startX -= repeatWidth;
    }
}
"""


def _readme(name, layers, W, H):
    rows = "\n".join(f"  {l['file']:<28} scroll {l['scroll']:<6} repeats every {l['width']:<4} px  "
                     f"{'opaque' if l['opaque'] else 'transparent'}" for l in layers)
    return f"""{name}: parallax backdrop made by PixelAgent

Pixel art at 1x (the characters' scale), {H} px high; each layer repeats horizontally at its own width.
Scale by whole numbers with nearest-neighbour filtering. Layers, far to near (scroll: how fast a layer moves
against the camera; 1 = with the play area):
{rows}

{name}.png / {name}_x2.png   all layers composited (preview)
{name}_parallax.gif          a scrolling preview
meta.json                    layers, scroll factors, size

Godot 4: copy this folder into your project as res://{name}/ (layers/ inside it), set the PNGs' import filter to
Nearest (the scene also sets texture_filter), and instance {name}.tscn (a ParallaxBackground) in your level.
Tiled: open {name}.tmx; its image layers have the parallax factors and repeat horizontally (Tiled 1.8+).
Unity: import the PNGs as Sprite (2D), Filter Point, Compression None, Wrap Mode Repeat, Pixels Per Unit as in your
game; per layer, two sprite copies side by side under a GameObject with ParallaxLayerScroll.cs (scroll as above,
repeatWidth = the layer's width / Pixels Per Unit).
LDtk: add the layer PNGs as level background images or as an "Entities" decoration; LDtk has no parallax image
layers, so the parallax itself is done in the engine (the scroll factors above).
"""
