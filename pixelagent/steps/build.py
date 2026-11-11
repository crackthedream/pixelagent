"""Steps 5, 6, 8: rebuild the sprite from material ramps, add line work, polish.
All deterministic. Results are layered engine projects; passes that need the whole picture write their
changes into their own overlay layer so every pass stays editable/removable."""
import json

from .. import imaging


def _palette_from_materials(ctx, mats):
    """One palette entry per family ramp step: key '<family><step>' (materials of a family share colours)."""
    from canvas import parse_hex
    pal = {}
    for m in mats:
        fam = m.get("family", m["name"])
        for i, hx in enumerate(m["ramp"]):
            pal[f"{fam}{i}"] = parse_hex(hx)
    return pal


def finish_mode(ctx):
    return ctx.cfg.get("finish", {}).get("mode", "artist")


def reduced(ctx):
    """Finishes whose sprite comes straight from the concept (no drawing): 'reduce' (a shrunk painting) and 'native'
    (a pixel-art concept read back)."""
    return finish_mode(ctx) in ("reduce", "native")


def native_tones(material, n):
    """Tones per material in the native finish, as hand-made sprites in the target style use them: 4-6 for most
    materials (fewer for tiny ones), 6 for focal ones (eyes, mouth)."""
    if is_focal(material):
        return 6
    return 6 if n >= 800 else 5 if n >= 200 else 4 if n >= 40 else 3


# materials whose few pixels carry the character's identity: they always get their own colours
FOCAL = ("eye", "iris", "pupil", "lash", "mouth", "lip", "tongue", "teeth", "blush", "cheek", "brow")


def _oklab(rgb):
    """sRGB (N, 3) 0-255 -> OKLab (N, 3)."""
    import numpy as np
    c = rgb / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    lms = c @ np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                        [0.2119034982, 0.6806995451, 0.1073969566],
                        [0.0883024619, 0.2817188376, 0.6299787005]]).T
    lms = np.cbrt(lms)
    return lms @ np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                           [1.9779984951, -2.4285922050, 0.4505937099],
                           [0.0259040371, 0.7827717662, -0.8086757660]]).T


def _from_oklab(lab):
    """OKLab (N, 3) -> sRGB (N, 3) uint8 (clipped to the gamut)."""
    import numpy as np
    lms = lab @ np.array([[1, 0.3963377774, 0.2158037573], [1, -0.1055613458, -0.0638541728],
                          [1, -0.0894841775, -1.2914855480]]).T
    c = (lms ** 3) @ np.array([[4.0767416621, -3.3077115913, 0.2309699292],
                               [-1.2684380046, 2.6097574011, -0.3413193965],
                               [-0.0041960863, -0.7034186147, 1.7076147010]]).T
    c = np.clip(c, 0, 1)
    c = np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)
    return np.clip(np.round(c * 255), 0, 255).astype(np.uint8)


# Palette grades of the native finish ([finish] grade). The image model's sprites are about twice as saturated as
# Alice in Cradle's characters (mean OKLab chroma 0.063 vs 0.013-0.059, ~0.033 on average) and their shadows run to
# near-black; the game's colours are dusty with lifted shadows (2026-11-11, docs/FACE_ROOT_CAUSE.md). chroma scales
# the colourfulness; lift raises the darkest tones (0 at L >= knee, `lift` at black), keeping their order.
GRADES = {"none": None, "soft": dict(chroma=0.8, lift=0.08, knee=0.55),
          "medium": dict(chroma=0.65, lift=0.14, knee=0.6), "strong": dict(chroma=0.5, lift=0.2, knee=0.65)}


def grade_colours(rgb, chroma=0.65, lift=0.14, knee=0.6):
    """Colours (N, 3) -> graded colours (N, 3) uint8: less saturated, shadows lifted (a palette mapping: every pixel
    of one colour stays one colour, so the clusters are kept exactly)."""
    import numpy as np
    lab = _oklab(np.asarray(rgb, float))
    t = np.clip(1 - lab[:, 0] / knee, 0, 1)
    lab[:, 0] += lift * t ** 1.5
    lab[:, 1:] *= chroma
    return _from_oklab(lab)


