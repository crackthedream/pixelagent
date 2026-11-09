"""Constrained region rewriting: the core of the detail pass and the repair loop.

A model sees one small region (text grid + zoomed images + the matching concept crop) and returns the region
as rows of palette characters. Several candidates are generated; a picker chooses among them with the
unchanged original always offered as #0, so 'no change' is a valid outcome. Edits can only touch the region
and can only use palette colours."""
from .names import safe_filename
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import imaging, prompts
from .schemas import Pick, RegionEdit, RegionVariants

KEY_CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!#$%&*+<>?@^~"


# ------------------------------------------------------------------ palette legend

def legend(palette):
    """palette {name: rgba} -> (char -> rgb, rgb -> char, text legend). Colours ordered by material and shade."""
    items = [(k, tuple(v[:3])) for k, v in palette.items() if v[3] > 0]
    char_of, rgb_of, lines = {}, {}, []
    for i, (name, rgb) in enumerate(items[:len(KEY_CHARS)]):
        if rgb in char_of:
            continue
        ch = KEY_CHARS[len(rgb_of)]
        char_of[rgb] = ch
        rgb_of[ch] = rgb
        lines.append(f"{ch} = #%02x%02x%02x ({name})" % rgb)
    return rgb_of, char_of, "\n".join(lines)


