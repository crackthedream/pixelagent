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


def run(ctx):
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