def grade_name(ctx):
    """The palette grade's name for this asset: [finish] grade for characters (default 'medium'), [finish] prop_grade
    for objects (default 'soft': loot stays readable -- gold and potion reds turned dusty under medium; the owner's
    pick, 2026-11-11). 'none' outside the native finish."""
    if finish_mode(ctx) != "native":
        return "none"
    f = ctx.cfg.get("finish", {})
    if ctx.spec().asset_type == "prop":
        name = str(f.get("prop_grade", "soft"))
    else:
        name = str(f.get("grade", "medium"))
    return name if name in GRADES else "medium"


def grade_setting(ctx):
    """The palette grade settings for this run (build.GRADES), or None."""
    return GRADES.get(grade_name(ctx))


def _kmeans(x, k, iters=12):
    """Small k-means in OKLab, seeded along lightness so dark lines and highlights get their own centres."""
    import numpy as np
    order = np.argsort(x[:, 0])
    centres = x[order[np.linspace(0, len(x) - 1, k).astype(int)]].copy()
    assign = np.zeros(len(x), int)
    for _ in range(iters):
        d = ((x[:, None, :] - centres[None, :, :]) ** 2).sum(-1)
        assign = d.argmin(1)
        for j in range(k):
            m = assign == j
            if m.any():
                centres[j] = x[m].mean(0)
    return assign


def is_focal(material):
    m = material.lower().replace("-", "_")
    return m.startswith(FOCAL) or "eye" in m or "mouth" in m


def reduce_material(rgb, k, focal=False):
    """One material's pixels (N, 3) -> (per-pixel colours (N, 3) uint8, the colours used). k-means in OKLab with
    k shades. A cluster's mean lightens its darkest members -- lines, lashes and pupils went grey (ref09,
    2026-11-10) -- so the darkest cluster takes the mean of its darkest third; focal materials also keep their
    lightest (catchlights, eye whites) from the lightest third."""
    import numpy as np
    k = int(min(k, len(np.unique(rgb, axis=0))))
    lab = _oklab(rgb)
    assign = _kmeans(lab, k) if k > 1 else np.zeros(len(rgb), int)
    means = {j: lab[assign == j, 0].mean() for j in range(k) if (assign == j).any()}
    darkest = min(means, key=means.get) if means else None
    lightest = max(means, key=means.get) if means else None
    out = np.zeros((len(rgb), 3), np.uint8)
    used = []
    for j in sorted(means):
        sel = assign == j
        members = rgb[sel]
        if len(means) > 1 and (j == darkest or (focal and j == lightest)):
            L = lab[sel, 0]
            pick = L <= np.quantile(L, 1 / 3) if j == darkest else L >= np.quantile(L, 2 / 3)
            members = members[pick] if pick.any() else members
        col = np.clip(np.round(members.mean(0)), 0, 255).astype(np.uint8)
        out[sel] = col
        used.append(col)
    return out, used


