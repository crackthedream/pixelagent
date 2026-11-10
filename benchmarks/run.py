"""Run cases from cases.csv through the pipeline (auto-approving gates) and summarise.

python benchmarks/run.py --project bench_runs [--dry-run | --backend openai] [--only id1,id2] [--jobs 3]
"""
import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pixelagent import imaging  # noqa: E402
from pixelagent.pipeline import Pipeline  # noqa: E402
from pixelagent.store import Project  # noqa: E402


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def rerun_case(proj, case, source_root, step, height=None):
    """Copy the newest asset of this case from another project and rerun it from `step` (same concept, map
    and grid): isolates the effect of pipeline changes after that step."""
    import glob
    import shutil
    srcs = sorted(glob.glob(os.path.join(source_root, "assets", case["id"] + "-*")))
    if not srcs:
        return {"id": case["id"], "status": "skipped: no source asset"}, None
    aid = os.path.basename(srcs[-1])
    dst = os.path.join(proj.root, "assets", aid)
    if not os.path.exists(dst):
        shutil.copytree(srcs[-1], dst)
    if height:  # change the sprite size in the (already approved) spec; everything from `step` follows it
        a = proj.asset(aid)
        spec_path = a.output("intake", "spec")
        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)
        spec["height_px"] = int(height)
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
    t = time.perf_counter()
    pipe = Pipeline(proj, aid)
    pipe.reset_from(step)
    try:
        status = pipe.run(auto_approve=True, log=lambda m: print(f"[{case['id']}] {m}", flush=True))
    except Exception as e:
        return {"id": case["id"], "asset": aid, "status": f"failed: {type(e).__name__}: {e}"[:300]}, None
    return _row(proj, case, aid, status, time.perf_counter() - t)


def _row(proj, case, aid, status, seconds):
    a = proj.asset(aid)
    lint = _read_json(a.output("critique", "lint"))
    crit = _read_json(a.output("critique", "critique"))
    st = a.state.steps
    row = {"id": case["id"], "asset": aid, "status": status, "seconds": round(seconds, 1),
           "usd": round(proj.cost_total(aid), 3), "lint_score": lint["score"], "critic_overall": crit["overall"],
           "verdict": crit["verdict"], "colors": lint["metrics"]["colors"],
           "shade_changes": lint["metrics"].get("shade_changes"), "singletons": lint["metrics"].get("singletons"),
           "concept_distance": lint.get("concept_distance"),
           "matmap_usable": st["matmap"].info.get("usable"), "matmap_iou": st["matmap"].info.get("iou"),
           "detail_regions": len(st["detail"].info.get("changed_regions", [])),
           "concept_sources": "+".join(st["concept"].info.get("sources", [])),
           "reference_cover": (st["concept"].info.get("reference_check") or {}).get("figure_cover"),
           "artist_usd": st["artist"].info.get("usd") if "artist" in st else None,
           "artist_turns": st["artist"].info.get("turns") if "artist" in st else None,
           "repair_rounds": st["critique"].info.get("rounds")}
    return row, imaging.load_rgba(a.output("export", "png"))


def run_case(proj, case, stamp):
    aid = f"{case['id']}-{stamp}"
    inputs = {"height": int(case["height"] or 64)}
    if case["input"] == "reference":
        src = os.path.join(HERE, case["source"])
        if not os.path.exists(src):
            return {"id": case["id"], "status": f"skipped: missing {src}"}, None
        inputs["references"] = [src]
    else:
        inputs["prompt"] = case["source"]
    proj.new_asset(aid, inputs)
    t = time.perf_counter()
    try:
        status = Pipeline(proj, aid).run(auto_approve=True, log=lambda m: print(f"[{case['id']}] {m}", flush=True))
    except Exception as e:  # one failing case must not stop the others
        return {"id": case["id"], "asset": aid, "status": f"failed: {type(e).__name__}: {e}"[:300],
                "seconds": round(time.perf_counter() - t, 1), "usd": round(proj.cost_total(aid), 3)}, None
    return _row(proj, case, aid, status, time.perf_counter() - t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backend", choices=["dry_run", "openai"])
    ap.add_argument("--only")
    ap.add_argument("--cases", default="cases.csv", help="case list in benchmarks/ (e.g. cases_refs.csv)")
    ap.add_argument("--jobs", type=int, default=1, help="cases run in parallel (API calls are I/O bound)")
    ap.add_argument("--rerun-from", help="step to rerun from, using copies of assets from --source")
    ap.add_argument("--source", help="project folder holding the assets to copy for --rerun-from")
    ap.add_argument("--height", type=int, help="with --rerun-from: change the sprite height (rerun from pixelize or earlier)")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    mode = "dry_run" if args.dry_run else args.backend
    proj = Project(args.project, {"backend": {"mode": mode}} if mode else None)
    print("backend:", proj.config["backend"]["mode"], flush=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    only = set(args.only.split(",")) if args.only else None
    with open(os.path.join(HERE, args.cases), encoding="utf-8") as f:
        cases = [c for c in csv.DictReader(f) if not only or c["id"] in only]
    if args.rerun_from:
        job = lambda c: rerun_case(proj, c, args.source, args.rerun_from, args.height)  # noqa: E731
    else:
        job = lambda c: run_case(proj, c, stamp)  # noqa: E731
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        results = list(ex.map(job, cases))
    rows = [r for r, _ in results]
    for r in rows:
        print(r, flush=True)
    done = [(r["id"], im) for r, im in results if im is not None]
    keys = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
    out = os.path.join(proj.root, f"summary-{stamp}.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print("summary:", out)
    if done:
        sheet = os.path.join(proj.root, f"finals-{stamp}.png")
        imaging.contact_sheet([im for _, im in done], [i for i, _ in done], zoom_factor=4).save(sheet)
        print("contact sheet:", sheet)
    print("total usd: %.3f" % sum(r.get("usd", 0) for r in rows))


if __name__ == "__main__":
    main()
