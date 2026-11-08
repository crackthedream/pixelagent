"""Step 10: the artist agent draws the final sprite (hybrid mode).

The polished conversion is the tracing layer (proportions, colour placement, light), the concept is the authority
for design intent, and the model redraws the sprite itself through the pixelliner MCP tools, then does a focused
face pass with a close-up of the concept's face. The result replaces the conversion as the sprite that is
critiqued and exported. [finish] mode = "repair" keeps the older detail/repair path instead."""
import json
import os

from .. import imaging, regions


def mode(ctx):
    return ctx.cfg.get("finish", {}).get("mode", "artist")


def run(ctx):
    if mode(ctx) != "artist":
        ctx.skipped = True
        ctx.info["reason"] = "[finish] mode is not 'artist'"
        return
    doc = ctx.load_project("polish")
    trace = ctx.save_image("trace", "trace.png", imaging.from_bytes(doc.w, doc.h, doc.composite()))
    from ..models import StubLLM
    if isinstance(ctx.backend.llm, StubLLM):
        # a stub model cannot draw: pass the polished sprite through so the pipeline still runs end to end
        ctx.save_project("project", doc)
        ctx.save_image("preview", "artist.png", imaging.load_rgba(trace))
        ctx.info["dry_run"] = "artist agent skipped (needs a real model); polished sprite passed through"
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
    s = run_artist(ctx.project, ctx.asset.id, concept_path, spec.height_px, trace=trace, palette=palette,
                   client=getattr(ctx.backend.llm, "client", None),
                   out_dir=ctx.path("agent"), max_usd=float(fcfg.get("max_usd", 1.5)),
                   max_turns=int(fcfg.get("max_turns", 40)), face_turns=int(fcfg.get("face_turns", 12)),
                   effort=fcfg.get("effort", "medium"), phases=("draw", "face") if face else ("draw",),
                   face=face, concept_face=cface)
    drawn = ctx.projectio.load_project(s["project"])
    ctx.save_project("project", drawn)
    ctx.save_image("preview", "artist.png", imaging.load_rgba(s["png"]))
    ctx.info.update(usd=s["usd"], turns=s["turns"], stop=s["stop"], face=s["face"],
                    summary=(s.get("finish_summary") or "")[:300], compare=s["compare"][1] if len(s["compare"]) > 1 else "")
