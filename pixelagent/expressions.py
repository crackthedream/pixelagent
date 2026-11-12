"""Expression sets for a finished pixel-art character (the native finish).

Each expression is a masked edit of the character's pixel-art concept limited to the face (eyes, brows, mouth; the
box comes from the vision model), read back on the character's own block grid and mapped onto the finished sprite's
colours (the palette grade included). Only pixels inside the face box and inside the figure can change, so the
outline and the rest of the sprite stay pixel for pixel: a game can swap the whole sprite or just the face patch.

An expression is a preset (EXPRESSIONS) or any description of the user's own. Each drawn face is checked against the
neutral one (the skin kept, and a vision check: reads as asked, same skin, hair and eye style) and drawn once more
with the findings as a note when it fails; the better attempt is kept.

Outputs, in the character's folder: expressions/<name>.png (1x), <name>_x4.png, <name>_face.png (the face patch) and
expressions.json (the face box, so a game can place the patch)."""
import json
import os
import re

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
    "surprised": "surprised: eyes opened wider, their irises a little smaller, brows raised, a small round open mouth",
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
    eye_colour: str = Field(default="", description="the colour of the irises in one or two plain words, for example "
                                                    "'green' or 'orange-amber'; empty when the eyes are closed or hidden")


def _sprite_index(edges, v, offset):
    """An image coordinate -> the sprite pixel whose block contains it (offset = margin - crop)."""
    i = int(np.searchsorted(edges, v, side="right")) - 1
    return max(0, min(len(edges) - 2, i)) + offset


def locate_face(backend, concept_path, geom, size, seen=None):
    """The face (eyes, brows, mouth) in sprite pixels, located by the vision model on the large pixel-art concept: the
    sprite's material labels cannot do it (skin is labelled on hands, a plush toy and lit hair; most characters have no
    eye material; 2026-11-11). None when the model sees no face. `seen` (a dict) receives the iris colour it names:
    the edit changed the irises' colour on most first attempts until the prompt named it (2026-11-12)."""
    from PIL import Image as _I
    with _I.open(concept_path) as im:
        W, H = im.size

    def stub():
        return FaceLocation(found=False, x0=0, y0=0, x1=0, y1=0)
    r = backend.llm.structured("vision_pick", prompts.LOCATE_FACE, "Locate the face.", [concept_path], FaceLocation,
                               stub)
    if not r.found or r.x1 <= r.x0 or r.y1 <= r.y0:
        return None
    if seen is not None:
        seen["eye_colour"] = " ".join(str(r.eye_colour or "").split())[:40]
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


class FaceCheck(BaseModel):
    reads: bool = Field(description="the new face clearly shows the requested expression")
    same_skin: bool = Field(description="the skin has the same colours as in the original face")
    same_hair: bool = Field(description="the hair (fringe, side locks) is unchanged")
    eyes_in_style: bool = Field(description="open eyes keep the original iris colour and drawing style (true when "
                                            "the expression closes the eyes)")
    issue: str = Field(description="what is wrong, in one short sentence; empty when all four are true")


def safe_name(text):
    """A file-safe expression name from the user's own name or description (lower case, a-z 0-9 and -)."""
    n = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:24].strip("-")
    if not n:
        raise ValueError("give the expression a name in letters or digits")
    return n


def skin_fix(read, base, where, near=0.05, limit=0.15, same=0.02):
    """Undo a tone shift of the skin, and keep what the edit did not really change. The edit sometimes repaints the
    whole face in a slightly different skin tone (ref08: pale pink became tan, validation round 2026-11-12). The
    face's main colour before, and the median of what was drawn in its place, give the shift; drawn colours near that
    median are moved back by it. Any pixel then within `same` (OKLab) of the original pixel IS the original pixel:
    an image model never returns a block's colour exactly. Returns the corrected readback."""
    from .steps.build import _from_oklab
    out = read.copy()
    ys, xs = np.nonzero(where & (out[..., 3] > 0) & (base[..., 3] > 0))
    if not len(ys):
        return out
    b = base[ys, xs, :3]
    cols, counts = np.unique(b, axis=0, return_counts=True)
    main = (b == cols[counts.argmax()]).all(1)
    lab = _oklab(out[ys, xs, :3].astype(float))
    s0 = _oklab(cols[counts.argmax()][None].astype(float))[0]
    s1 = np.median(lab[main], axis=0)
    d = s0 - s1
    shift = float((d ** 2).sum()) ** 0.5
    tight = (((lab[main] - s1) ** 2).sum(1) ** 0.5 <= near).mean()
    if same < shift <= limit and tight >= 0.5:
        hit = ((lab - s1) ** 2).sum(1) ** 0.5 <= near
        lab[hit] += d
        out[ys[hit], xs[hit], :3] = _from_oklab(lab[hit])
    back = ((lab - _oklab(b.astype(float))) ** 2).sum(1) ** 0.5 <= same
    out[ys[back], xs[back]] = base[ys[back], xs[back]]
    return out


