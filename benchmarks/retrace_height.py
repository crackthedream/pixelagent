"""Redo a finished character at another sprite height, reusing its concept and material map (both are made at concept
resolution and do not depend on the height). Everything from pixelize on runs again with the real models.

  python benchmarks/retrace_height.py --source bench_runs_refs --case ref01 --height 160 --out bench_runs_160
      [--artist-max-usd 2.5] [--face-rules v3] [--until polish]

--until polish stops before the drawing (pixelize .. polish only cost a few cents)."""
import argparse
import glob
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pixelagent.pipeline import Pipeline  # noqa: E402
from pixelagent.store import Project  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="project folder with the finished run")
    ap.add_argument("--case", required=True, help="asset id prefix, e.g. ref01")
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--out", required=True, help="project folder for the new run")
    ap.add_argument("--artist-max-usd", type=float, default=None)
    ap.add_argument("--face-rules", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--mode", default="openai")
    a = ap.parse_args()

    src = sorted(glob.glob(os.path.join(a.source, "assets", a.case + "-*")))
    if not src:
        sys.exit(f"no asset '{a.case}-*' in {a.source}")
    over = {"backend": {"mode": a.mode}, "finish": {}}
    if a.artist_max_usd is not None:
        over["finish"]["max_usd"] = a.artist_max_usd
    if a.face_rules:
        over["finish"]["face_rules"] = a.face_rules
    proj = Project(a.out, over)
    aid = f"{a.case}-h{a.height}-{time.strftime('%Y%m%d-%H%M%S')}"
    dst = os.path.join(proj.root, "assets", aid)
    shutil.copytree(src[-1], dst)
    stp = os.path.join(dst, "state.json")
    st = json.load(open(stp, encoding="utf-8"))
    st["id"], st["inputs"]["height"], st["status"] = aid, a.height, "pending"
    json.dump(st, open(stp, "w", encoding="utf-8"), indent=1)
    sp = os.path.join(dst, st["steps"]["intake"]["outputs"]["spec"])
    spec = json.load(open(sp, encoding="utf-8"))
    spec["height_px"] = a.height
    json.dump(spec, open(sp, "w", encoding="utf-8"), indent=2)

    pipe = Pipeline(proj, aid)
    pipe.reset_from("pixelize")
    t0 = time.time()
    status = pipe.run(auto_approve=True, until=a.until, log=lambda m: print(f"[{aid}] {m}", flush=True))
    print(f"[{aid}] {status}; {time.time() - t0:.0f} s; cost of this run ${proj.cost_total():.2f}")


if __name__ == "__main__":
    main()
