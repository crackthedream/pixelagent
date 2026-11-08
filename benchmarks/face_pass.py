"""Experiment: run only the artist's FACE PASS on existing hybrid sprites and judge the faces before vs after
(pairwise, order-swapped). Isolates the effect of the face pass: the body is unchanged.

python benchmarks/face_pass.py --project artist_runs --pipeline bench_runs_v3 --from-tag hybrid --tag face \
       --only forest_archer,mech_engineer,apprentice_mage,mushroom_traveler
"""
import argparse
import glob
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pixelagent import imaging, regions  # noqa: E402
from pixelagent.artist import face_region, run_artist  # noqa: E402
from pixelagent.judge import compare_pair  # noqa: E402
from pixelagent.models import make_backend  # noqa: E402
from pixelagent.store import Project  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--pipeline", required=True)
    ap.add_argument("--from-tag", required=True)
    ap.add_argument("--tag", default="face")
    ap.add_argument("--only", required=True)
    ap.add_argument("--max-usd", type=float, default=1.5)
    ap.add_argument("--face-turns", type=int, default=12)
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    proj = Project(args.project, {"backend": {"mode": "openai"}})

    def job(case):
        a = sorted(glob.glob(os.path.join(args.pipeline, "assets", case + "-*")))[-1]
        with open(os.path.join(a, "state.json"), encoding="utf-8") as f:
            st = json.load(f)["steps"]
        with open(os.path.join(a, st["materials"]["outputs"]["labelmap"]), encoding="utf-8") as f:
            lm = json.load(f)
        with open(os.path.join(a, st["pixelize"]["outputs"]["geom"]), encoding="utf-8") as f:
            geom = json.load(f)
        face = face_region(lm["labels"], lm["width"], lm["height"])
        if not face:
            return case, {"skipped": "no face found (e.g. closed helmet)"}
        concept_path = os.path.join(a, st["concept"]["outputs"]["concept"])
        concept = imaging.load_rgba(concept_path)
        out = os.path.join(proj.root, "artist", f"{case}-{args.tag}")
        os.makedirs(out, exist_ok=True)
        cf = os.path.join(out, "concept_face.png")
        crop = regions.concept_crop(concept, geom, face, pad=2)
        crop.save(cf)
        src = os.path.join(proj.root, "artist", f"{case}-{args.from_tag}")
        try:
            s = run_artist(proj, f"{case}-{args.tag}", concept_path, start_project=os.path.join(src, "project"),
                           phases=("face",), face=face, concept_face=cf, max_usd=args.max_usd,
                           face_turns=args.face_turns, log=lambda m: print(m, flush=True))
        except Exception as e:
            return case, {"error": f"{type(e).__name__}: {e}"[:300]}
        llm = make_backend(proj, f"{case}-{args.tag}").llm
        verdict = compare_pair(llm, os.path.join(src, f"{case}-{args.from_tag}.png"), s["png"], face, crop, out)
        return case, {"face": face, "usd": s["usd"], "turns": s["turns"], "stop": s["stop"], "verdict": verdict,
                      "before": os.path.join(src, f"{case}-{args.from_tag}.png"), "after": s["png"]}

    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(job, args.only.split(",")))
    tiles, labels = [], []
    for case, r in results:
        print(case, json.dumps({k: v for k, v in r.items() if k not in ("before", "after")}, ensure_ascii=False), flush=True)
        if "after" in r:
            x, y, w, h = r["face"]
            box = (max(0, x - 3), max(0, y - 3), x + w + 3, y + h + 3)
            for p, lab in ((r["before"], "before"), (r["after"], "after face pass")):
                tiles.append(imaging.zoom(imaging.load_rgba(p).crop(box), 12))
                labels.append(f"{case[:14]} {lab}")
            cf = imaging.load_rgba(os.path.join(proj.root, "artist", f"{case}-{args.tag}", "concept_face.png"))
            tiles.append(cf.resize((cf.width * tiles[-1].height // cf.height, tiles[-1].height)))
            labels.append("concept")
    if tiles:
        out = os.path.join(proj.root, f"faces-{args.tag}.png")
        imaging.contact_sheet(tiles, labels, 1, cols=6).save(out)
        print("sheet:", out)
    with open(os.path.join(proj.root, f"face_summary-{args.tag}.json"), "w", encoding="utf-8") as f:
        json.dump(dict(results), f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
