"""Face lab: compare face-pass variants on real characters, rated blind by a person.

Model judges proved unreliable for faces this small (position bias in pairwise tests), so the owner rates.

  python benchmarks/face_lab.py prepare --round r1       start projects: each sprite with its face reset to the trace
  python benchmarks/face_lab.py run --round r1           the face passes (real models; ~$0.07 each)
  python benchmarks/face_lab.py serve --round r1         rating page on http://127.0.0.1:8791 (blind, shuffled)
  python benchmarks/face_lab.py report --round r1        ratings revealed per variant

Variants (all from the same start):
  A  today's face pass: normal trace, face rules v1
  B  face trace (pixelagent.face.face_trace) as the reference inside the face, rules v1
  C  face trace + face rules v2 (feature sizes from the face trace, colour rules)"""
import argparse
import glob
import http.server
import json
import os
import random
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from pixelagent import imaging  # noqa: E402
from pixelagent.artist import face_region, palette_args, run_artist  # noqa: E402
from pixelagent.config import load_engine  # noqa: E402
from pixelagent.face import face_trace, reset_face  # noqa: E402
from pixelagent.mcp_client import MCPClient  # noqa: E402
from pixelagent.store import Project  # noqa: E402

VARIANTS = {"A": ("trace", "v1"), "B": ("face_trace", "v1"), "C": ("face_trace", "v2")}
DEFAULT_SOURCES = [os.path.join(REPO, "bench_runs_96", "assets", "*-20261108-155243"),
                   os.path.join(os.path.expanduser("~"), "Documents", "PixelAgent", "Starter cast", "assets", "lantern-keeper")]


def round_dir(name):
    return os.path.join(HERE, "face_lab_runs", name)


def characters(sources):
    for pat in sources:
        for d in sorted(glob.glob(pat)):
            p = os.path.join(d, "state.json")
            if not os.path.exists(p):
                continue
            st = json.load(open(p, encoding="utf-8"))
            if st["inputs"].get("kind") == "animation" or "artist" not in st["steps"]:
                continue
            yield os.path.basename(d).split("-2026")[0], d, {k: v["outputs"] for k, v in st["steps"].items()}


def prepare(args):
    out = round_dir(args.round)
    os.makedirs(out, exist_ok=True)
    engine = load_engine(Project(out).config)
    cases = []
    for name, d, o in characters(args.sources):
        lm = json.load(open(os.path.join(d, o["materials"]["labelmap"]), encoding="utf-8"))
        box = face_region(lm["labels"], lm["width"], lm["height"])
        if not box:
            print(f"{name}: no face found (helmet or no face materials), skipped")
            continue
        geom = json.load(open(os.path.join(d, o["pixelize"]["geom"]), encoding="utf-8"))
        trace = Image.open(os.path.join(d, o["artist"]["trace"])).convert("RGBA")
        sprite = Image.open(os.path.join(d, o["artist"]["preview"])).convert("RGBA")
        concept = Image.open(os.path.join(d, o["concept"]["concept"]))
        cdir = os.path.join(out, name)
        os.makedirs(cdir, exist_ok=True)
        ftrace, changed = face_trace(trace, concept, geom, lm["labels"], lm["width"], box)
        reset_face(sprite, trace, lm["labels"], lm["width"], box).save(os.path.join(cdir, "start.png"))
        trace.save(os.path.join(cdir, "trace.png"))
        ftrace.save(os.path.join(cdir, "face_trace.png"))
        shutil.copyfile(os.path.join(d, o["concept"]["concept"]), os.path.join(cdir, "concept.png"))
        if "concept_face" in o["artist"]:
            shutil.copyfile(os.path.join(d, o["artist"]["concept_face"]), os.path.join(cdir, "concept_face.png"))
        tpx = np.asarray(trace).reshape(-1, 4)
        palette = sorted({"#%02x%02x%02x" % tuple(int(v) for v in p[:3]) for p in tpx[tpx[:, 3] > 0]})
        for ref in ("trace", "face_trace"):
            mcp = MCPClient(os.path.join(engine, "server.py"), env={"PIXELLINER_MCP_OUT": cdir})
            try:
                W, H = trace.size
                for tool, a in [("open_image", {"path": os.path.join(cdir, "start.png"), "name": name}),
                                ("import_reference", {"path": os.path.join(cdir, f"{ref}.png"), "width": W, "height": H,
                                                      "into": "layer", "x": 0, "y": 0, "palette": "none", "opacity": 0.35,
                                                      "layer_name": "reference"}),
                                ("layer", {"action": "move", "layer": "reference", "index": 0}),
                                ("palette", palette_args(palette)),
                                ("save_project", {"path": os.path.join(cdir, f"start_{ref}")})]:
                    txt, _, err = mcp.call(tool, a)
                    if err:
                        raise RuntimeError(f"{name} {tool}: {txt}")
            finally:
                mcp.close()
        st = json.load(open(os.path.join(d, "state.json"), encoding="utf-8"))
        spec = json.load(open(os.path.join(d, o["intake"]["spec"]), encoding="utf-8"))
        cases.append({"name": name, "box": list(box), "height": spec.get("height_px", 96), "face_trace_cells": changed,
                      "source": d})
        print(f"{name}: face {box}, face trace changed {changed} cells")
    json.dump({"cases": cases}, open(os.path.join(out, "cases.json"), "w", encoding="utf-8"), indent=1)


