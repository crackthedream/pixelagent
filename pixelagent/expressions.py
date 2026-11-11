"""Expression sets for a finished pixel-art character (the native finish).

Each expression is a masked edit of the character's pixel-art concept limited to the face (eyes, brows, mouth; the
box comes from the vision model), read back on the character's own block grid and mapped onto the finished sprite's
colours (the palette grade included). Only pixels inside the face box and inside the figure can change, so the
outline and the rest of the sprite stay pixel for pixel: a game can swap the whole sprite or just the face patch.

Outputs, in the character's folder: expressions/<name>.png (1x), <name>_x4.png, <name>_face.png (the face patch) and
expressions.json (the face box, so a game can place the patch)."""
import json
import os

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from . import imaging, prompts
from .models import make_backend
from .steps.build import _oklab, is_focal

# name -> what the face does (short, concrete: the model edits a few dozen pixels)
EXPRESSIONS = {
    "happy": "a bright happy smile: eyes curved into upward arcs, a small open smiling mouth",
    "sad": "sad: inner ends of the brows raised, eyes looking down and slightly narrowed, a small downturned mouth",
    "angry": "angry: brows slanted down toward the nose, eyes narrowed, a small frowning mouth",
    "surprised": "surprised: eyes wide open with small pupils, a small round open mouth",
    "hurt": "in pain: eyes squeezed shut into tight lines, brows knitted, mouth clenched open showing teeth",
    "blink": "eyes closed in a calm blink (each eye a short curved line), the mouth unchanged",
}


def face_box(labels, w, h, sprite=None):
    """The face area in sprite pixels (x0, y0, x1, y1). With the finished sprite, the measured face (face.face_layout:
    from the fringe to the chin, cheek to cheek, found from the skin material); labelled eyes widen it for the brows.
    None when neither is found."""
    from .artist import face_region
    from .face import face_layout
    boxes = []
    region = face_region(labels, w, h)
    if region and sprite is not None:
        L = face_layout(sprite, region)
        if L:
            boxes.append((L["left"] - 1, L["fringe_y"] - 2, L["right"] + 2, L["chin_y"] + 1))
    lab = np.array([l if l else "" for l in labels], object).reshape(h, w)
    focal = np.vectorize(lambda m: bool(m) and is_focal(m))(lab)
    ys, xs = np.nonzero(focal)
    if len(xs) >= 10:                                      # labelled eyes (stray labelled pixels trimmed)
        x0, x1 = int(np.percentile(xs, 3)), int(np.percentile(xs, 97)) + 1
        y0, y1 = int(np.percentile(ys, 3)), int(np.percentile(ys, 97)) + 1
        boxes.append((x0 - 2, y0 - 3, x1 + 2, y1 + max(4, round((x1 - x0) / 3))))
    if not boxes:
        return None
    return (int(max(0, min(b[0] for b in boxes))), int(max(0, min(b[1] for b in boxes))),
            int(min(w, max(b[2] for b in boxes))), int(min(h, max(b[3] for b in boxes))))


class FaceLocation(BaseModel):
    found: bool = Field(description="false when the face is not visible (back view, hidden)")
    x0: float = Field(description="left edge of the box around both eyes, the brows and the mouth, 0-1 of the width")
    y0: float = Field(description="top edge (the brows), 0-1 of the height")
    x1: float = Field(description="right edge, 0-1 of the width")
    y1: float = Field(description="bottom edge (just below the mouth), 0-1 of the height")


def _sprite_index(edges, v, offset):
    """An image coordinate -> the sprite pixel whose block contains it (offset = margin - crop)."""
    i = int(np.searchsorted(edges, v, side="right")) - 1
    return max(0, min(len(edges) - 2, i)) + offset


