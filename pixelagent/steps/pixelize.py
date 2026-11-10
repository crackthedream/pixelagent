"""Step 3: grid-alignment search. Several pixelizations of the concept on shifted pixel grids; a vision
model picks the cleanest (dry run: the one with the cleanest silhouette by lint)."""
import itertools

from .. import imaging, prompts
from ..schemas import Pick


def _native(ctx):
    """The native finish: the concept IS a pixel-art sprite drawn as blocks: read it back pixel for pixel."""
    from ..pixelart import sprite_from_pixel_art
    concept = imaging.load_rgba(ctx.input("concept", "concept"))
    fix = ctx.asset.state.inputs.get("concept_fix") or {}
    keep = fix.get("grid") if fix.get("box") else None    # an area revision keeps the grid
    from .concept import BLOCK
    canvas, geom = sprite_from_pixel_art(concept, int(ctx.style["canvas_margin"]), grid=keep, expected=BLOCK)
    ctx.save_image("candidate0", "grid_00.png", canvas)
    ctx.save_image("pixel_base", "pixel_base.png", canvas)
    ctx.save_json("geom", "grid_geom.json", geom)
    bb = canvas.getbbox()
    ctx.info.update(picked=0, reason="native finish: the pixel-art concept read back block by block" +
                    ("; area revision: the previous grid is kept" if keep else ""),
                    grid=geom["grid"], size=list(canvas.size), sprite_height=(bb[3] - bb[1]) if bb else 0)


def run(ctx):
    spec = ctx.spec()
    if ctx.cfg.get("finish", {}).get("mode") == "native":
        return _native(ctx)
    concept = imaging.load_rgba(ctx.input("concept", "concept"))
    offs = [float(o) for o in ctx.cfg["pixelize"]["offsets"]]
    combos = list(itertools.product(offs, offs))[: int(ctx.cfg["budget"]["grid_candidates"])]
    fix = ctx.asset.state.inputs.get("concept_fix") or {}
    keep = fix.get("offsets") if fix.get("box") else None
    if keep:   # an area revision: the same grid as before, so everything outside the area lands on the same pixels
        combos = [tuple(float(v) for v in keep)]
    # The final sprite, outline included, must fit spec.height_px with canvas_margin free pixels around it:
    # the outline adds 1 px on every side, and a shifted grid can need one extra sample row.
    # no outline ring in the reduce finish (the concept's own edges stay), so the figure uses the full height
    ol = 0 if spec.outline == "none" or ctx.cfg.get("finish", {}).get("mode") in ("reduce", "native") else 1
    margin = int(ctx.style["canvas_margin"]) + ol
    body_h = max(4, spec.height_px - 2 * ol - 1)
    cands, scores, geoms = [], [], []
    for i, (ox, oy) in enumerate(combos):
        im, geom = imaging.pixelize(concept, body_h, (ox, oy), margin, int(ctx.style["alpha_threshold"]),
                                    return_geom=True)
        cands.append(im)
        geoms.append(geom)
        ctx.save_image(f"candidate{i}", f"grid_{i:02d}.png", im)
        scores.append(_silhouette_score(ctx, im))
    sheet = imaging.contact_sheet(cands, [f"#{i} ({ox:.2f},{oy:.2f})" for i, (ox, oy) in enumerate(combos)],
                                  zoom_factor=4, cols=min(6, len(cands)))
    sheet_path = ctx.save_image("sheet", "grid_sheet.png", sheet)

    def stub():
        best = max(range(len(cands)), key=lambda i: scores[i])
        return Pick(index=best, reason="cleanest silhouette by lint (dry run)",
                    ranking=sorted(range(len(cands)), key=lambda i: -scores[i]))

    pick = (Pick(index=0, reason="area revision: the previous grid is kept", ranking=[0]) if keep else
            ctx.backend.llm.structured("vision_pick", prompts.PICK_GRID.format(n=len(cands)),
                                       f"spec: {spec.summary}", [sheet_path], Pick, stub))
    if not 0 <= pick.index < len(cands):
        raise ValueError(f"model picked candidate {pick.index}, only 0..{len(cands) - 1} exist")
    ctx.save_image("pixel_base", "pixel_base.png", cands[pick.index])
    ctx.save_json("geom", "grid_geom.json", geoms[pick.index])
    ctx.info.update(picked=pick.index, reason=pick.reason, offsets=combos[pick.index],
                    silhouette_scores=[round(s, 1) for s in scores], size=list(cands[pick.index].size))


def _silhouette_score(ctx, im):
    """Lint the alpha mask only: stray fragments, orphans, jaggies of the outer shape."""
    w, h = im.size
    data = imaging.to_bytes(im)
    for i in range(0, len(data), 4):
        if data[i + 3]:
            data[i:i + 3] = b"\x40\x40\x40"
    return ctx.art.lint(w, h, data)["score"]