def run(args):
    out = round_dir(args.round)
    cases = json.load(open(os.path.join(out, "cases.json"), encoding="utf-8"))["cases"]
    proj = Project(out, {"backend": {"mode": "openai"}})
    from pixelagent.openai_backend import make_client
    client = make_client(proj.config)
    jobs = [(c, v) for c in cases for v in args.variants]

    def one(job):
        c, v = job
        ref, rules = VARIANTS[v]
        cdir = os.path.join(out, c["name"])
        vdir = os.path.join(cdir, v)
        if os.path.exists(os.path.join(vdir, "summary.json")):
            return c["name"], v, "done before"
        cf = os.path.join(cdir, "concept_face.png")
        try:
            s = run_artist(proj, f"{c['name']}-{v}", os.path.join(cdir, "concept.png"), height=c["height"], client=client,
                           max_usd=args.max_usd, start_project=os.path.join(cdir, f"start_{ref}"), phases=("face",),
                           face=tuple(c["box"]), concept_face=cf if os.path.exists(cf) else None,
                           face_turns=args.face_turns, out_dir=vdir, face_rules=rules,
                           log=lambda m: None)
            return c["name"], v, f"${s['usd']:.3f}, {s['turns']} rounds, {s['stop']}"
        except Exception as e:  # one failure must not stop the others
            return c["name"], v, f"error: {type(e).__name__}: {e}"[:300]

    with ThreadPoolExecutor(args.jobs) as ex:
        for name, v, res in ex.map(one, jobs):
            print(f"{name:20s} {v}  {res}", flush=True)
    print(f"total ${proj.cost_total():.2f}")


