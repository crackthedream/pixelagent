"""Make a character's move set (the app's Move set: idle, walk, run, jump, attack, hurt) in a project folder, all moves
in parallel, approving the poses automatically, and print what each cost and how tall its frames came out.

  python benchmarks/move_set.py --project bench_runs_moves --character ref05 [--moves idle,walk] [--mode dry_run]

About $0.08 per move with the native finish (plan + one pose sheet; the frames are read back, not drawn)."""
import argparse
import glob
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image  # noqa: E402

from pixelagent.app.server import MOVES  # noqa: E402
from pixelagent.pipeline import Pipeline  # noqa: E402
from pixelagent.store import Project  # noqa: E402


def spent(root, aid):
    path = os.path.join(root, "costs.jsonl")
    if not os.path.exists(path):
        return 0.0
    with open(path, encoding="utf-8") as f:
        return sum(json.loads(l).get("usd", 0.0) for l in f if l.strip() and json.loads(l).get("asset") == aid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--character", required=True)
    ap.add_argument("--moves", default=",".join(MOVES))
    ap.add_argument("--mode", default="openai", choices=["openai", "dry_run"])
    a = ap.parse_args()
    proj = Project(a.project, {"backend": {"mode": a.mode}})
    proj.asset(a.character)
    ids, results = [], {}
    for m in a.moves.split(","):
        prompt, frames = MOVES[m]
        aid = f"{a.character}-{m}"
        if aid not in proj.asset_ids():
            proj.new_asset(aid, {"kind": "animation", "character": a.character, "anim": m, "prompt": prompt,
                                 "frames": frames})
        ids.append(aid)

    def run(aid):
        t = time.time()
        try:
            results[aid] = (Pipeline(proj, aid).run(auto_approve=True, log=lambda *_: None), time.time() - t)
        except Exception as e:  # noqa: BLE001  (report and keep the other moves going)
            results[aid] = (f"error: {e}", time.time() - t)
    threads = [threading.Thread(target=run, args=(aid,)) for aid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    char = Image.open([p for p in sorted(glob.glob(os.path.join(a.project, "assets", a.character, "steps", "*export",
                                                               "v*", "*.png"))) if not p.endswith("_x4.png")][-1])
    print(f"character {a.character}: {char.getbbox()[3] - char.getbbox()[1]} px tall")
    for aid in ids:
        status, secs = results[aid]
        frames = sorted(glob.glob(os.path.join(a.project, "assets", aid, "steps", "*anim_draw", "v*", "frame_*.png")))
        hs = []
        for p in frames[-12:]:
            bb = Image.open(p).getbbox()
            hs.append(bb[3] - bb[1] if bb else 0)
        fit = proj.asset(aid).state.steps.get("anim_traces")
        fit = fit.info.get("frame_size") if fit else None
        print(f"{aid}: {status}, {secs:.0f} s, ${spent(a.project, aid):.3f}, frame heights {hs}, size fix {fit}")


if __name__ == "__main__":
    main()
