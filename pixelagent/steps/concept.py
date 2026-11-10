"""Step 2: concept image.
Reference -> cleaned (background removed, cropped); optionally also an image-model redraw as a second candidate.
Prompt    -> N generated drafts.
Gate B: the user picks one (skipped when there is only one candidate). Optionally the pick is refined."""
import shutil

from .. import imaging, prompts, synth


def _synth_stub(k, out_dir):
    paths = []
    for i in range(k):
        im, _ = synth.draw_character(seed=i, background=(200, 200, 204))
        p = f"{out_dir}/gen{i:02d}.png"
        im.save(p)
        paths.append(p)
    return paths


def _clean(path):
    return imaging.crop_to_content(imaging.remove_background(imaging.load_rgba(path)))


def reference_usable(path):
    """Can this reference be converted as it is? Only when its background is plain enough to cut away: then the
    figure covers a modest part of the image and touches no edge. Typical illustrations (scenery, cropped at the
    waist) fail this, and converting them would turn the whole scene into the sprite. Returns (usable, facts)."""
    import numpy as np
    a = np.asarray(imaging.remove_background(imaging.load_rgba(path)))[..., 3] > 0
    edges = max(a[0].mean(), a[-1].mean(), a[:, 0].mean(), a[:, -1].mean())
    cover = float(a.mean())
    return bool(cover <= 0.6 and edges <= 0.05), {"figure_cover": round(cover, 3), "edge_cover": round(float(edges), 3)}


def _revise(ctx, fix):
    """A revision of a finished character (reduce finish): the approved concept is edited with the note -- inside the
    area only when one was given (masked edit, then only that area is taken back, so nothing else can move) -- and
    becomes the concept again without another approval (the user asked for the change)."""
    import json

    from PIL import Image
    base = imaging.load_rgba(ctx.asset.path(fix["from_concept"]))
    bw, bh = base.size
    W, H = (int(v) for v in ctx.cfg.get("images", {}).get("draft_size", "1024x1536").split("x"))
    native = ctx.cfg.get("finish", {}).get("mode") == "native"
    if native:   # a pixel-art concept is sent as it is: any scaling would blur its blocks
        W, H = bw, bh
    k = 1.0 if native else min(W * 0.92 / bw, H * 0.92 / bh)
    sw, sh = max(1, round(bw * k)), max(1, round(bh * k))
    ox, oy = (W - sw) // 2, (H - sh) // 2
    canvas = Image.new("RGBA", (W, H), (222, 222, 226, 255))
    canvas.alpha_composite(base if native else base.resize((sw, sh), Image.LANCZOS), (ox, oy))
    src = ctx.path("revise_source.png")
    canvas.convert("RGB").save(src)
    mask, cbox = None, None
    if fix.get("box") and fix.get("geom"):
        g = fix["geom"]
        x, y, w, h = fix["box"]
        m, s = g["margin"], g["s"]
        X0 = g["gx0"] + (x - 1 - m + g["crop"][0]) * s
        Y0 = g["gy0"] + (y - 1 - m + g["crop"][1]) * s
        X1 = g["gx0"] + (x + w + 1 - m + g["crop"][0]) * s
        Y1 = g["gy0"] + (y + h + 1 - m + g["crop"][1]) * s
        cbox = (max(0, int(X0)), max(0, int(Y0)), min(bw, int(X1 + 1)), min(bh, int(Y1 + 1)))
        mk = Image.new("RGBA", (W, H), (255, 255, 255, 255))
        hole = Image.new("RGBA", (round((cbox[2] - cbox[0]) * k), round((cbox[3] - cbox[1]) * k)), (0, 0, 0, 0))
        mk.paste(hole, (ox + round(cbox[0] * k), oy + round(cbox[1] * k)))
        mask = ctx.path("revise_mask.png")
        mk.save(mask)
    scope = "Change only the part of the image inside the transparent area of the mask.\n" if mask else ""
    prompt = prompts.REVISE_CONCEPT.format(note=fix["note"], scope=scope)
    with open(ctx.path("concept_prompt.txt"), "w", encoding="utf-8") as f:
        f.write(prompt)
    out = ctx.backend.images.generate("image_final", prompt, 1, ctx.path("generated"), [src],
                                      lambda n, d: [shutil.copyfile(src, f"{d}/gen00.png") or f"{d}/gen00.png"],
                                      mask=mask)
    edited = imaging.load_rgba(out[0])
    if edited.size != (W, H):
        edited = edited.resize((W, H), Image.NEAREST if native else Image.LANCZOS)
    if cbox:
        back = edited.crop((ox, oy, ox + sw, oy + sh)).resize((bw, bh), Image.NEAREST if native else Image.LANCZOS)
        if not native:   # a pixel-art concept keeps its flat background (pixelize cuts it away)
            back = imaging.remove_background(back)
        result = base.copy()
        result.paste(back.crop(cbox), cbox[:2])           # only the area changes: the rest is the approved concept
    elif native:
        result = edited                         # the whole edited pixel-art sheet (same size, crisp blocks)
    else:
        result = _clean(out[0])
    ctx.save_image("candidate0", "concept_00.png", result)
    ctx.outputs["concept"] = ctx.save_image("concept", "concept.png", result)
    ctx.info.update(candidates=1, sources=["revised"], suggested=0, picked=0,
                    revised={"note": fix["note"], "box": fix.get("box")})
    ctx.needs_gate = False
    fix["concept_done"] = True
    json.dumps(fix)  # stays JSON-serialisable in the asset state