# ------------------------------------------------------------------ rating page

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Face lab</title><style>
:root{--bg:#F5F5F7;--card:#fff;--text:#1D1D1F;--text2:#6E6E73;--accent:#5E5CE6;--check-a:#EFEFF2;--check-b:#E4E4E9}
@media (prefers-color-scheme:dark){:root{--bg:#1C1C1E;--card:#2C2C2E;--text:#F5F5F7;--text2:#98989D;--check-a:#48484C;--check-b:#3D3D41}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,"Segoe UI Variable Text","Segoe UI",system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 20px 80px}h1{font-size:26px;margin:0 0 6px}p.lead{color:var(--text2);margin:0 0 28px;max-width:70ch}
section{background:var(--card);border-radius:14px;padding:18px;margin-bottom:22px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
section h2{font-size:15px;margin:0 0 14px}.row{display:grid;grid-template-columns:200px repeat(var(--n),1fr);gap:14px;align-items:start}
.stage{border-radius:10px;display:grid;place-items:center;overflow:hidden;background-color:var(--check-a);
background-image:conic-gradient(var(--check-b) 25%,transparent 0 50%,var(--check-b) 0 75%,transparent 0);background-size:16px 16px}
.stage img{image-rendering:pixelated;display:block}.concept img{image-rendering:auto;width:100%;border-radius:10px}
.cand{display:flex;flex-direction:column;gap:8px}.cand .face{height:200px}.cand .whole{height:230px}
.label{font-weight:600;font-size:13px}.rank{display:flex;gap:4px;background:rgba(120,120,128,.14);border-radius:8px;padding:2px}
.rank button{flex:1;border:0;background:none;border-radius:6px;padding:5px 0;font:inherit;font-size:12px;color:var(--text);cursor:default}
.rank button[aria-pressed=true]{background:var(--card);box-shadow:0 1px 2px rgba(0,0,0,.15);font-weight:600}
textarea{width:100%;margin-top:12px;border-radius:8px;border:1px solid rgba(120,120,128,.3);background:transparent;color:var(--text);font:inherit;padding:8px;min-height:44px}
.bar{position:sticky;bottom:16px;display:flex;justify-content:flex-end;gap:12px;align-items:center}
.save{border:0;border-radius:980px;background:var(--accent);color:#fff;font:inherit;font-weight:600;padding:9px 20px}
#msg{color:var(--text2)}
</style></head><body><main>
<h1>Which face is best?</h1>
<p class="lead">Each character has three versions of the same sprite; only the face differs. They are shuffled and unlabelled.
Rank them 1st, 2nd, 3rd against the concept on the left: does the face read clearly at game size and keep the expression?
A note helps when something specific is wrong.</p>
<div id="cases"></div>
<div class="bar"><span id="msg"></span><button class="save" id="save">Save ratings</button></div>
</main><script>
const data = __DATA__;
const ranks = {}, notes = {};
function px(img, box, h) { img.onload = () => { const k = Math.max(1, Math.floor(h * .9 / img.naturalHeight));
  img.style.width = img.naturalWidth * k + "px"; }; }
document.getElementById("cases").innerHTML = data.cases.map((c) => `<section><h2>${c.title}</h2>
  <div class="row" style="--n:${c.cands.length}"><div class="concept"><img src="${c.concept}" alt="concept face"></div>
  ${c.cands.map((k) => `<div class="cand"><div class="label">Version ${k.label}</div>
     <div class="stage face"><img data-h="200" src="${k.face}"></div><div class="stage whole"><img data-h="230" src="${k.sprite}"></div>
     <div class="rank" data-case="${c.id}" data-key="${k.key}">${[1,2,3].slice(0, c.cands.length).map((r) =>
       `<button data-r="${r}" aria-pressed="false">${r === 1 ? "1st" : r === 2 ? "2nd" : "3rd"}</button>`).join("")}</div></div>`).join("")}
  </div><textarea data-note="${c.id}" placeholder="Optional note"></textarea></section>`).join("");
document.querySelectorAll(".stage img").forEach((i) => px(i, null, Number(i.dataset.h)));
document.querySelectorAll(".rank button").forEach((b) => b.onclick = () => {
  const g = b.parentElement, cid = g.dataset.case, key = g.dataset.key, r = Number(b.dataset.r);
  ranks[cid] = ranks[cid] || {};
  for (const [k, v] of Object.entries(ranks[cid])) if (v === r) delete ranks[cid][k];   // one version per place
  ranks[cid][key] = r;
  document.querySelectorAll(`.rank[data-case="${cid}"]`).forEach((gg) => gg.querySelectorAll("button").forEach((x) =>
    x.setAttribute("aria-pressed", ranks[cid][gg.dataset.key] === Number(x.dataset.r))));
});
document.querySelectorAll("textarea").forEach((t) => t.oninput = () => notes[t.dataset.note] = t.value);
document.getElementById("save").onclick = async () => {
  const r = await fetch("/ratings", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ranks, notes})});
  document.getElementById("msg").textContent = r.ok ? "Saved. You can close this page." : "Could not save";
};
</script></body></html>"""


def serve(args):
    out = round_dir(args.round)
    cases = json.load(open(os.path.join(out, "cases.json"), encoding="utf-8"))["cases"]
    keyfile = os.path.join(out, "blind_key.json")
    if os.path.exists(keyfile):
        key = json.load(open(keyfile, encoding="utf-8"))
    else:  # shuffled once and kept, so reloading the page shows the same letters
        key = {}
        for c in cases:
            vs = [v for v in VARIANTS if os.path.exists(os.path.join(out, c["name"], v, f"{c['name']}-{v}.png"))]
            random.shuffle(vs)
            key[c["name"]] = {"XYZW"[i]: v for i, v in enumerate(vs)}
        json.dump(key, open(keyfile, "w", encoding="utf-8"), indent=1)
    page_cases = []
    for c in cases:
        x, y, w, h = c["box"]
        cdir = os.path.join(out, c["name"])
        cands = []
        for label, v in key[c["name"]].items():
            sp = Image.open(os.path.join(cdir, v, f"{c['name']}-{v}.png")).convert("RGBA")
            sp.crop((x - 3, y - 3, x + w + 3, y + h + 3)).save(os.path.join(cdir, v, "face_crop.png"))
            cands.append({"label": label, "key": label, "face": f"/img/{c['name']}/{v}/face_crop.png",
                          "sprite": f"/img/{c['name']}/{v}/{c['name']}-{v}.png"})
        cf = "concept_face.png" if os.path.exists(os.path.join(cdir, "concept_face.png")) else "concept.png"
        page_cases.append({"id": c["name"], "title": c["name"].replace("_", " ").replace("-", " ").title(),
                           "concept": f"/img/{c['name']}/{cf}", "cands": cands})
    page = PAGE.replace("__DATA__", json.dumps({"cases": page_cases}))

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = unquote(urlparse(self.path).path)
            if u == "/":
                body, ctype = page.encode("utf-8"), "text/html; charset=utf-8"
            elif u.startswith("/img/"):
                p = os.path.normpath(os.path.join(out, u[5:]))
                if not p.startswith(os.path.normpath(out) + os.sep) or not os.path.isfile(p):
                    return self.send_error(404)
                body, ctype = open(p, "rb").read(), "image/png"
            else:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if urlparse(self.path).path != "/ratings":
                return self.send_error(404)
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            with open(os.path.join(out, "ratings.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, ensure_ascii=False)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), H)
    print(f"rating page: http://127.0.0.1:{args.port}/   (ratings go to {os.path.join(out, 'ratings.json')})", flush=True)
    if args.open:
        threading.Timer(0.5, lambda: __import__("webbrowser").open(f"http://127.0.0.1:{args.port}/")).start()
    httpd.serve_forever()


def report(args):
    out = round_dir(args.round)
    key = json.load(open(os.path.join(out, "blind_key.json"), encoding="utf-8"))
    r = json.load(open(os.path.join(out, "ratings.json"), encoding="utf-8"))
    totals = {}
    for case, ranks in r.get("ranks", {}).items():
        for label, place in ranks.items():
            v = key[case][label]
            totals.setdefault(v, []).append(place)
        print(f"{case:20s} " + "  ".join(f"{key[case][l]}={p}" for l, p in sorted(ranks.items(), key=lambda kv: kv[1]))
              + (f"   note: {r['notes'][case]}" if r.get("notes", {}).get(case) else ""))
    for v, ps in sorted(totals.items()):
        print(f"variant {v}: mean place {sum(ps) / len(ps):.2f}  firsts {ps.count(1)}/{len(ps)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "run", "serve", "report"])
    ap.add_argument("--round", default="r1")
    ap.add_argument("--sources", nargs="*", default=DEFAULT_SOURCES)
    ap.add_argument("--variants", default="ABC")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--max-usd", type=float, default=0.30)
    ap.add_argument("--face-turns", type=int, default=12)
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    {"prepare": prepare, "run": run, "serve": serve, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()
