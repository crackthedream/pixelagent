"""Backgrounds spike (docs/BACKGROUNDS_DESIGN.md section 5, step 1): three questions before any background code.

  1. Does the image model give a transparent layer (background="transparent")?
  2. Does a full scene layer, pixelized at 2 image pixels per sprite pixel (768x512), read as pixel art next to a
     160-px character at 1x?
  3. Does a seam repaint (wrap-around shift, masked band repainted) make the layer repeat without a visible seam?

  python benchmarks/bg_spike.py --project bench_runs_bg_spike --character-png PATH [--mode dry_run]

About $0.25 (two generations and one masked edit at medium quality)."""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from pixelagent.models import make_backend  # noqa: E402
from pixelagent.steps.build import GRADES, _kmeans, _oklab, _from_oklab, grade_colours  # noqa: E402
from pixelagent.store import Project  # noqa: E402

FAR = """A far background layer for a 2D side-scrolling pixel-art game: a row of distant misty mountains and a soft
treeline silhouette along the bottom third, in pale, low-contrast blue-violet tones. ONLY those shapes: everything
above and around them is fully transparent (no sky, no background colour). Wide horizontal composition that could
continue to the left and right. Hand-pixelled style: clean flat tone clusters, no gradients, no text."""

MID = """A side-scrolling 2D pixel-art game background, forest at dusk, seen from the side: tall tree trunks and
layered foliage in the middle distance, soft evening light from the left, a few fireflies, a calm path of earth along
the bottom. Hand-pixelled style like modern pixel-art action platformers: very small crisp pixels, clean flat tone
clusters, no gradients, no blur, a light and slightly muted palette. Wide horizontal composition that continues to
the left and right; nothing cut off by a frame. No characters, no text, no UI."""

SEAM = """This is a side-scrolling game background. Repaint ONLY the transparent band of the mask so that the scene
continues naturally and seamlessly from the left part to the right part: the same trees, light, colours, horizon
and path, in exactly the same pixel-art style. Keep everything outside the band unchanged."""


def pixelize(img, size=(768, 512), colours=40, grade="medium"):
    """A painted-pixel layer -> pixel art at `size`: box-downsampled, colours reduced by k-means in OKLab, graded."""
    small = img.convert("RGB").resize(size, Image.BOX)
    a = np.asarray(small).reshape(-1, 3).astype(float)
    lab = _oklab(a)
    rng = np.random.default_rng(0)
    sample = lab[rng.choice(len(lab), min(len(lab), 20000), replace=False)]
    assign = _kmeans(sample, colours)
    centres = np.array([sample[assign == k].mean(0) for k in range(colours) if (assign == k).any()])
    idx = ((lab[:, None, :] - centres[None]) ** 2).sum(-1).argmin(1)
    pal = _from_oklab(centres)
    if GRADES.get(grade):
        pal = grade_colours(pal, **GRADES[grade])
    return Image.fromarray(pal[idx].reshape(size[1], size[0], 3).astype(np.uint8)).convert("RGBA")


def seam_error(img):
    """Mean colour difference between the last and the first column (0 = repeats perfectly)."""
    a = np.asarray(img.convert("RGB")).astype(float)
    return round(float(np.abs(a[:, -1] - a[:, 0]).mean()), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--character-png", required=True)
    ap.add_argument("--mode", default="openai", choices=["openai", "dry_run"])
    a = ap.parse_args()
    os.makedirs(a.project, exist_ok=True)
    proj = Project(a.project, {"backend": {"mode": a.mode}})
    back = make_backend(proj, "spike")
    out = os.path.join(a.project, "spike")
    os.makedirs(out, exist_ok=True)
    icfg = proj.config.setdefault("images", {})
    icfg["draft_size"] = "1536x1024"
    report = {}

    def stub_png(color):
        def f(n, d):
            p = os.path.join(d, "gen00.png")
            Image.new("RGBA", (1536, 1024), color).save(p)
            return [p]
        return f

    # 1. transparent layer
    icfg["background"] = "transparent"
    try:
        far = back.images.generate("image_final", FAR, 1, os.path.join(out, "far"), quality="medium",
                                   stub=stub_png((0, 0, 0, 0)))[0]
        al = np.asarray(Image.open(far).convert("RGBA"))[..., 3]
        report["transparent"] = {"ok": True, "clear_share": round(float((al == 0).mean()), 3),
                                 "partial_share": round(float(((al > 0) & (al < 255)).mean()), 3)}
    except Exception as e:  # noqa: BLE001
        report["transparent"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    # 2. a full scene layer, pixelized at 2:1
    icfg["background"] = "opaque"
    mid = back.images.generate("image_final", MID, 1, os.path.join(out, "mid"), quality="medium",
                               stub=stub_png((90, 120, 110, 255)))[0]
    mid_im = Image.open(mid).convert("RGBA")
    px = pixelize(mid_im)
    px.save(os.path.join(out, "mid_pixel_768x512.png"))
    report["layer"] = {"drawn": list(mid_im.size), "pixelized": list(px.size),
                       "colours": len(np.unique(np.asarray(px).reshape(-1, 4), axis=0)), "seam_before": seam_error(px)}
    # 3. seam repaint: wrap by half, repaint a band over the old edges, pixelize again
    W, H = mid_im.size
    shifted = Image.new("RGBA", (W, H))
    shifted.paste(mid_im.crop((W // 2, 0, W, H)), (0, 0))
    shifted.paste(mid_im.crop((0, 0, W // 2, H)), (W - W // 2, 0))
    src = os.path.join(out, "seam_source.png")
    shifted.convert("RGB").save(src)
    band = 192
    mask = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    mask.paste(Image.new("RGBA", (band, H), (0, 0, 0, 0)), (W // 2 - band // 2, 0))
    mpath = os.path.join(out, "seam_mask.png")
    mask.save(mpath)
    fixed = back.images.generate("image_final", SEAM, 1, os.path.join(out, "seam"), refs=[src], mask=mpath,
                                 quality="medium")[0]
    fixed_im = Image.open(fixed).convert("RGBA").resize((W, H))
    # outside the band the shifted image is kept exactly; the band is the repaint
    result = shifted.copy()
    result.paste(fixed_im.crop((W // 2 - band // 2, 0, W // 2 + band // 2, H)), (W // 2 - band // 2, 0))
    px2 = pixelize(result)
    px2.save(os.path.join(out, "mid_pixel_seamless.png"))
    report["seam"] = {"seam_before_shift": seam_error(px), "after": seam_error(px2),
                      "note": "after the shift the old edges meet in the middle (repainted); the new edges are the "
                              "former middle, continuous by construction"}
    # preview: the pixel layer repeated twice with the character standing on the path, at 2x
    ch = Image.open(a.character_png).convert("RGBA")
    ch = ch.crop(ch.getbbox())
    tile = Image.new("RGBA", (px2.width * 2, px2.height))
    tile.alpha_composite(px2, (0, 0))
    tile.alpha_composite(px2, (px2.width, 0))
    tile.alpha_composite(ch, (px2.width - ch.width // 2, px2.height - 24 - ch.height))
    tile.resize((tile.width * 2, tile.height * 2), Image.NEAREST).save(os.path.join(out, "preview_2x.png"))
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    costs = os.path.join(a.project, "costs.jsonl")
    usd = 0.0
    if os.path.exists(costs):
        with open(costs, encoding="utf-8") as f:
            usd = sum(json.loads(line).get("usd", 0) for line in f if line.strip())
    print(json.dumps(report, indent=1))
    print(f"cost ${usd:.3f}; files in {out}")


if __name__ == "__main__":
    main()