def region_grid(data, w, h, region, char_of):
    x, y, rw, rh = region
    tens = "".join(str(((x + i) // 10) % 10) if (x + i) % 10 == 0 or i == 0 else " " for i in range(rw))
    ones = "".join(str((x + i) % 10) for i in range(rw))
    pad = len(str(y + rh))
    out = [" " * (pad + 1) + tens, " " * (pad + 1) + ones]
    for yy in range(y, y + rh):
        row = []
        for xx in range(x, x + rw):
            i = (yy * w + xx) * 4
            if data[i + 3] == 0:
                row.append(".")
            else:
                row.append(char_of.get(tuple(data[i:i + 3]), "?"))
        out.append(str(yy).rjust(pad) + " " + "".join(row))
    return "\n".join(out)


def clamp_region(region, w, h, max_side):
    x, y, rw, rh = (int(v) for v in region)
    rw, rh = max(1, min(rw, max_side)), max(1, min(rh, max_side))
    x, y = max(0, min(x, w - rw)), max(0, min(y, h - rh))
    return x, y, min(rw, w - x), min(rh, h - y)


def apply_rows(data, w, h, region, rows, rgb_of):
    """Return a copy of data with the region replaced by rows. Raises ValueError on malformed rows."""
    x, y, rw, rh = region
    if len(rows) != rh:
        raise ValueError(f"expected {rh} rows, got {len(rows)}")
    out = bytearray(data)
    for j, row in enumerate(rows):
        row = row.rstrip("\n")
        if len(row) == rw - 1:      # models occasionally drop/duplicate one character: keep the last pixel
            row += "="
        elif len(row) == rw + 1:
            row = row[:rw]
        if len(row) != rw:
            raise ValueError(f"row {j} has {len(row)} characters, expected {rw}")
        for i, ch in enumerate(row):
            k = ((y + j) * w + (x + i)) * 4
            if ch in "=?":  # keep ('?' marks a current colour outside the palette)
                continue
            if ch == ".":
                out[k:k + 4] = b"\0\0\0\0"
            elif ch in rgb_of:
                out[k:k + 4] = bytes(rgb_of[ch]) + b"\xff"
            else:
                raise ValueError(f"unknown palette key '{ch}' in row {j}")
    return out


# ------------------------------------------------------------------ images for the model

def grid_image(im, zoom=8, step=8, region=None, label=True):
    """Zoomed sprite with a light pixel grid, stronger lines every `step` px, coordinate labels and an
    optional magenta region box."""
    z = imaging.zoom(im, zoom)
    bg = imaging.checker(z.width, z.height, zoom)
    bg.alpha_composite(z)
    mx = 22 if label else 0
    canvas = Image.new("RGBA", (bg.width + mx, bg.height + mx), (255, 255, 255, 255))
    canvas.paste(bg, (mx, mx))
    d = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for X in range(im.width + 1):
        strong = X % step == 0
        d.line([(mx + X * zoom, mx), (mx + X * zoom, canvas.height)],
               fill=(70, 90, 200, 255) if strong else (0, 0, 0, 40), width=1)
        if label and strong and X < im.width:
            d.text((mx + X * zoom + 1, 4), str(X), fill=(20, 20, 20, 255), font=font)
    for Y in range(im.height + 1):
        strong = Y % step == 0
        d.line([(mx, mx + Y * zoom), (canvas.width, mx + Y * zoom)],
               fill=(70, 90, 200, 255) if strong else (0, 0, 0, 40), width=1)
        if label and strong and Y < im.height:
            d.text((2, mx + Y * zoom + 1), str(Y), fill=(20, 20, 20, 255), font=font)
    if region:
        x, y, rw, rh = region
        d.rectangle([mx + x * zoom, mx + y * zoom, mx + (x + rw) * zoom, mx + (y + rh) * zoom],
                    outline=(255, 0, 255, 255), width=3)
    return canvas


def concept_crop(concept, geom, region, pad=1):
    """The area of the high-resolution concept that maps onto the sprite region (via the pixel-grid geometry)."""
    x, y, rw, rh = region
    s, m = geom["s"], geom["margin"]
    cx, cy = geom["crop"][0], geom["crop"][1]
    x0 = geom["gx0"] + (x - pad - m + cx) * s
    y0 = geom["gy0"] + (y - pad - m + cy) * s
    x1 = geom["gx0"] + (x + rw + pad - m + cx) * s
    y1 = geom["gy0"] + (y + rh + pad - m + cy) * s
    box = (max(0, int(x0)), max(0, int(y0)), min(concept.width, int(np.ceil(x1))), min(concept.height, int(np.ceil(y1))))
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    crop = concept.crop(box)
    if max(crop.size) < 256:
        f = 256 / max(crop.size)
        crop = crop.resize((max(1, int(crop.width * f)), max(1, int(crop.height * f))), Image.LANCZOS)
    bg = Image.new("RGBA", crop.size, (230, 230, 230, 255))
    bg.alpha_composite(crop)
    return bg


def candidates_sheet(w, h, datas, region, ctx_pad=5, zoom=8):
    x, y, rw, rh = region
    box = (max(0, x - ctx_pad), max(0, y - ctx_pad), min(w, x + rw + ctx_pad), min(h, y + rh + ctx_pad))
    tiles = []
    for d in datas:
        im = imaging.from_bytes(w, h, d)
        area = grid_image(im.crop(box), zoom, 64, None, label=False)
        full = imaging.zoom(im, 2)
        tile = Image.new("RGBA", (max(area.width, full.width), area.height + full.height + 6), (255, 255, 255, 255))
        tile.paste(area, (0, 0))
        fb = imaging.checker(full.width, full.height, 4)
        fb.alpha_composite(full)
        tile.paste(fb, (0, area.height + 6))
        tiles.append(tile)
    return imaging.contact_sheet(tiles, [f"#{i}" + (" current" if i == 0 else "") for i in range(len(tiles))], 1)


# ------------------------------------------------------------------ the rewrite itself

def rewrite_region(ctx, data, w, h, palette, region, name, goal, concept=None, geom=None, n=3, tag="r",
                   ref=None, fidelity_tol=0.05):
    """Generate n candidate rewrites of `region` (one model call), drop candidates that drift from the
    concept reference `ref` (sprite-grid rendering of the concept), let a picker choose (or keep #0 = current).
    Returns (new data, info dict). Candidate images go to ctx.out."""
    art = ctx.art
    fname = safe_filename(name, "region")  # region names come from the model: never trust them as file names
    rgb_of, char_of, legend_txt = legend(palette)
    x, y, rw, rh = region
    sprite = imaging.from_bytes(w, h, data)
    ctx_img = ctx.path(f"{tag}_{fname}_context.png")
    grid_image(sprite, 6, 8, region).save(ctx_img)
    reg_img = ctx.path(f"{tag}_{fname}_region.png")
    grid_image(sprite.crop((x, y, x + rw, y + rh)), 16, 64, None, label=False).save(reg_img)
    images = [ctx_img, reg_img]
    if concept is not None and geom is not None:
        cc = concept_crop(concept, geom, region)
        if cc is not None:
            cp = ctx.path(f"{tag}_{fname}_concept.png")
            cc.save(cp)
            images.append(cp)
    grid_txt = region_grid(data, w, h, region, char_of)
    spec = ctx.spec()
    instr = prompts.REWRITE_REGION.format(sprite_w=w, sprite_h=h, name=name, x=x, y=y, w=rw, h=rh, goal=goal,
                                          legend=legend_txt, light=spec.light, grid=grid_txt)
    base_rows = grid_txt.splitlines()[2:]
    base_rows = [r.split(" ", 1)[1] for r in base_rows]

    def stub():
        tmp = bytearray(data)
        art.cleanup(w, h, tmp, doubles=False, orphans=True, region=region)
        rows = [r.split(" ", 1)[1] for r in region_grid(tmp, w, h, region, char_of).splitlines()[2:]]
        return RegionVariants(variants=[RegionEdit(rows=base_rows, rationale="unchanged (dry run)"),
                                        RegionEdit(rows=rows, rationale="orphan cleanup (dry run)")][:max(1, n)])

    # one call returns all variants: the images, grid and reasoning are paid for once instead of n times
    styles = ["conservative: change only what the goal needs",
              "bolder: restructure the clusters if it reads better",
              "an alternative approach to the same goal"]
    ask = (f"Return exactly {n} different variants. " +
           " ".join(f"Variant {k + 1}: {styles[min(k, len(styles) - 1)]}." for k in range(n)))
    cands, errors, rationales = [bytes(data)], [], []
    try:
        out = ctx.backend.llm.structured("detail", instr, ask, images, RegionVariants, stub)
        variants = out.variants[:n]
    except ValueError as e:
        variants, errors = [], [str(e)]
    for k, edit in enumerate(variants):
        try:
            new = apply_rows(data, w, h, region, edit.rows, rgb_of)
        except ValueError as e:
            errors.append(f"variant {k + 1}: {e}")
            continue
        if new == bytearray(data) or any(bytes(new) == c for c in cands):
            continue
        cands.append(bytes(new))
        rationales.append(edit.rationale)
    info = {"region": [x, y, rw, rh], "goal": goal, "candidates": len(cands) - 1, "errors": errors}

    # anchor to the concept: a variant may not move the region clearly further from the concept (rendered on
    # the same pixel grid) or delete concept pixels -- whatever the critic or picker think of it
    fid = None
    if ref is not None:
        fid = [art.fidelity(w, h, bytearray(c), ref, region) for c in cands]
        keep = [0]
        for i in range(1, len(cands)):
            worse = fid[i]["delta_e"] - fid[0]["delta_e"]
            lost = fid[i]["missing"] - fid[0]["missing"]
            if worse > fidelity_tol or lost > 0.08:
                info.setdefault("rejected_by_fidelity", []).append(
                    {"variant": i, "delta_e_increase": round(worse, 4), "missing_increase": round(lost, 4)})
            else:
                keep.append(i)
        cands = [cands[i] for i in keep]
        rationales = [rationales[i - 1] for i in keep[1:]]
        fid = [fid[i] for i in keep]
    if len(cands) == 1:
        info["picked"] = 0
        return bytearray(data), info
    sheet_path = ctx.path(f"{tag}_{fname}_candidates.png")
    candidates_sheet(w, h, cands, region).save(sheet_path)
    pal = [v[:3] for v in palette.values() if v[3] > 0]
    scores = [art.lint(w, h, bytearray(c), pal)["score"] for c in cands]

    def pick_stub():
        best = max(range(len(cands)), key=lambda i: (scores[i], -i))
        return Pick(index=best, reason="best lint score (dry run)")

    facts = {"lint_scores": scores}
    if fid:
        facts["concept_distance"] = [f["delta_e"] for f in fid]  # lower = closer to the concept
    pick = ctx.backend.llm.structured("vision_pick", prompts.PICK_REGION.format(name=name, goal=goal),
                                      json.dumps(facts), [sheet_path], Pick, pick_stub)
    i = pick.index if 0 <= pick.index < len(cands) else 0
    # guard: never accept a candidate that clearly hurts the objective numbers
    if i and scores[i] < scores[0] - 8:
        info["rejected_by_lint"] = i
        i = 0
    info.update(picked=i, reason=pick.reason, lint_scores=scores,
                rationale=rationales[i - 1] if i else "")
    if fid:
        info["concept_distance"] = [f["delta_e"] for f in fid]
    return bytearray(cands[i]), info


def write_overlay(ctx, doc, new, layer_name):
    """Put the difference between doc's composite and `new` into an overlay layer (erasures go to all layers)."""
    from .steps.build import _overlay
    return _overlay(ctx, doc, new, layer_name)
