"""Step 5: per-pixel materials -> one hue-shifted ramp per material, fitted to the colour budget.

Labels come from the semantic material map (matmap step) when it is usable: each sprite pixel takes the
material that covers most of its source block. Otherwise colour clusters are labelled by a model (dry run:
grouped by hue). Colour clusters are always computed for the fidelity safety net (extra palette colours)."""
import json

import numpy as np
from PIL import Image

from .. import imaging, prompts
from ..schemas import ClusterLabel, MaterialLabels
from . import matmap


def run(ctx):
    spec = ctx.spec()
    art = ctx.art
    from canvas import parse_hex
    base = imaging.load_rgba(ctx.input("pixelize", "pixel_base"))
    w, h = base.size
    data = imaging.to_bytes(base)
    px = [tuple(data[i:i + 4]) for i in range(0, len(data), 4)]
    k = int(ctx.cfg["pixelize"]["material_clusters"])
    centers = art.extract_palette(px, k)
    m = art.Matcher(centers)
    cluster_of = [m.nearest_index(p[:3]) if p[3] else None for p in px]
    counts = [0] * len(centers)
    for c in cluster_of:
        if c is not None:
            counts[c] += 1

    idx_map, legend = matmap.load(ctx)
    if idx_map is not None:
        labelmap = _labels_from_map(ctx, idx_map, legend, px, w, h)
        ctx.info["label_source"] = "material map"
        mat_of_cluster = None
    else:
        labelmap, mat_of_cluster = _labels_from_clusters(ctx, spec, art, centers, counts, cluster_of, w, h)
        ctx.info["label_source"] = "colour clusters" + (" (material map unusable)" if legend else "")

    px_of = {}
    for i, name in enumerate(labelmap):
        if name is not None:
            px_of.setdefault(name, []).append(i)
    focal = _focal_materials(legend, spec)
    families, fam_ramps = build_palette(art, px, px_of, focal, budget=int(spec.max_colors) - 1,
                                        max_steps=int(ctx.style["ramp_steps"]) + 1,
                                        hue_shift=float(ctx.style["hue_shift"]))
    fam_of = {m: f for f, ms in families.items() for m in ms}
    materials = []
    for name in sorted(px_of, key=lambda n: -len(px_of[n])):
        f = fam_of[name]
        ramp = fam_ramps[f]["ramp"]
        materials.append({"name": name, "family": f, "pixels": len(px_of[name]), "base": fam_ramps[f]["base"],
                          "base_index": fam_ramps[f]["base_index"],
                          "ramp": ["#%02x%02x%02x" % tuple(c) for c in ramp]})
    ctx.info["families"] = {f: {"materials": ms, "steps": len(fam_ramps[f]["ramp"])} for f, ms in families.items()}
    # fidelity safety net: cluster colours that no ramp can reproduce become extra palette colours
    # (within the colour budget), so a wrong label can never force a pixel far from its concept colour
    thr = float(ctx.cfg["pixelize"].get("fidelity_threshold", 0.10))
    ramp_labs = [art.rgb_to_lab(parse_hex(hx)[:3]) for m_ in materials for hx in m_["ramp"]]
    room = int(spec.max_colors) - 1 - len(ramp_labs)
    extras = []
    for i in sorted(range(len(centers)), key=lambda i: -counts[i]):
        if not counts[i] or room <= 0:
            continue
        lab = art.rgb_to_lab(centers[i])
        if min(sum((a - b) ** 2 for a, b in zip(lab, r)) ** 0.5 for r in ramp_labs) > thr:
            extras.append("#%02x%02x%02x" % centers[i])
            ramp_labs.append(lab)
            room -= 1
    ctx.save_json("materials", "materials.json", {"materials": materials, "extras": extras, "cluster_colors":
                  ["#%02x%02x%02x" % c for c in centers], "cluster_material": mat_of_cluster,
                  "label_source": ctx.info["label_source"]})
    ctx.save_json("labelmap", "labelmap.json", {"width": w, "height": h, "labels": labelmap})
    ctx.save_image("ramps", "ramps.png", _ramps_sheet(materials))
    ctx.save_image("labels", "labels.png", _label_preview(w, h, labelmap, materials))
    ctx.info.update(materials=[m_["name"] for m_ in materials], extras=len(extras),
                    colors=sum(len(r["ramp"]) for r in fam_ramps.values()) + len(extras) + 1, clusters=len(centers))


FOCAL_WORDS = ("skin", "face", "eye", "hair", "mouth", "lip")


def _focal_materials(legend, spec):
    """Materials that keep their own ramp: priority-2 materials from the material list / spec, plus names that
    are obviously identity-critical (skin, eyes, hair)."""
    out = set()
    for m in (legend or {}).get("materials", []) + [m.model_dump() for m in spec.materials]:
        if m.get("priority", 1) >= 2:
            out.add(m["name"])
    return out, FOCAL_WORDS