BLOCK = 8   # image pixels per sprite pixel asked of the image model (160 px * 8 = 1280 px, fits 1024x1536)


def _native_stub(ctx, spec):
    """Test runs: a synthetic character reduced to the sprite height and shown as crisp blocks, like the image
    model's pixel-art sprite."""
    def stub(k, out_dir):
        from ..pixelart import enlarge
        paths = []
        for i in range(k):
            im, _ = synth.draw_character(seed=i, background=None)
            small = imaging.pixelize(im, min(spec.height_px, 180), margin=0)
            p = f"{out_dir}/gen{i:02d}.png"
            enlarge(small, BLOCK).save(p)
            paths.append(p)
        return paths
    return stub


def _native(ctx, spec, refs, view):
    """The native finish: the image model draws the character as a hand-pixelled sprite (from the reference, or from
    the plan's description); pixelize then reads it back pixel for pixel. It is a new design, so it always waits
    for approval (gate B)."""
    heads = float(spec.heads_tall or ctx.style.get("heads_tall", 4.5))
    h = int(spec.height_px)
    fmt = dict(height=h, block=BLOCK, heads=f"{heads:g}", head_px=round(h / heads), face_px=round(h * 0.12),
               leg_px=max(3, round(h * 0.05)), view=view, summary=spec.summary,
               must_keep=", ".join(spec.must_keep) or "-")
    prompt = (prompts.PIXEL_SPRITE if refs else prompts.PIXEL_SPRITE_TEXT).format(**fmt)
    notes = ctx.notes()
    if notes:
        prompt += "\nChanges requested: " + "; ".join(notes)
    with open(ctx.path("concept_prompt.txt"), "w", encoding="utf-8") as f:
        f.write(prompt)
    n = 1 if refs else int(ctx.cfg["budget"]["concept_drafts"])
    out = ctx.backend.images.generate("image_final", prompt, n, ctx.path("generated"), refs[:1] or None,
                                      _native_stub(ctx, spec))
    cands = [imaging.load_rgba(p) for p in out]
    for i, im in enumerate(cands):
        ctx.save_image(f"candidate{i}", f"concept_{i:02d}.png", im)
    thumbs = [im.resize((max(1, im.width * 256 // im.height), 256)) for im in cands]
    ctx.save_image("sheet", "concepts_sheet.png",
                   imaging.contact_sheet(thumbs, [f"#{i} pixel sprite" for i in range(len(cands))], 1))
    ctx.info.update(candidates=len(cands), sources=["pixel sprite"] * len(cands), suggested=0, native=True)


def run(ctx):
    fix = ctx.asset.state.inputs.get("concept_fix")
    if fix and not fix.get("concept_done"):
        return _revise(ctx, fix)
    spec0 = ctx.spec()
    if ctx.cfg.get("finish", {}).get("mode") == "native" and spec0.asset_type == "character":
        return _native(ctx, spec0, ctx.asset.references(), spec0.view.replace("_", "-"))
    spec = ctx.spec()
    refs = ctx.asset.references()
    cands, sources = [], []
    view = spec.view.replace("_", "-")
    soft = ctx.style.get("look") == "soft" and spec.asset_type == "character"
    look = prompts.LOOK_SOFT.format(heads=spec.heads_tall or ctx.style.get("heads_tall", 4.5)) if soft else ""
    if refs:
        mode = ctx.cfg.get("concept", {}).get("clean_reference", "auto")
        usable, facts = reference_usable(refs[0])
        ctx.info["reference_check"] = {**facts, "usable_as_is": usable}
        redraw = mode is True or (mode == "auto" and not usable)  # false: convert the reference as it is
        # the soft look restyles every reference (proportions, palette); a usable reference stays a candidate
        redraw = redraw or (soft and mode is not False)
        if (usable or not redraw) and not (soft and redraw):
            cands.append(_clean(refs[0]))
            sources.append("reference")
        if redraw:
            # scenery or a partial figure: the image model redraws the character as a clean full-body concept;
            # this is an interpretation, so it always waits for the user's approval (gate B)
            if soft:
                heads = float(spec.heads_tall or ctx.style.get("heads_tall", 4.5))
                prompt = prompts.RESTYLE_REFERENCE.format(
                    heads=f"{heads:g}", head_pct=round(100 / heads), view=view, light=spec.light,
                    summary=spec.summary, must_keep=", ".join(spec.must_keep) or "-")
            else:
                prompt = prompts.CLEAN_REFERENCE.format(
                    view=view, light=spec.light, summary=spec.summary, must_keep=", ".join(spec.must_keep) or "-",
                    identity_extra=", every costume detail and the same proportions", look="")
            notes = ctx.notes()
            if notes:
                prompt += "\nChanges requested: " + "; ".join(notes)
            with open(ctx.path("concept_prompt.txt"), "w", encoding="utf-8") as f:
                f.write(prompt)
            for p in ctx.backend.images.generate("image_final", prompt, 1, ctx.path("generated"), refs[:1],
                                                 lambda k, d: [shutil.copyfile(refs[0], f"{d}/gen00.png") or f"{d}/gen00.png"]):
                cands.append(_clean(p))
                sources.append("reference redrawn")
        if soft and redraw and usable:   # after the restyled concept, so an automatic pick takes the restyled one
            cands.append(_clean(refs[0]))
            sources.append("reference")
    else:
        n = int(ctx.cfg["budget"]["concept_drafts"])
        prompt = prompts.CONCEPT.format(asset_type=spec.asset_type, view=view, background="light grey",
                                        light=spec.light, summary=spec.summary,
                                        must_keep=", ".join(spec.must_keep) or "-", look=look)
        notes = ctx.notes()
        if notes:
            prompt += "\nChanges requested: " + "; ".join(notes)
        with open(ctx.path("concept_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt)
        for p in ctx.backend.images.generate("image_draft", prompt, n, ctx.path("generated"), None, _synth_stub):
            cands.append(_clean(p))
            sources.append("generated")
    for i, im in enumerate(cands):
        ctx.save_image(f"candidate{i}", f"concept_{i:02d}.png", im)
    thumbs = [im.resize((max(1, im.width * 256 // im.height), 256)) for im in cands]
    ctx.save_image("sheet", "concepts_sheet.png",
                   imaging.contact_sheet(thumbs, [f"#{i} {s}" for i, s in enumerate(sources)], 1))
    ctx.info.update(candidates=len(cands), sources=sources, suggested=0)
    if len(cands) == 1 and sources[0] != "reference redrawn":
        ctx.needs_gate = False
        _choose(ctx, 0)


def _choose(ctx, i):
    src = ctx.outputs.get(f"candidate{i}") or ctx.input("concept", f"candidate{i}")
    dst = ctx.path("concept.png")
    if src != dst:
        shutil.copyfile(src, dst)
    ctx.outputs["concept"] = dst
    ctx.info["picked"] = i
    if ctx.cfg.get("concept", {}).get("refine_after_pick"):
        spec = ctx.spec()
        shutil.copyfile(dst, ctx.path("concept_picked.png"))
        ctx.outputs["concept_picked"] = ctx.path("concept_picked.png")
        out = ctx.backend.images.generate("image_final", prompts.REFINE_CONCEPT.format(light=spec.light), 1,
                                          ctx.path("refined"), [dst],
                                          lambda k, d: [shutil.copyfile(dst, f"{d}/gen00.png") or f"{d}/gen00.png"])
        _clean(out[0]).save(dst)
        ctx.info["refined"] = True


def after_gate(ctx, decision):
    n = ctx.asset.state.steps["concept"].info.get("candidates", 1)
    pick = decision.pick if decision.pick is not None else 0
    if not 0 <= pick < n:
        raise ValueError(f"pick must be 0..{n - 1}")
    _choose(ctx, pick)