def hair_zone(final, box, band=5, share=0.08, limit=0.55):
    """The fringe and side locks inside the face box: pixels an expression must not touch. Half of the drawn faces
    reshaped the fringe (validation round, 2026-11-12). The hair's colours are those of the rows just above the
    box; from the box's top edge down each column, and from its side edges inward along each row, the zone runs
    through hair-coloured pixels and stops at the first other one, so eyes and brows that have skin around them
    stay editable. No zone when the hair cannot be told from the skin, or when it would cover most of the box."""
    h, w = final.shape[:2]
    x0, y0, x1, y1 = box
    zone = np.zeros((h, w), bool)
    above = final[max(0, y0 - band):y0, x0:x1].reshape(-1, 4)
    above = above[above[:, 3] > 0][:, :3]
    inside = final[y0:y1, x0:x1].reshape(-1, 4)
    inside = inside[inside[:, 3] > 0][:, :3]
    if len(above) < 4 or not len(inside):
        return zone
    cols, counts = np.unique(inside, axis=0, return_counts=True)
    skin = _oklab(cols[counts.argmax()][None].astype(float))[0]
    hc, hn = np.unique(above, axis=0, return_counts=True)
    hair = [c for c, n in zip(hc, hn) if n >= share * len(above)
            and float(((_oklab(c[None].astype(float))[0] - skin) ** 2).sum()) ** 0.5 > 0.04]
    if not hair:
        return zone
    is_hair = np.zeros((h, w), bool)
    for c in hair:
        is_hair |= (final[..., :3] == c).all(-1) & (final[..., 3] > 0)
    for x in range(x0, x1):                                 # the fringe: down from the top edge
        y = y0
        while y < y1 and is_hair[y, x]:
            zone[y, x] = True
            y += 1
    for y in range(y0, y1):                                 # side locks: inward from both side edges
        x = x0
        while x < x1 and is_hair[y, x]:
            zone[y, x] = True
            x += 1
        x = x1 - 1
        while x >= x0 and is_hair[y, x]:
            zone[y, x] = True
            x -= 1
    figure = (final[y0:y1, x0:x1, 3] > 0).sum()
    return zone if zone.sum() <= limit * max(1, figure) else np.zeros((h, w), bool)


def skin_kept(final, out, where):
    """The share of the face's main colour (in the finished sprite) that a new face leaves as it was."""
    f = final[where][:, :3]
    if not len(f):
        return 1.0
    cols, counts = np.unique(f, axis=0, return_counts=True)
    main = where & (final[..., :3] == cols[counts.argmax()]).all(-1)
    return float((out[main] == final[main]).all(-1).mean())