def build_palette(art, px, px_of, focal, budget, max_steps=6, hue_shift=24.0):
    """Shared colour families + area-weighted ramp steps.

    1. Materials whose lit tones share hue and saturation (all steel parts, cloak + boots...) form one colour
       family with one ramp, as a pixel artist would share ramps; focal materials keep their own family.
    2. Every family starts with 2 steps; remaining colours go one at a time to the family where an extra step
       removes the most quantisation error (pixels x lightness spread / steps).
    3. Each family's steps sit at the lightness levels its pixels actually use (1-D k-means), coloured as a
       hue-shifted ramp around the family's lit tone.
    If even 2 steps per family exceed the budget, the closest families are merged until it fits.
    Returns ({family: [materials]}, {family: {"ramp", "base", "base_index"}})."""
    focal_names, focal_words = focal

    def is_focal(name):
        return name in focal_names or any(w_ in name.lower() for w_ in focal_words)

    tone = {m: _lit_tone(art, [px[i][:3] for i in idx]) for m, idx in px_of.items()}
    lch = {m: art.rgb_to_lch(tone[m]) for m in px_of}

    def sat(L, C):
        # chroma relative to lightness: dark colours have low absolute chroma but can be clearly blue or brown
        return C / max(L, 0.25)

    def dist(a, b):
        """< 1 = may share a ramp. Neutrals (greys/silvers) share with neutrals only; coloured materials need
        close hue AND similar saturation (a vivid crystal must not join a dull robe of similar hue)."""
        import math
        (La, Ca, Ha), (Lb, Cb, Hb) = a, b
        # only true greys share regardless of tint; slightly tinted colours (pale tan wood) are compared by hue,
        # otherwise a warm wooden bow joins a grey bowstring and loses its warmth
        na, nb = sat(La, Ca) < 0.035, sat(Lb, Cb) < 0.035
        if na and nb:
            return 0.3 + abs(sat(La, Ca) - sat(Lb, Cb)) * 5
        if na != nb:
            return 2.0
        dh = abs((Ha - Hb + 180) % 360 - 180) / 25.0
        dc = abs(math.log(max(Ca, 1e-3) / max(Cb, 1e-3))) / math.log(1.6)
        return dh + 0.5 * dc

    families = {}
    for m in sorted(px_of, key=lambda m: -len(px_of[m])):
        if is_focal(m):
            families[m] = [m]
            continue
        best = None
        for f, ms in families.items():
            if is_focal(ms[0]):
                continue
            d = dist(lch[m], lch[ms[0]])
            if d < 1.0 and (best is None or d < best[0]):
                best = (d, f)
        if best:
            families[best[1]].append(m)
        else:
            families[m] = [m]
    # too many families for the colour budget: merge the closest non-focal pairs
    while 2 * len(families) > budget:
        cands = [(dist(lch[a[0]], lch[b[0]]), fa, fb) for fa, a in families.items() for fb, b in families.items()
                 if fa < fb and not is_focal(a[0]) and not is_focal(b[0])]
        if not cands:
            break
        _, fa, fb = min(cands)
        families[fa] += families.pop(fb)

    Ls = {f: [art.rgb_to_lab(px[i][:3])[0] for m in ms for i in px_of[m]] for f, ms in families.items()}

    def spread(f):
        v = sorted(Ls[f])
        return max(0.03, v[int(0.95 * (len(v) - 1))] - v[int(0.05 * (len(v) - 1))])

    steps = {f: 2 if len(Ls[f]) >= 4 else 1 for f in families}
    while sum(steps.values()) < budget:
        open_ = [f for f in families if steps[f] < max_steps and steps[f] < len(Ls[f])]
        if not open_:
            break
        f = max(open_, key=lambda f: len(Ls[f]) * spread(f) / steps[f])
        steps[f] += 1
    ramps = {}
    for f, ms in families.items():
        levels = art.lightness_levels(Ls[f], steps[f])
        base = _lit_tone(art, [px[i][:3] for m in ms for i in px_of[m]])
        ramp = art.ramp_for_levels(base, levels, hue_shift)
        L0 = art.rgb_to_lab(base)[0]
        ramps[f] = {"ramp": ramp, "base": "#%02x%02x%02x" % tuple(base),
                    "base_index": min(range(len(levels)), key=lambda i: abs(levels[i] - L0))}
    return families, ramps


def _lit_tone(art, colors):
    """Local colour of a material = its lit tone: mean OKLab colour of the 60th-90th lightness percentile.
    (The most common colour is often a shadow tone, which makes ramps muddy.)"""
    labs = sorted((art.rgb_to_lab(c) for c in colors), key=lambda l: l[0])
    n = len(labs)
    sel = labs[int(n * 0.6):max(int(n * 0.6) + 1, int(n * 0.9))] or labs
    mean = tuple(sum(l[j] for l in sel) / len(sel) for j in range(3))
    return art.lab_to_rgb(mean)


