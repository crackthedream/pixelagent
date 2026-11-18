"""Step 10: the artist agent draws the final sprite (hybrid mode).

The polished conversion is the tracing layer (proportions, colour placement, light), the concept is the authority
for design intent, and the model redraws the sprite itself through the pixelliner MCP tools, then does a focused
face pass with a close-up of the concept's face. The result replaces the conversion as the sprite that is
critiqued and exported. [finish] mode = "repair" keeps the older detail/repair path instead."""
import json
import os
import shutil

from ..live import LivePreview
from .. import imaging, regions


def mode(ctx):
    return ctx.cfg.get("finish", {}).get("mode", "artist")


def run(ctx):
    if mode(ctx) not in ("artist", "reduce", "native"):
        ctx.skipped = True
        ctx.info["reason"] = "[finish] mode is not 'artist'"
        return
    doc = ctx.load_project("polish")
    fix = ctx.asset.state.inputs.get("fix")
    if mode(ctx) in ("reduce", "native") and not (fix and os.path.exists(os.path.join(ctx.asset.path(fix.get("from_project", "")),
                                                                         "project.json"))):
        # the reduction is the sprite: no drawing and no face pass (docs/FACE_ROOT_CAUSE.md). It is passed through as
        # this step's result, so a note at the review (or Revise) has the model edit THIS sprite, versions work as usual
        cfix = ctx.asset.state.inputs.get("concept_fix")
        if cfix and cfix.get("box"):
            if not cfix.get("from_project") or not \
                    os.path.exists(os.path.join(ctx.asset.path(cfix["from_project"]), "project.json")):
                raise ValueError("cannot preserve the area edit: the original sprite project is missing")
            from ..revise import confine   # outside the area the sprite stays exactly as it was
            old = ctx.projectio.load_project(ctx.asset.path(cfix["from_project"]))
            if (old.w, old.h) != (doc.w, doc.h):
                raise ValueError("cannot preserve the area edit: the sprite canvas changed size")
            ctx.info["restored_outside_area"] = confine(doc, old, cfix["box"])
        if cfix:
            ctx.info["revised"] = {"note": cfix.get("note"), "box": cfix.get("box"), "via": "concept"}
        ctx.save_project("project", doc)
        ctx.save_image("preview", "artist.png", imaging.from_bytes(doc.w, doc.h, doc.composite()))
        ctx.asset.state.inputs.pop("fix", None)
        ctx.asset.state.inputs.pop("concept_fix", None)
        ctx.info["reason"] = "[finish] mode = reduce: the reduction is the sprite (no drawing)"
        return
    trace = ctx.save_image("trace", "trace.png", imaging.from_bytes(doc.w, doc.h, doc.composite()))
    from ..models import StubLLM
    if isinstance(ctx.backend.llm, StubLLM):
        # a stub model cannot draw: pass the polished sprite through so the pipeline still runs end to end
        ctx.save_project("project", doc)
        ctx.save_image("preview", "artist.png", imaging.load_rgba(trace))
        ctx.info["dry_run"] = "artist agent skipped (needs a real model); polished sprite passed through"
        fix = ctx.asset.state.inputs.pop("fix", None)  # a revision request is used up here too (nothing to revise)
        if fix:
            ctx.info["revised"] = {"note": fix.get("note"), "box": fix.get("box"), "test_run": True}
        return
    fcfg = ctx.cfg.get("finish", {})
    spec = ctx.spec()
    concept_path = ctx.input("concept", "concept")
    face, cface = None, None
    if fcfg.get("face_pass", True):
        from ..artist import face_region
        with open(ctx.input("materials", "labelmap"), encoding="utf-8") as f:
            lm = json.load(f)
        face = face_region(lm["labels"], lm["width"], lm["height"])
        if face:
            with open(ctx.input("pixelize", "geom"), encoding="utf-8") as f:
                geom = json.load(f)
            crop = regions.concept_crop(imaging.load_rgba(concept_path), geom, face, pad=2)
            if crop is not None:
                cface = ctx.save_image("concept_face", "concept_face.png", crop)
    from ..artist import run_artist
    palette = ["#%02x%02x%02x" % v[:3] for v in doc.palette.values() if v[3] > 0]
    # a drawing that a spending limit stopped earlier is continued, not started over (finding F06)
    ckpt_dir = os.path.join(ctx.asset.dir, "checkpoints", "artist")
    made_from = {k: (r.info or {}).get("dir") for k, r in ctx.asset.state.steps.items() if k in ("concept", "polish")}
    phases = ("draw", "face") if face else ("draw",)
    start, revise, base = None, None, None
    fix = ctx.asset.state.inputs.get("fix")
    if fix:
        # revise the current drawing with the user's note (optionally only inside an area) instead of starting over
        base = ctx.asset.path(fix.get("from_project", ""))
        if os.path.exists(os.path.join(base, "project.json")):
            from ..revise import clamp_box
            box = clamp_box(fix.get("box"), doc.w, doc.h)
            close = None
            if box:
                with open(ctx.input("pixelize", "geom"), encoding="utf-8") as f:
                    crop = regions.concept_crop(imaging.load_rgba(concept_path), json.load(f), box, pad=2)
                if crop is not None:
                    close = ctx.save_image(None, "fix_concept.png", crop)
            revise = {"note": fix["note"], "box": box, "concept_close": close}
            start, phases = base, ("revise",)
            made_from["fix"] = fix.get("from_project")
            ctx.log(f"[artist] revising the current drawing{' inside the selected area' if box else ''}: {fix['note']}")
        else:
            ctx.log("[artist] the version to revise is gone; drawing anew with the note")
            base = None
    try:  # work that a spending limit or a cancel stopped earlier is continued, not started over
        with open(os.path.join(ckpt_dir, "checkpoint.json"), encoding="utf-8") as f:
            if json.load(f).get("made_from") == made_from:
                start = os.path.join(ckpt_dir, "project")
                ctx.log("[artist] continuing from where it stopped")
    except (OSError, ValueError):
        pass
    s = run_artist(ctx.project, ctx.asset.id, concept_path, spec.height_px, trace=trace, palette=palette,
                   client=getattr(ctx.backend.llm, "client", None),
                   out_dir=ctx.path("agent"),
                   max_usd=float(fcfg.get("revise_max_usd", 0.30) if revise else fcfg.get("max_usd", 1.5)),
                   max_turns=int(fcfg.get("max_turns", 40)), face_turns=int(fcfg.get("face_turns", 12)),
                   effort=fcfg.get("effort", "medium"), phases=phases, look=ctx.style.get("look"),
                   face=face, concept_face=cface, notes=None if revise else ctx.notes(), log=ctx.log, start_project=start,
                   live=LivePreview(ctx.asset.dir, "artist"), face_rules=fcfg.get("face_rules", "v3"),
                   revise=revise, revise_turns=int(fcfg.get("revise_turns", 12)))
    if "stopped:" in s["stop"] or "cancelled" in s["stop"]:
        # a spending limit or the user stopped the model: keep its drawing for later, and never pass a half-done
        # sprite on to critique and approval as if it were finished
        shutil.rmtree(ckpt_dir, ignore_errors=True)
        shutil.copytree(s["project"], os.path.join(ckpt_dir, "project"))
        with open(os.path.join(ckpt_dir, "checkpoint.json"), "w", encoding="utf-8") as f:
            json.dump({"made_from": made_from, "stop": s["stop"]}, f)
        if "cancelled" in s["stop"] and "stopped:" not in s["stop"]:
            from ..cancel import Cancelled
            raise Cancelled("cancelled; the drawing so far is kept")
        from ..models import BudgetExceeded
        why = s["stop"].split("stopped: ", 1)[1].split(";")[0]
        if why.startswith("turn limit"):
            raise RuntimeError(f"the drawing stopped before it was finished: {why}. It is kept; choose Try again "
                               "to continue it")
        raise BudgetExceeded(f"the drawing stopped before it was finished: {why}. It is kept; raise the limit in "
                             "Settings and choose Try again to continue it")
    shutil.rmtree(ckpt_dir, ignore_errors=True)
    drawn = ctx.projectio.load_project(s["project"])
    if not revise and fcfg.get("cleanup", True):
        # speckle cleanup on the finished drawing, as its own top layer (pixelagent/cleanup.py)
        from ..cleanup import cleanup_overlay
        with open(ctx.input("materials", "labelmap"), encoding="utf-8") as f:
            lmc = json.load(f)
        if (lmc["width"], lmc["height"]) == (drawn.w, drawn.h):
            overlay, n = cleanup_overlay(ctx.art, drawn.w, drawn.h, bytes(drawn.composite()), lmc["labels"], face=face,
                                         min_size=int(fcfg.get("cleanup_min_cluster", 3)))
            if n:
                drawn.layers.append(ctx.canvas.Layer("cleanup", drawn.w, drawn.h, data=overlay))
            ctx.info["cleanup_pixels"] = n
    if revise and revise["box"]:
        from ..revise import confine
        ctx.info["restored_outside_area"] = confine(drawn, ctx.projectio.load_project(base), revise["box"])
    ctx.save_project("project", drawn)
    ctx.save_image("preview", "artist.png", imaging.from_bytes(drawn.w, drawn.h, drawn.composite()))
    if revise:
        ctx.info["revised"] = {"note": revise["note"], "box": list(revise["box"]) if revise["box"] else None}
        ctx.asset.state.inputs.pop("fix", None)
    ctx.info.update(usd=s["usd"], turns=s["turns"], stop=s["stop"], face=s["face"],
                    summary=(s.get("finish_summary") or "")[:300], compare=s["compare"][1] if len(s["compare"]) > 1 else "")