def soft_reduce(ctx):
    """Finish mode 'reduce' (the soft look): the final sprite comes from the concept's own reduction onto the grid
    (pixelize), which keeps the face, the eye colours and the soft shading that the model's redraw and a 40-colour
    ramp palette lost (docs/FACE_ROOT_CAUSE.md). Each material gets its own colours by k-means in OKLab -- a share of
    the budget by the square root of its area, at least 6 for focal materials (eyes, mouth, blush), so small
    features keep their colour; the darkest shade of every material (and the lightest of focal ones) comes from its
    extreme pixels rather than the cluster mean, so lines, lashes and pupils stay crisp -- then only near-identical
    specks merge."""
    import numpy as np
    canvas = ctx.canvas
    base = imaging.load_rgba(ctx.input("pixelize", "pixel_base"))
    w, h = base.size
    with open(ctx.input("materials", "materials"), encoding="utf-8") as f:
        mats = json.load(f)["materials"]
    with open(ctx.input("materials", "labelmap"), encoding="utf-8") as f:
        labels = json.load(f)["labels"]
    src = np.asarray(base).reshape(-1, 4)
    budget = int(ctx.cfg.get("finish", {}).get("reduce_colors", 96))
    native = finish_mode(ctx) == "native"
    grade = grade_setting(ctx)
    groups = {}
    for k, m in enumerate(labels):
        if src[k, 3]:
            groups.setdefault(m if m is not None else "_unlabelled", []).append(k)
    weight = {m: len(ix) ** 0.5 for m, ix in groups.items()}
    total = sum(weight.values()) or 1.0
    out = np.zeros_like(src)
    palette, counts = {}, {}
    for m, ix in groups.items():
        ix = np.array(ix)
        focal = is_focal(m)
        k = native_tones(m, len(ix)) if native else max(6 if focal else 2, round(budget * weight[m] / total))
        colours, cols = reduce_material(src[ix, :3].astype(float), k, focal)
        if grade and not focal and cols:              # the face (eyes, mouth, blush) keeps its drawn colours
            uniq = np.array(cols, np.uint8)
            graded = grade_colours(uniq, **grade)
            look = {tuple(int(v) for v in u): g for u, g in zip(uniq, graded)}
            colours = np.array([look[tuple(int(v) for v in c)] for c in colours], np.uint8)
            cols = list(graded)
        out[ix, :3] = colours
        out[ix, 3] = 255
        for j, col in enumerate(cols):
            palette[f"{m}{j}"] = (int(col[0]), int(col[1]), int(col[2]), 255)
        counts[m] = len(cols)
    data = bytearray(out.astype(np.uint8).tobytes())
    merged = ctx.art.merge_small_clusters(w, h, data, labels, min_size=2, max_dl=0.04)
    order = [m["name"] for m in sorted(mats, key=lambda m: m["pixels"])]
    order.reverse()
    order += [m for m in groups if m not in order]
    layers = ctx.art.split_by_labels(w, h, data, labels, order)
    doc = canvas.Document(ctx.asset.id, w, h)
    doc.layers = []
    ref = canvas.Layer("concept (reference)", w, h, imaging.to_bytes(base))
    ref.reference, ref.visible, ref.opacity = True, False, 0.5
    doc.layers.append(ref)
    for name in order:
        if name in layers:
            doc.layers.append(canvas.Layer(name, w, h, layers[name]))
    doc.palette = palette
    ctx.save_project("project", doc)
    ctx.save_image("preview", "rebuild.png", imaging.from_bytes(w, h, doc.composite()))
    ctx.info.update(mode=finish_mode(ctx), colors=len(palette), colors_per_material=counts, specks_merged=merged,
                    grade=grade_name(ctx) if grade else "none")


def rebuild(ctx):
    if reduced(ctx):
        return soft_reduce(ctx)
    art, canvas = ctx.art, ctx.canvas
    from canvas import parse_hex
    base = imaging.load_rgba(ctx.input("pixelize", "pixel_base"))
    w, h = base.size
    with open(ctx.input("materials", "materials"), encoding="utf-8") as f:
        mj = json.load(f)
    mats, extras = mj["materials"], [parse_hex(c)[:3] for c in mj.get("extras", [])]
    with open(ctx.input("materials", "labelmap"), encoding="utf-8") as f:
        labels = json.load(f)["labels"]
    ramps = {m["name"]: [parse_hex(c)[:3] for c in m["ramp"]] for m in mats}
    src = imaging.to_bytes(base)
    # Spatially coherent quantisation (not pixel by pixel):
    # 1. lightness smoothed with a 3x3 median over SAME-material neighbours (texture/aliasing speckle goes,
    #    material edges and larger light/shadow shapes stay);
    # 2. each pixel takes the ramp step whose lightness is nearest (ramp levels come from this material's
    #    own lightness distribution, see materials.build_palette);
    # 3. fidelity fallback for pixels the ramp cannot reproduce (usually a wrong label);
    # 4. same-colour clusters smaller than min_cluster merge into their neighbours inside the material.
    Ls = art.smooth_lightness(w, h, src, labels)
    rampL = {m: [art.rgb_to_lab(c)[0] for c in r] for m, r in ramps.items()}
    shaded = bytearray(w * h * 4)
    for k, m in enumerate(labels):
        if m is None or not src[k * 4 + 3]:
            continue
        lv = rampL[m]
        c = ramps[m][min(range(len(lv)), key=lambda i: abs(lv[i] - Ls[k]))]
        shaded[k * 4:k * 4 + 4] = bytes((c[0], c[1], c[2], 255))
    thr = float(ctx.cfg["pixelize"].get("fidelity_threshold", 0.10))
    full = art.Matcher([c for r in ramps.values() for c in r] + extras)
    fixed = 0
    for i in range(0, len(src), 4):
        if not shaded[i + 3]:
            continue
        a, b = art.rgb_to_lab(tuple(src[i:i + 3])), art.rgb_to_lab(tuple(shaded[i:i + 3]))
        if sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5 > thr:
            shaded[i:i + 3] = bytes(full.nearest(tuple(src[i:i + 3])))
            fixed += 1
    merged = art.merge_small_clusters(w, h, shaded, labels, int(ctx.cfg["pixelize"].get("min_cluster", 3)))
    noise = art.shade_noise(w, h, [tuple(shaded[i:i + 4]) for i in range(0, len(shaded), 4)])
    ctx.info.update(fidelity_fixed=fixed, clusters_merged=merged, shade_changes=noise["shade_changes"],
                    singletons=noise["singletons"],
                    fidelity=art.fidelity(w, h, shaded, src)["delta_e"])
    order = [m["name"] for m in sorted(mats, key=lambda m: m["pixels"])]  # big areas at the bottom
    order.reverse()
    layers = art.split_by_labels(w, h, shaded, labels, order)
    doc = canvas.Document(ctx.asset.id, w, h)
    doc.layers = []
    ref = canvas.Layer("concept (reference)", w, h, imaging.to_bytes(base))
    ref.reference, ref.visible, ref.opacity = True, False, 0.5
    doc.layers.append(ref)
    for name in order:
        doc.layers.append(canvas.Layer(name, w, h, layers[name]))
    doc.palette = _palette_from_materials(ctx, mats)
    for i, c in enumerate(extras):
        doc.palette[f"extra{i}"] = tuple(c) + (255,)
    ctx.save_project("project", doc)
    ctx.save_image("preview", "rebuild.png", imaging.from_bytes(w, h, doc.composite()))
    ctx.info["layers"] = [l.name for l in doc.layers]