def _labels_from_map(ctx, idx_map, legend, px, w, h):
    with open(ctx.input("pixelize", "geom"), encoding="utf-8") as f:
        geom = json.load(f)
    grid = imaging.block_labels(idx_map, geom)
    if grid.shape != (h, w):
        raise ValueError(f"material map grid {grid.shape} does not match sprite {h}x{w}")
    opaque = np.array([p[3] > 0 for p in px]).reshape(h, w)
    grid = imaging.fill_unlabelled(grid, opaque)
    names = [m["name"] for m in legend["materials"]]
    out = []
    for i, p in enumerate(px):
        g = grid[i // w, i % w]
        out.append(None if not p[3] else (names[g] if g >= 0 else "misc"))
    return out


def _labels_from_clusters(ctx, spec, art, centers, counts, cluster_of, w, h):
    swatch = _swatch_sheet(w, h, cluster_of, centers)
    sw_path = ctx.save_image("clusters", "clusters.png", swatch)
    names = [mt.name for mt in spec.materials]

    def stub():
        return MaterialLabels(labels=_group_by_hue(art, centers, counts, spec))

    labels = ctx.backend.llm.structured(
        "labeler", prompts.LABEL_MATERIALS.format(materials=", ".join(names) or "(free names)"),
        json.dumps({"clusters": [{"id": i, "color": "#%02x%02x%02x" % c, "pixels": counts[i]} for i, c in enumerate(centers)]}),
        [sw_path], MaterialLabels, stub)
    mat_of_cluster = {l.cluster: l.material for l in labels.labels}
    missing = [i for i in range(len(centers)) if i not in mat_of_cluster and counts[i]]
    if missing:
        raise ValueError(f"clusters {missing} were not labelled")
    return [mat_of_cluster[c] if c is not None else None for c in cluster_of], mat_of_cluster


def _label_preview(w, h, labelmap, materials):
    from canvas import parse_hex
    col = {m["name"]: parse_hex(m["base"]) for m in materials}
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pxs = im.load()
    for i, name in enumerate(labelmap):
        if name is not None:
            pxs[i % w, i // w] = col.get(name, (255, 0, 255, 255))
    return im


def _group_by_hue(art, centers, counts, spec):
    """Dry-run labelling: clusters with similar hue/chroma (any lightness) form one material.
    If the spec lists materials with base colours, snap to the nearest of those instead."""
    hinted = [(mt.name, mt.base_color) for mt in spec.materials if mt.base_color]
    if hinted:
        from canvas import parse_hex
        labs = [(n, art.rgb_to_lch(parse_hex(c)[:3])) for n, c in hinted]
        out = []
        for i, c in enumerate(centers):
            L, C, H = art.rgb_to_lch(c)
            name = min(labs, key=lambda t: _lch_dist((L, C, H), t[1]))[0]
            out.append(ClusterLabel(cluster=i, material=name))
        return out
    groups = []  # [(L, C, H) of the group's biggest cluster, [cluster ids]]
    for i in sorted(range(len(centers)), key=lambda i: -counts[i]):
        lch = art.rgb_to_lch(centers[i])
        for g in groups:
            if _same_material(lch, g[0]):
                g[1].append(i)
                break
        else:
            groups.append((lch, [i]))
    out = []
    for gi, (_, ids) in enumerate(groups):
        for i in ids:
            out.append(ClusterLabel(cluster=i, material=f"mat{gi}"))
    return out


def _same_material(a, b):
    La, Ca, Ha = a
    Lb, Cb, Hb = b
    # Conservative on purpose: wrongly merging two materials (skin + hair) ruins colours, while splitting
    # one material into two only costs a few palette slots. Real labelling is the model's job (M1).
    if Ca < 0.035 and Cb < 0.035:          # both near-grey
        return abs(La - Lb) < 0.15
    if (Ca < 0.035) != (Cb < 0.035):
        return False
    dh = abs((Ha - Hb + 180) % 360 - 180)
    return dh < 14 and abs(Ca - Cb) < 0.05 and abs(La - Lb) < 0.22


def _lch_dist(a, b):
    dh = abs((a[2] - b[2] + 180) % 360 - 180) / 180.0
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 * 4 + (dh * max(a[1], b[1]) * 4) ** 2


def _swatch_sheet(w, h, cluster_of, centers):
    """One tile per cluster: the sprite with that cluster highlighted, for the labelling model."""
    tiles, labels = [], []
    for i, c in enumerate(centers):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pxs = im.load()
        for k, cl in enumerate(cluster_of):
            if cl is None:
                continue
            x, y = k % w, k // w
            pxs[x, y] = (*c, 255) if cl == i else (205, 205, 205, 255)
        tiles.append(im)
        labels.append(f"#{i} #%02x%02x%02x" % c)
    return imaging.contact_sheet(tiles, labels, zoom_factor=3, cols=min(5, len(tiles)))


def _ramps_sheet(materials):
    rows = len(materials)
    n = max(len(m["ramp"]) for m in materials)
    im = Image.new("RGBA", (n * 16, rows * 16), (255, 255, 255, 255))
    from canvas import parse_hex
    for r, m in enumerate(materials):
        for i, hx in enumerate(m["ramp"]):
            tile = Image.new("RGBA", (16, 16), parse_hex(hx))
            im.paste(tile, (i * 16, r * 16))
    return im
