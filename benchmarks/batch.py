"""Run a set of references end to end with the project's current defaults (real models), several at a time, and
write one summary row per character.

  python benchmarks/batch.py --out bench_runs_batch --refs benchmarks/refs/owner/ref*.png [--jobs 3]

Summary (summary.json in --out): status, seconds, cost by role, measured heads tall, proportion retries, critic
overall score and verdict."""
import argparse
import collections
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pixelagent.pipeline import Pipeline  # noqa: E402
from pixelagent.store import Project  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--refs", nargs="+", required=True)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--mode", default="openai")
    a = ap.parse_args()
    refs = sorted({p for pat in a.refs for p in glob.glob(pat)})
    proj = Project(a.out, {"backend": {"mode": a.mode}})

    def one(ref):
        aid = os.path.splitext(os.path.basename(ref))[0]
        if aid not in proj.asset_ids():
            proj.new_asset(aid, {"references": [os.path.abspath(ref)]})
        t0 = time.time()
        try:
            status = Pipeline(proj, aid).run(auto_approve=True, log=lambda m: None)
        except Exception as e:  # one failure must not stop the others
            status = f"error: {type(e).__name__}: {e}"[:300]
        secs = round(time.time() - t0)
        print(f"{aid}: {status} in {secs}s", flush=True)
        return aid, status, secs

    with ThreadPoolExecutor(a.jobs) as ex:
        results = list(ex.map(one, refs))

    cost = collections.defaultdict(lambda: collections.defaultdict(float))
    path = os.path.join(proj.root, "costs.jsonl")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            cost[r["asset"]][r["role"]] += r["usd"]
    rows = []
    for aid, status, secs in results:
        st = proj.asset(aid).state
        mat = st.steps.get("materials")
        crit = st.steps.get("critique")
        overall = verdict = None
        if crit and crit.outputs.get("critique"):
            try:
                c = json.load(open(proj.asset(aid).path(crit.outputs["critique"]), encoding="utf-8"))
                overall, verdict = c.get("overall"), c.get("verdict")
            except OSError:
                pass
        rows.append({"id": aid, "status": status, "seconds": secs,
                     "usd": round(sum(cost[aid].values()), 3), "usd_by_role": {k: round(v, 3) for k, v in cost[aid].items()},
                     "heads_tall": (mat.info or {}).get("heads_tall_measured") if mat else None,
                     "proportion_retries": st.inputs.get("proportion_retries", 0),
                     "proportions_note": (mat.info or {}).get("proportions") if mat else None,
                     "concept_version": st.steps["concept"].version if "concept" in st.steps else None,
                     "critic_overall": overall, "critic_verdict": verdict})
    json.dump(rows, open(os.path.join(proj.root, "summary.json"), "w", encoding="utf-8"), indent=1)
    for r in rows:
        print(f"{r['id']:8s} {r['status']:10s} {r['seconds']:4d}s ${r['usd']:.3f} heads {r['heads_tall']} "
              f"retries {r['proportion_retries']} critic {r['critic_overall']} {r['critic_verdict']}")
    print(f"total ${sum(r['usd'] for r in rows):.2f}")


if __name__ == "__main__":
    main()
