"""Step 12: final files for the game and for PixelLiner."""
import json
import os
import shutil

from ..names import safe_filename
from .. import imaging


def run(ctx):
    crit = ctx.asset.state.steps.get("critique")
    doc = ctx.load_project("critique" if crit and "project" in crit.outputs else "polish")
    spec = ctx.spec()
    final = imaging.from_bytes(doc.w, doc.h, doc.composite())
    name = safe_filename(spec.name, fallback=ctx.asset.id)  # the display name may contain '/', ':', '..' etc.
    ctx.save_image("png", f"{name}.png", final)
    ctx.save_image("png_x4", f"{name}_x4.png", imaging.zoom(final, 4))
    layer_dir = ctx.path("layers")
    os.makedirs(layer_dir, exist_ok=True)
    for i, l in enumerate(doc.layers):
        if l.reference:
            continue
        imaging.from_bytes(doc.w, doc.h, l.cels[0]).save(ctx.path(f"layers/{i:02d}_{safe_filename(l.name, 'layer')}.png"))
    ctx.outputs["layers"] = layer_dir
    proj = ctx.save_project("project", doc)
    meta = {
        "name": spec.name, "asset_type": spec.asset_type, "size": [doc.w, doc.h],
        "palette": {k: "#%02x%02x%02x" % v[:3] for k, v in doc.palette.items()},
        "layers": [l.name for l in doc.layers if not l.reference],
        "pivot": [doc.w // 2, doc.h - int(ctx.style["canvas_margin"]) - 1],  # feet centre
        "frames": [{"duration_ms": d} for d in doc.durations],
        "source": {"asset": ctx.asset.id, "references": ctx.asset.state.inputs.get("references", [])},
    }
    ctx.save_json("meta", "meta.json", meta)
    ctx.info.update(png=ctx.outputs["png"], project=proj)
    dest = ctx.asset.state.inputs.get("export_dir")
    if dest:
        os.makedirs(dest, exist_ok=True)
        shutil.copyfile(ctx.outputs["png"], os.path.join(dest, os.path.basename(ctx.outputs["png"])))