def _overlay(ctx, doc, new, name):
    """Put the difference between the current composite and `new` into an overlay layer.
    Pixels that became transparent are erased from all layers instead. An existing layer of the same name is
    replaced: the diff is taken against the composite WITHOUT it, so its earlier pixels are carried over."""
    canvas = ctx.canvas
    doc.layers = [l for l in doc.layers if l.name != name]
    cur = doc.composite()
    ov = canvas.Layer(name, doc.w, doc.h)
    changed = erased = 0
    for i in range(0, len(cur), 4):
        if cur[i:i + 4] == new[i:i + 4]:
            continue
        if new[i + 3] == 0:
            for l in doc.layers:
                if not l.reference:
                    l.cels[0][i:i + 4] = b"\0\0\0\0"
            erased += 1
        else:
            ov.cels[0][i:i + 4] = new[i:i + 4]
            changed += 1
    doc.layers = doc.layers + [ov]
    return changed, erased


def lines(ctx):
    art, canvas = ctx.art, ctx.canvas
    from canvas import parse_hex
    doc = ctx.load_project("rebuild")
    style = ctx.spec().outline
    if reduced(ctx):
        # the concept's own soft edges are kept; an added outline ring would harden the soft look
        ctx.skipped = True
        ctx.info["reason"] = "[finish] mode = reduce keeps the concept's own edges"
        ctx.save_project("project", doc)
        return
    if style == "none":
        ctx.skipped = True
        ctx.info["reason"] = "spec.outline = none"
        ctx.save_project("project", doc)
        return
    oc = parse_hex(ctx.style["outline_color"])
    before = doc.composite()
    tmp = canvas.Document("tmp", doc.w, doc.h)
    tmp.layers[0].cels[0] = bytearray(before)
    canvas.Painter(tmp, 0).outline(oc, clean=True)
    data = tmp.layers[0].cels[0]
    merged = 0
    if style in ("selout", "soft"):
        # soft: a gentler rim from the materials' own shades only -- the near-black outline colour is not offered,
        # so no black line runs around the figure (the look of hand-made anime action-game sprites)
        pal = [v[:3] for v in doc.palette.values()]
        if style == "selout":
            doc.palette["outline"] = oc
            pal.append(oc[:3])
        art.selout(doc.w, doc.h, data, 0.3 if style == "selout" else 0.2, ctx.spec().light, art.Matcher(pal))
        # sel-out picks each outline pixel's colour from what lies behind it; along a line that flickers
        # between shades. Merge runs shorter than 3 px along the outline ring into their neighbours.
        ring = ["ring" if data[k * 4 + 3] and not before[k * 4 + 3] else None for k in range(doc.w * doc.h)]
        merged = art.merge_small_clusters(doc.w, doc.h, data, ring, min_size=3, max_dl=0.25, connectivity=8)
        if style == "soft":
            _no_black_ring(art, doc.w, doc.h, data, tuple(oc[:3]), art.Matcher(pal))
    else:
        doc.palette["outline"] = oc
    changed, _ = _overlay(ctx, doc, data, "lines")
    ctx.save_project("project", doc)
    ctx.save_image("preview", "lines.png", imaging.from_bytes(doc.w, doc.h, doc.composite()))
    ctx.info.update(line_pixels=changed, outline_runs_merged=merged)


