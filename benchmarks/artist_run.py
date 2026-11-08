"""Run the artist agent (the model draws via the pixelliner MCP tools) on benchmark concepts, in parallel, and
compare with the conversion pipeline.

python benchmarks/artist_run.py --project artist_runs --concepts bench_runs --pipeline bench_runs_v3 \
       --only forest_archer,armored_knight --jobs 5 [--max-usd 3] [--max-turns 60] [--tag run2]
"""
import argparse
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pixelagent import imaging  # noqa: E402
from pixelagent.artist import run_artist  # noqa: E402
from pixelagent.config import load_engine  # noqa: E402
from pixelagent.store import Project  # noqa: E402


def concept_of(root, case):
    a = sorted(glob.glob(os.path.join(root, "assets", case + "-*")))[-1]
    with open(os.path.join(a, "state.json"), encoding="utf-8") as f:
        st = json.load(f)["steps"]
    return os.path.join(a, st["concept"]["outputs"]["concept"]), st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--concepts", required=True, help="project holding the benchmark assets (their concepts are the references)")
    ap.add_argument("--pipeline", help="project with pipeline results to compare against")
    ap.add_argument("--only", required=True)
    ap.add_argument("--jobs", type=int, default=5)
    ap.add_argument("--height", type=int, default=96)
    ap.add_argument("--max-usd", type=float, default=3.0)
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--tag", default=time.strftime("%H%M%S"))
    ap.add_argument("--hybrid", action="store_true",
                    help="trace = the pipeline's cleaned sprite (from --pipeline), palette = the pipeline's palette")
    ap.add_argument("--agent-tag", help="also show pure-agent results of this earlier tag for comparison")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    proj = Project(args.project, {"backend": {"mode": "openai"}})
    load_engine(proj.config)
    import art
    cases = args.only.split(",")

    def pipeline_of(case):
        pa = sorted(glob.glob(os.path.join(args.pipeline, "assets", case + "-*")))[-1]
        with open(os.path.join(pa, "state.json"), encoding="utf-8") as f:
            pst = json.load(f)["steps"]
        with open(os.path.join(pa, pst["export"]["outputs"]["meta"]), encoding="utf-8") as f:
            meta = json.load(f)
        return (os.path.join(pa, pst["export"]["outputs"]["png"]), meta["palette"],
                os.path.join(pa, pst["pixelize"]["outputs"]["pixel_base"]))

    def job(case):
        ref, _ = concept_of(args.concepts, case)
        extra = {}
        if args.hybrid:
            trace, pal, _ = pipeline_of(case)
            extra = {"trace": trace, "palette": pal}
        try:
            return case, run_artist(proj, f"{case}-{args.tag}", ref, args.height, max_usd=args.max_usd,
                                    max_turns=args.max_turns, log=lambda m: print(m, flush=True), **extra)
        except Exception as e:  # one failing case must not stop the others
            return case, {"error": f"{type(e).__name__}: {e}"[:300]}

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        results = list(ex.map(job, cases))

    tiles, labels, rows = [], [], []
    for case, s in results:
        if "error" in s:
            print(case, "FAILED", s["error"])
            continue
        agent = imaging.load_rgba(s["png"])
        ref_path, _ = concept_of(args.concepts, case)
        concept = imaging.load_rgba(ref_path)
        tiles.append(imaging.zoom(agent, 3))
        labels.append(f"{case[:16]}: " + ("hybrid" if args.hybrid else "agent"))
        if args.agent_tag:
            prev = os.path.join(proj.root, "artist", f"{case}-{args.agent_tag}", f"{case}-{args.agent_tag}.png")
            if os.path.exists(prev):
                tiles.append(imaging.zoom(imaging.load_rgba(prev), 3))
                labels.append("pure agent")
        row = {"case": case, "turns": s["turns"], "usd_max": s["usd"], "seconds": s["seconds"], "stop": s["stop"],
               "concept_distance": float(s["compare"][1].split("colour distance ")[1].split(" ")[0]),
               "colors": s["lint"]["metrics"]["colors"], "specks": s["lint"]["metrics"]["singletons"]}
        if args.pipeline:
            pa = sorted(glob.glob(os.path.join(args.pipeline, "assets", case + "-*")))[-1]
            with open(os.path.join(pa, "state.json"), encoding="utf-8") as f:
                pst = json.load(f)["steps"]
            pipe = imaging.load_rgba(os.path.join(pa, pst["export"]["outputs"]["png"]))
            d = imaging.to_bytes(pipe)
            row["pipeline_colors"] = art.lint(pipe.width, pipe.height, d)["metrics"]["colors"]
            _, _, base_path = pipeline_of(case)
            base = imaging.to_bytes(imaging.load_rgba(base_path))
            if agent.size == pipe.size:   # same pixel grid: distance to the concept on that grid, both sprites
                row["concept_dist_this"] = art.fidelity(agent.width, agent.height, imaging.to_bytes(agent), base)["delta_e"]
                row["concept_dist_pipeline"] = art.fidelity(pipe.width, pipe.height, d, base)["delta_e"]
            row["pipeline_specks"] = art.shade_noise(pipe.width, pipe.height, [tuple(d[i:i + 4]) for i in range(0, len(d), 4)])["singletons"]
            tiles.append(imaging.zoom(pipe, 3))
            labels.append("pipeline v3")
        tiles.append(concept.resize((max(1, concept.width * agent.height * 3 // concept.height), agent.height * 3)))
        labels.append("reference")
        rows.append(row)
        print(row, flush=True)
    if tiles:
        cols = 2 + bool(args.pipeline) + bool(args.agent_tag)
        out = os.path.join(proj.root, f"artist_compare-{args.tag}.png")
        imaging.contact_sheet(tiles, labels, 1, cols=cols if cols > 3 else cols * 2).save(out)
        print("sheet:", out)
    with open(os.path.join(proj.root, f"artist_summary-{args.tag}.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print("total usd (upper bound): %.3f" % sum(r["usd_max"] for r in rows))


if __name__ == "__main__":
    main()