def locate_face(backend, concept_path, geom, size):
    """The face (eyes, brows, mouth) in sprite pixels, located by the vision model on the large pixel-art concept: the
    sprite's material labels cannot do it (skin is labelled on hands, a plush toy and lit hair; most characters have no
    eye material; 2026-11-11). None when the model sees no face."""
    from PIL import Image as _I
    W, H = _I.open(concept_path).size

    def stub():
        return FaceLocation(found=False, x0=0, y0=0, x1=0, y1=0)
    r = backend.llm.structured("vision_pick", prompts.LOCATE_FACE, "Locate the face.", [concept_path], FaceLocation,
                               stub)
    if not r.found or r.x1 <= r.x0 or r.y1 <= r.y0:
        return None
    g, m = geom["grid"], geom["margin"]
    ox, oy = m - geom["crop"][0], m - geom["crop"][1]
    w, h = size
    x0 = _sprite_index(g["xs"], r.x0 * W, ox) - 1
    x1 = _sprite_index(g["xs"], r.x1 * W, ox) + 2
    y0 = _sprite_index(g["ys"], r.y0 * H, oy) - 1
    y1 = _sprite_index(g["ys"], r.y1 * H, oy) + 2
    return (max(0, x0), max(0, y0), min(w, x1), min(h, y1))


def _concept_box(geom, box, pad=1):
    """A sprite-pixel box -> the concept's image-pixel box (the block edges the readback used)."""
    g = geom["grid"]
    m, cx, cy = geom["margin"], geom["crop"][0], geom["crop"][1]
    xs, ys = g["xs"], g["ys"]

    def at(edges, i):
        i = max(0, min(len(edges) - 1, i))
        return edges[i]
    X0, X1 = at(xs, box[0] - pad - m + cx), at(xs, box[2] + pad - m + cx)
    Y0, Y1 = at(ys, box[1] - pad - m + cy), at(ys, box[3] + pad - m + cy)
    return int(X0), int(Y0), int(round(X1)) + 1, int(round(Y1)) + 1


def colour_map(final, grade=None, snap=0.03):
    """A drawn colour -> the finished sprite's colour: graded like the character (`grade`: build.GRADES settings, or
    None), then snapped to the sprite's nearest colour when one is within `snap` (OKLab), so the face reuses the
    character's own tones. Colours the character does not have keep their graded hue: a happy face's pink tongue
    and purple lash arcs were turned into skin and navy when every colour was snapped (2026-11-11)."""
    from .steps.build import grade_colours
    f = np.asarray(final.convert("RGBA")).reshape(-1, 4)
    pal = np.unique(f[f[:, 3] > 0, :3], axis=0)
    lab = _oklab(pal.astype(float))
    cache = {}

    def to_final(rgb):
        key = tuple(int(v) for v in rgb)
        if key not in cache:
            c = np.array([key], np.uint8)
            g = grade_colours(c, **grade)[0] if grade else c[0]
            d = ((lab - _oklab(np.array([g], float))) ** 2).sum(1)
            cache[key] = tuple(int(v) for v in (pal[int(d.argmin())] if d.min() ** 0.5 <= snap else g))
        return cache[key]
    return to_final


