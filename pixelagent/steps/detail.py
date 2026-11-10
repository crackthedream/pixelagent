"""Step 8: detail pass. A model picks the focal regions (face, hands, held items...), then each region gets a
best-of-N constrained rewrite (see regions.py). Changes go into a 'detail' overlay layer."""
import json

from .. import imaging, prompts, regions
from ..schemas import Region, RegionPlan


def _material_boxes(ctx, w, h):
    """Dry-run planner: boxes around small/priority materials from the label map, or a head box."""
    try:
        with open(ctx.input("materials", "labelmap"), encoding="utf-8") as f:
            labels = json.load(f)["labels"]
    except (KeyError, OSError):
        labels = []
    pts = {}
    for i, name in enumerate(labels):
        if name:
            pts.setdefault(name, []).append((i % w, i // w))
    out = []
    for key in ("eyes", "skin", "face"):
        if key in pts:
            ys = [p[1] for p in pts[key]]
            top = min(ys)
            head = [p for p in pts[key] if p[1] <= top + max(6, h // 6)]
            xs, ys = [p[0] for p in head], [p[1] for p in head]
            out.append(Region(name="face", x=min(xs) - 1, y=min(ys) - 1, w=max(xs) - min(xs) + 3,
                              h=max(ys) - min(ys) + 3, goal="readable eyes and expression at 1x"))
            break
    small = sorted((n for n in pts if len(pts[n]) < 40 and n not in ("eyes", "skin", "face")), key=lambda n: len(pts[n]))
    for n in small[:2]:
        xs, ys = [p[0] for p in pts[n]], [p[1] for p in pts[n]]
        out.append(Region(name=n, x=min(xs) - 1, y=min(ys) - 1, w=max(xs) - min(xs) + 3, h=max(ys) - min(ys) + 3,
                          goal=f"clear, deliberate {n.replace('_', ' ')} shape"))
    if not out:
        out.append(Region(name="head", x=w // 3, y=2, w=max(4, w // 3), h=max(4, h // 6), goal="readable face"))
    return out


def run(ctx):
    dcfg = ctx.cfg.get("detail", {})
    mode = ctx.cfg.get("finish", {}).get("mode", "artist")
    if mode in ("artist", "reduce", "native"):
        ctx.skipped = True
        ctx.info["reason"] = ("the artist agent finishes the sprite ([finish] mode = 'artist')" if mode == "artist"
                              else "the reduction is the finished sprite ([finish] mode = 'reduce')")
        return
    if not dcfg.get("enabled", True):
        ctx.skipped = True
        ctx.info["reason"] = "[detail] enabled = false"
        return
    doc = ctx.load_project("lines")
    w, h = doc.w, doc.h
    spec = ctx.spec()
    data = doc.composite()
    sprite = imaging.from_bytes(w, h, data)
    max_regions = int(dcfg.get("max_regions", 4))
    max_side = int(dcfg.get("max_region_side", 24))
    overview = ctx.save_image("overview", "detail_overview.png", regions.grid_image(sprite, 8, 8))
    plan = ctx.backend.llm.structured(
        "director", prompts.PLAN_DETAIL.format(max_regions=max_regions, max_side=max_side,
                                               priorities="; ".join(spec.detail_priorities) or "face, hands"),
        f"sprite {w}x{h}. {spec.summary}", [overview, ctx.input("concept", "concept")], RegionPlan,
        lambda: RegionPlan(regions=_material_boxes(ctx, w, h)))
    concept = imaging.load_rgba(ctx.input("concept", "concept"))
    with open(ctx.input("pixelize", "geom"), encoding="utf-8") as f:
        geom = json.load(f)
    n = int(ctx.cfg["budget"].get("detail_candidates", 3))
    ref = imaging.to_bytes(imaging.load_rgba(ctx.input("pixelize", "pixel_base")))
    tol = float(dcfg.get("fidelity_tolerance", 0.02))
    results, done = [], []
    for r in plan.regions[:max_regions]:
        reg = regions.clamp_region((r.x, r.y, r.w, r.h), w, h, max_side)
        safe = "".join(ch if ch.isalnum() else "_" for ch in r.name)[:24] or "region"
        data, info = regions.rewrite_region(ctx, data, w, h, doc.palette, reg, safe, r.goal, concept, geom, n, tag="d",
                                            ref=ref, fidelity_tol=tol)
        info["name"] = r.name
        results.append(info)
        if info.get("picked"):
            done.append(list(reg))
    changed, erased = regions.write_overlay(ctx, doc, data, "detail")
    ctx.save_project("project", doc)
    ctx.save_image("preview", "detail.png", imaging.from_bytes(w, h, doc.composite()))
    ctx.save_json("plan", "detail_plan.json", {"regions": results})
    ctx.info.update(regions=[{k: v for k, v in r.items()
                              if k in ("name", "region", "picked", "candidates", "errors", "rejected_by_fidelity")}
                             for r in results],
                    changed_regions=done, overlay_pixels=changed, erased=erased)