def judge(backend, neutral, face, text, out_path):
    """The vision model's check of a drawn face against the neutral one, side by side. Both are crops of the large
    pixel-art drawings (the concept and the edit), not of the 1x sprite: at 20 pixels across the model misjudged
    eye colours and called good faces off-model (trial, 2026-11-12)."""
    k = max(1, 420 // max(1, neutral.height))
    gap = 16
    pair = Image.new("RGBA", (neutral.width * k * 2 + gap, neutral.height * k), (222, 222, 226, 255))
    pair.alpha_composite(neutral.convert("RGBA").resize((neutral.width * k, neutral.height * k), Image.NEAREST), (0, 0))
    pair.alpha_composite(face.convert("RGBA").resize((face.width * k, face.height * k), Image.NEAREST),
                         (neutral.width * k + gap, 0))
    pair.convert("RGB").save(out_path)

    def stub():
        return FaceCheck(reads=True, same_skin=True, same_hair=True, eyes_in_style=True, issue="")
    return backend.llm.structured("vision_pick", prompts.FACE_CHECK.format(expression=text), "Check the new face.",
                                  [out_path], FaceCheck, stub)


def _score(check):
    return sum(bool(check.get(k)) for k in ("reads", "same_skin", "same_hair", "eyes_in_style")) + check.get("skin_kept", 0)


def passes(check, min_skin=0.5):
    return all(check.get(k, True) for k in ("reads", "same_skin", "same_hair", "eyes_in_style")) \
        and check.get("skin_kept", 1.0) >= min_skin


def make(proj, aid, names=None, log=print, mode=None, reuse=False, custom=None, relocate=False):
    """Draw expressions for the finished character `aid`: the presets `names` (default: all, or none when `custom` is
    given) and `custom`, a dict name -> the user's own description of the face. Returns expressions.json's dict.
    The face is located once and its box kept: faces made later join the earlier ones. Locating it again moved the
    box by a pixel and dropped the six faces made before (2026-11-12). relocate: find the face again, which starts
    the set afresh.
    reuse: take the face box and the drawn images from an earlier run (no model calls: to redo the read-back)."""
    from .pixelart import sprite_from_pixel_art
    a = proj.asset(aid)
    if proj.config.get("finish", {}).get("mode") != "native" or "pixelize" not in a.state.steps:
        raise ValueError("expressions are made for finished pixel-art characters (the Soft look)")
    custom = {safe_name(k): str(v).strip() for k, v in (custom or {}).items()}
    if any(not v for v in custom.values()):
        raise ValueError("describe the expression, for example: a smug half-smile with one brow raised")
    names = list(dict.fromkeys(names if names is not None else ([] if custom else EXPRESSIONS)))
    for n in names:
        if n not in EXPRESSIONS:
            raise ValueError(f"unknown expression: {n}")
    todo = {**{n: EXPRESSIONS[n] for n in names}, **custom}
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
    ecfg = proj.config.get("expressions", {})
    out_dir = os.path.join(a.dir, "expressions")
    path = os.path.join(out_dir, "expressions.json")
    earlier = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            earlier = json.load(f)
    seen = {"eye_colour": earlier.get("eye_colour", "")}
    if earlier.get("face_box") and (reuse or not relocate):
        box = tuple(earlier["face_box"])
        if not reuse and "eye_colour" not in earlier:       # a set from before the eye colour was asked for
            locate_face(backend, a.output("concept", "concept"), geom, (w, h), seen)
    else:
        box = locate_face(backend, a.output("concept", "concept"), geom, (w, h), seen)
    if box is None and (mode or proj.config["backend"]["mode"]) == "dry_run":
        box = face_box(lm["labels"], w, h, final)          # test runs: the labels' estimate, else a guess
        if box is None and final.getbbox():
            x0, y0, x1, y1 = final.getbbox()
            hh = y1 - y0
            box = (x0 + (x1 - x0) // 4, y0 + hh // 12, x1 - (x1 - x0) // 4, y0 + hh // 5)
    if box is None:
        raise ValueError("no face found in the character")
    final_a = np.asarray(final.convert("RGBA"))
    base_a = np.asarray(base.convert("RGBA"))
    editable = np.zeros((h, w), bool)                    # inside the face box, within the figure (its outline stays)
    editable[box[1]:box[3], box[0]:box[2]] = final_a[box[1]:box[3], box[0]:box[2], 3] > 0
    hair = hair_zone(final_a, box)
    editable &= ~hair                                    # the fringe and side locks stay pixel for pixel
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
    face_size = f"{concept.width}x{concept.height}"     # passed with each call (never via the shared config)
    result = {"face_box": list(box), "size": [w, h], "eye_colour": seen["eye_colour"], "expressions": {}}
    eyes = prompts.EXPRESSION_EYES.format(colour=seen["eye_colour"]) if seen["eye_colour"] else ""
    if earlier.get("face_box") and tuple(earlier["face_box"]) == tuple(box):   # the same face: earlier ones still fit
        result["expressions"] = earlier.get("expressions", {})
    pad = max(8, (cbox[2] - cbox[0]) // 6)                 # the judge sees the face with a little of its surroundings
    view = (max(0, cbox[0] - pad), max(0, cbox[1] - pad), min(concept.width, cbox[2] + pad),
            min(concept.height, cbox[3] + pad))

    def read_back(gen_path):
        """A drawn face -> (the finished sprite with it, pixels changed, the share of skin kept, the drawing's face)."""
        edited = imaging.load_rgba(gen_path)
        if edited.size != concept.size:
            edited = edited.resize(concept.size, Image.NEAREST)
        shown = src.copy()
        shown.paste(edited.crop(cbox), cbox[:2])
        for y, x in zip(*np.nonzero(hair)):                # what the judge sees keeps the hair, as the sprite will
            hb = _concept_box(geom, (int(x), int(y), int(x) + 1, int(y) + 1), pad=0)
            shown.paste(src.crop(hb), hb[:2])
        back = Image.new("RGBA", concept.size, (0, 0, 0, 0))     # the concept outside the face: the same grid
        back.alpha_composite(concept)
        back.paste(edited.crop(cbox), cbox[:2])
        read, _ = sprite_from_pixel_art(back, int(geom["margin"]), grid=geom["grid"])
        if read.size != (w, h):
            raise ValueError(f"the readback did not line up ({read.size} vs {(w, h)})")
        r = skin_fix(np.asarray(read.convert("RGBA")), base_a, editable)
        out = final_a.copy()
        same = (r == base_a).all(-1)                               # untouched by the edit: kept as is
        ys, xs = np.nonzero(editable & (r[..., 3] > 0) & ~same)
        for y, x in zip(ys, xs):
            out[y, x, :3] = to_final(r[y, x, :3])
        return (Image.fromarray(out), int((final_a != out).any(-1).sum()), skin_kept(final_a, out, editable),
                shown.crop(view))

    for n, text in todo.items():
        log(f"[expressions] {n}")
        gdir = os.path.join(out_dir, "_generated", n)
        old = os.path.join(gdir, "gen00.png")
        best, note = None, ""
        for k in range(1 if reuse else 1 + int(ecfg.get("retries", 1))):
            if reuse and os.path.exists(old):
                gen = old
            else:
                prompt = prompts.EXPRESSION.format(expression=text) + eyes + \
                    (prompts.EXPRESSION_RETRY.format(issue=note) if note else "")
                gen = backend.images.generate("image_final", prompt, 1, gdir if k == 0 else f"{gdir}_retry{k}",
                                              [src_path], None, mask=mask_path, size=face_size)[0]
            sprite, changed, kept, drawn = read_back(gen)
            check = {"skin_kept": round(kept, 3)}
            if not reuse and ecfg.get("judge", True):
                v = judge(backend, src.crop(view), drawn, text,
                          os.path.join(out_dir, "_generated", f"{n}_check{k}.png"))
                check.update(reads=v.reads, same_skin=v.same_skin, same_hair=v.same_hair,
                             eyes_in_style=v.eyes_in_style, issue=v.issue)
            cand = {"sprite": sprite, "changed": changed, "check": check, "attempt": k + 1}
            if best is None or _score(check) >= _score(best["check"]):   # a tie: the attempt that had the note
                best = cand
            if passes(check, float(ecfg.get("min_skin_kept", 0.5))):
                break
            note = check.get("issue") or "the skin of the face changed colour: keep the skin exactly as it is"
            log(f"[expressions] {n}: {note}" + ("" if reuse else " (drawing it once more)" if k == 0 else ""))
        sprite = best["sprite"]
        sprite.save(os.path.join(out_dir, f"{n}.png"))
        imaging.zoom(sprite, 4).save(os.path.join(out_dir, f"{n}_x4.png"))
        sprite.crop(box).save(os.path.join(out_dir, f"{n}_face.png"))
        result["expressions"][n] = {"png": f"{n}.png", "png_x4": f"{n}_x4.png", "face": f"{n}_face.png",
                                    "note": text, "pixels_changed": best["changed"], "custom": n in custom,
                                    "check": best["check"], "attempts": k + 1, "kept_attempt": best["attempt"],
                                    "passed": passes(best["check"], float(ecfg.get("min_skin_kept", 0.5)))}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1)
    return result
