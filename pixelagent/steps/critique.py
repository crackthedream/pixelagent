"""Step 10: critique + repair loop. Step 11: review gate C.

Round 0 critiques the polished sprite (lint + vision critic). While the verdict is 'repair' and rounds remain,
the most severe local issues (those with a region) get a constrained rewrite (variants anchored to the
concept, see regions.py), then the sprite is re-critiqued.

Acceptance is value-driven: quality = critic overall + lint/50 - fidelity_weight * distance to the concept.
A round is kept only if quality rises by more than min_gain (the critic's run-to-run noise), so changes that
are not measurably better are not kept; otherwise it is reverted and the loop stops. Regions whose rewrite
was already rejected are not retried. Repairs live in a 'repair' overlay layer."""
import json

from .. import imaging, prompts, regions
from ..schemas import Critique, Issue, Scores

SEV = {"high": 0, "medium": 1, "low": 2}


def _evaluate(ctx, doc, tag, ref=None):
    art = ctx.art
    spec = ctx.spec()
    data = doc.composite()
    pal = [v[:3] for v in doc.palette.values() if v[3] > 0]
    lint = art.lint(doc.w, doc.h, data, pal, spec.max_colors)
    final = imaging.from_bytes(doc.w, doc.h, data)
    p1 = ctx.save_image(None, f"{tag}_1x.png", final)
    pg = ctx.save_image(None, f"{tag}_grid.png", regions.grid_image(final, 8, 8))

    def stub():
        sev = {"error": "high", "warning": "medium", "info": "low"}
        issues = [Issue(area="global", severity=sev[i["severity"]], problem=i["message"]) for i in lint["issues"]]
        s = lint["score"] / 10
        return Critique(scores=Scores(cleanliness=round(s, 1)), overall=round(s, 1), issues=issues,
                        verdict="pass" if not any(i.severity == "high" for i in issues) else "repair")

    fmode = ctx.cfg.get("finish", {}).get("mode")
    note = (prompts.CRITIC_SOFT.format(colors=spec.max_colors) if fmode == "reduce"
            else prompts.CRITIC_NATIVE if fmode == "native" else "")
    crit = ctx.backend.llm.structured("critic", prompts.CRITIC.format(light=spec.light, lint=json.dumps(lint),
                                                                      style_note=note),
                                      spec.model_dump_json(), [pg, p1, ctx.input("concept", "concept")], Critique, stub)
    fid = art.fidelity(doc.w, doc.h, data, ref)["delta_e"] if ref is not None else 0.0
    weight = float(ctx.cfg.get("detail", {}).get("fidelity_weight", 15.0))
    quality = crit.overall + lint["score"] / 50.0 - weight * fid
    lint["concept_distance"] = fid
    return crit, lint, quality, p1, pg


def run(ctx):
    art_rec = ctx.asset.state.steps.get("artist")
    by_artist = bool(art_rec and art_rec.status == "done" and "project" in art_rec.outputs)
    doc = ctx.load_project("artist" if by_artist else "polish")
    w, h = doc.w, doc.h
    # the artist agent's drawing is critiqued for information only: automatic region rewrites on top of it
    # would undo its deliberate pixel work
    rounds = 0 if by_artist else int(ctx.cfg["budget"].get("critique_rounds", 2))
    per_round = int(ctx.cfg.get("detail", {}).get("repair_issues_per_round", 3))
    max_side = int(ctx.cfg.get("detail", {}).get("max_region_side", 24))
    dcfg = ctx.cfg.get("detail", {})
    n = int(dcfg.get("repair_candidates", 2))
    min_gain = float(dcfg.get("min_gain", 0.25))
    tol = float(dcfg.get("fidelity_tolerance", 0.02))
    ref = imaging.to_bytes(imaging.load_rgba(ctx.input("pixelize", "pixel_base")))
    concept = imaging.load_rgba(ctx.input("concept", "concept"))
    with open(ctx.input("pixelize", "geom"), encoding="utf-8") as f:
        geom = json.load(f)

    crit, lint, q, p1, pg = _evaluate(ctx, doc, "round0", ref)
    history = [{"round": 0, "overall": crit.overall, "lint": lint["score"], "concept_distance": lint["concept_distance"],
                "quality": round(q, 2), "verdict": crit.verdict}]
    best = (q, crit, lint, p1, pg, bytes(doc.composite()))
    failed = []  # regions whose rewrite was rejected: not retried
    for r in range(1, rounds + 1):
        if crit.verdict == "pass":
            break
        todo = sorted((i for i in crit.issues if i.region and len(i.region) == 4 and i.severity != "low"
                       and not any(_iou(regions.clamp_region(i.region, w, h, max_side), f) > 0.5 for f in failed)),
                      key=lambda i: SEV[i.severity])[:per_round]
        if not todo:
            history[-1]["stop"] = "no local issues with a region"
            break
        data = doc.composite()
        fixes = []
        for k, issue in enumerate(todo):
            reg = regions.clamp_region(issue.region, w, h, max_side)
            name = "".join(ch if ch.isalnum() else "_" for ch in issue.area)[:24] or f"issue{k}"
            goal = issue.problem + (" Fix: " + issue.fix if issue.fix else "")
            data, info = regions.rewrite_region(ctx, data, w, h, doc.palette, reg, name, goal, concept, geom, n,
                                                tag=f"r{r}", ref=ref, fidelity_tol=tol)
            if not info.get("picked"):
                failed.append(reg)
            fixes.append({"area": issue.area, "region": list(reg), "picked": info.get("picked"),
                          "candidates": info.get("candidates"), "errors": info.get("errors"),
                          "rejected_by_fidelity": info.get("rejected_by_fidelity")})
        if bytes(data) == best[5]:
            history.append({"round": r, "fixes": fixes, "stop": "no candidate accepted"})
            break
        regions.write_overlay(ctx, doc, data, "repair")
        crit, lint, q, p1, pg = _evaluate(ctx, doc, f"round{r}", ref)
        entry = {"round": r, "fixes": fixes, "overall": crit.overall, "lint": lint["score"],
                 "concept_distance": lint["concept_distance"], "quality": round(q, 2), "verdict": crit.verdict}
        if q >= best[0] + min_gain:
            best = (q, crit, lint, p1, pg, bytes(doc.composite()))
            entry["kept"] = True
            history.append(entry)
        else:  # not measurably better: revert to the best version and stop
            regions.write_overlay(ctx, doc, bytearray(best[5]), "repair")
            entry["kept"] = False
            history.append(entry)
            break
    q, crit, lint, p1, pg, _ = best
    ctx.outputs["final_1x"] = p1
    ctx.outputs["final_grid"] = pg
    final = imaging.load_rgba(p1)
    ctx.save_image("final_zoom", "final_zoom.png", imaging.zoom(final, int(ctx.cfg["review"]["zoom"])))
    ctx.save_project("project", doc)
    ctx.save_json("lint", "lint.json", lint)
    ctx.save_json("critique", "critique.json", crit)
    ctx.save_json("history", "repair_history.json", history)
    ctx.info.update(score=lint["score"], overall=crit.overall, verdict=crit.verdict,
                    rounds=len(history) - 1, history=[{k: v for k, v in e.items() if k != "fixes"} for e in history])


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def review(ctx):
    ctx.info["final"] = ctx.input("critique", "final_zoom")
    ctx.info["suggested"] = None
