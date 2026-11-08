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


def rebuild(ctx):
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
    doc.palette["outline"] = oc
    merged = 0
    if style == "selout":
        pal = [v[:3] for v in doc.palette.values()]
        art.selout(doc.w, doc.h, data, 0.3, ctx.spec().light, art.Matcher(pal))
        # sel-out picks each outline pixel's colour from what lies behind it; along a line that flickers
        # between shades. Merge runs shorter than 3 px along the outline ring into their neighbours.
        ring = ["ring" if data[k * 4 + 3] and not before[k * 4 + 3] else None for k in range(doc.w * doc.h)]
        merged = art.merge_small_clusters(doc.w, doc.h, data, ring, min_size=3, max_dl=0.25, connectivity=8)
    changed, _ = _overlay(ctx, doc, data, "lines")
    ctx.save_project("project", doc)
    ctx.save_image("preview", "lines.png", imaging.from_bytes(doc.w, doc.h, doc.composite()))
    ctx.info.update(line_pixels=changed, outline_runs_merged=merged)


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