def _no_black_ring(art, w, h, data, oc, matcher):
    """Soft outline: ring pixels the sel-out did not reach (it only recolours the silhouette edge) still hold the
    near-black outline colour; give each a darker shade of its most common neighbour from the palette instead."""
    for y in range(h):
        for x in range(w):
            k = (y * w + x) * 4
            if not data[k + 3] or tuple(data[k:k + 3]) != oc:
                continue
            cnt = {}
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    xx, yy = x + dx, y + dy
                    if (dx or dy) and 0 <= xx < w and 0 <= yy < h:
                        j = (yy * w + xx) * 4
                        c = tuple(data[j:j + 3])
                        if data[j + 3] and c != oc:
                            cnt[c] = cnt.get(c, 0) + 1
            if cnt:
                base = max(cnt, key=cnt.get)
                data[k:k + 3] = bytes(matcher.nearest(art.shade_color(base, -0.2))[:3])


def _pair_matcher(art, palette):
    """AA colours must come from the ramps of the two materials that meet, never from a third material."""
    ramp_of = {}
    groups = {}
    for key, c in palette.items():
        mat = key.rstrip("0123456789") or key
        groups.setdefault(mat, []).append(c[:3])
    for mat, cols in groups.items():
        for c in cols:
            ramp_of.setdefault(c, set()).update(cols)
    cache = {}

    def get(a, b):
        key = (a, b)
        if key not in cache:
            cols = ramp_of.get(a, {a}) | ramp_of.get(b, {b})
            cache[key] = art.Matcher(sorted(cols))
        return cache[key]
    return get


def polish(ctx):
    art = ctx.art
    det = ctx.asset.state.steps.get("detail")
    doc = ctx.load_project("detail" if det and det.status == "done" and "project" in det.outputs else "lines")
    if reduced(ctx):
        # anti-aliasing against ramp pairs and orphan removal would undo the soft reduction (its single pixels are
        # deliberate shading); the reduction is the finished sprite
        ctx.save_project("project", doc)
        ctx.save_image("preview", "polish.png", imaging.from_bytes(doc.w, doc.h, doc.composite()))
        ctx.info["reason"] = "[finish] mode = reduce: passed through"
        return
    data = doc.composite()
    orig = bytes(data)
    aa = art.antialias(doc.w, doc.h, data, 0.5, _pair_matcher(art, doc.palette))
    _, orphans = art.cleanup(doc.w, doc.h, data, doubles=False, orphans=True)
    # regions refined by the detail pass are deliberate (eye glints look like 'orphans'): leave them alone
    protected = det.info.get("changed_regions", []) if det and det.status == "done" else []
    restore_regions(data, orig, doc.w, protected)
    changed, erased = _overlay(ctx, doc, data, "polish")
    ctx.save_project("project", doc)
    ctx.save_image("preview", "polish.png", imaging.from_bytes(doc.w, doc.h, doc.composite()))
    ctx.info.update(antialiased=aa, orphans_removed=orphans, overlay_pixels=changed, erased=erased,
                    protected_regions=protected)


def restore_regions(data, orig, w, regions_):
    for x, y, rw, rh in regions_:
        for yy in range(y, y + rh):
            a = (yy * w + x) * 4
            data[a:a + rw * 4] = orig[a:a + rw * 4]