def make(proj, aid, names=None, log=print, mode=None, reuse=False):
    """Draw the expressions `names` (default: all) for the finished character `aid`; returns expressions.json's dict.
    reuse: take the face box and the drawn images from an earlier run (no model calls: to redo the read-back)."""
    from .pixelart import sprite_from_pixel_art
    a = proj.asset(aid)
    if proj.config.get("finish", {}).get("mode") != "native" or "pixelize" not in a.state.steps:
        raise ValueError("expressions are made for finished pixel-art characters (the Soft look)")
    names = list(dict.fromkeys(names or EXPRESSIONS))
    for n in names:
        if n not in EXPRESSIONS:
            raise ValueError(f"unknown expression: {n}")
    concept = imaging.load_rgba(a.output("concept", "concept"))
    base = imaging.load_rgba(a.output("pixelize", "pixel_base"))
    final = imaging.load_rgba(a.output("export", "png"))
    with open(a.output("pixelize", "geom"), encoding="utf-8") as f:
        geom = json.load(f)
    with open(a.output("materials", "labelmap"), encoding="utf-8") as f:
        lm = json.load(f)
    w, h = base.size
    if final.size != (w, h) or (lm["width"], lm["height"]) != (w, h):
        raise ValueError("the finished sprite does not line up with its readback: redo the character first")
    backend = make_backend(proj, aid, mode)
    out_dir = os.path.join(a.dir, "expressions")
    path = os.path.join(out_dir, "expressions.json")
    earlier = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            earlier = json.load(f)
    box = tuple(earlier["face_box"]) if reuse and earlier.get("face_box") else         locate_face(backend, a.output("concept", "concept"), geom, (w, h))
    if box is None and (mode or proj.config["backend"]["mode"]) == "dry_run":
        box = face_box(lm["labels"], w, h, final)          # test runs: the labels' estimate, else a guess
        if box is None and final.getbbox():
            x0, y0, x1, y1 = final.getbbox()
            hh = y1 - y0
            box = (x0 + (x1 - x0) // 4, y0 + hh // 12, x1 - (x1 - x0) // 4, y0 + hh // 5)
    if box is None:
        raise ValueError("no face found in the character")
    editable = np.zeros((h, w), bool)                    # inside the face box, within the figure (its outline stays)
    editable[box[1]:box[3], box[0]:box[2]] = np.asarray(final.convert("RGBA"))[box[1]:box[3], box[0]:box[2], 3] > 0
    cbox = _concept_box(geom, box)
    os.makedirs(out_dir, exist_ok=True)
    src = Image.new("RGBA", concept.size, (222, 222, 226, 255))
    src.alpha_composite(concept)
    src_path = os.path.join(out_dir, "_source.png")
    src.convert("RGB").save(src_path)
    mask = Image.new("RGBA", concept.size, (255, 255, 255, 255))
    mask.paste(Image.new("RGBA", (cbox[2] - cbox[0], cbox[3] - cbox[1]), (0, 0, 0, 0)), cbox[:2])
    mask_path = os.path.join(out_dir, "_mask.png")
    mask.save(mask_path)
    from .steps.build import GRADES
    to_final = colour_map(final, GRADES.get(str(proj.config.get("finish", {}).get("grade", "medium"))))
    old_size = proj.config.setdefault("images", {}).get("draft_size")
    proj.config["images"]["draft_size"] = f"{concept.width}x{concept.height}"
    result = {"face_box": list(box), "size": [w, h], "expressions": {}}
    if earlier.get("face_box") and tuple(earlier["face_box"]) == tuple(box):   # the same face: earlier ones still fit
        result["expressions"] = earlier.get("expressions", {})
    try:
        for n in names:
            log(f"[expressions] {n}")
            prompt = prompts.EXPRESSION.format(expression=EXPRESSIONS[n])
            gdir = os.path.join(out_dir, "_generated", n)
            old = os.path.join(gdir, "gen00.png")
            gen = [old] if reuse and os.path.exists(old) else                 backend.images.generate("image_final", prompt, 1, gdir, [src_path], None, mask=mask_path)
            edited = imaging.load_rgba(gen[0])
            if edited.size != concept.size:
                edited = edited.resize(concept.size, Image.NEAREST)
            back = Image.new("RGBA", concept.size, (0, 0, 0, 0))     # the concept outside the face: the same grid
            back.alpha_composite(concept)
            back.paste(edited.crop(cbox), cbox[:2])
            read, _ = sprite_from_pixel_art(back, int(geom["margin"]), grid=geom["grid"])
            if read.size != (w, h):
                raise ValueError(f"{n}: the readback did not line up ({read.size} vs {(w, h)})")
            r = np.asarray(read.convert("RGBA"))
            out = np.asarray(final.convert("RGBA")).copy()
            same = (r == np.asarray(base.convert("RGBA"))).all(-1)          # untouched by the edit: kept as is
            ys, xs = np.nonzero(editable & (r[..., 3] > 0) & ~same)
            for y, x in zip(ys, xs):
                out[y, x, :3] = to_final(r[y, x, :3])
            sprite = Image.fromarray(out)
            sprite.save(os.path.join(out_dir, f"{n}.png"))
            imaging.zoom(sprite, 4).save(os.path.join(out_dir, f"{n}_x4.png"))
            sprite.crop(box).save(os.path.join(out_dir, f"{n}_face.png"))
            changed = int((np.asarray(final.convert("RGBA")) != out).any(-1).sum())
            result["expressions"][n] = {"png": f"{n}.png", "png_x4": f"{n}_x4.png", "face": f"{n}_face.png",
                                        "note": EXPRESSIONS[n], "pixels_changed": changed}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=1)
    finally:
        if old_size is None:
            proj.config["images"].pop("draft_size", None)
        else:
            proj.config["images"]["draft_size"] = old_size
    return result
