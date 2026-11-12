"""A validation round for the owner's ratings: move sets and expressions for several finished characters, and a rating
sheet (rating.html) with a 1-5 score, problem tags and a note for each move and face.

  python benchmarks/validation_round.py run   --project bench_runs_validate2 --source bench_runs_native_batch \
         --moves ref01,ref03,ref08,ref06 --faces ref01,ref03,ref08,ref10 [--custom "smug=a smug half-smile"] [--mode dry_run]
  python benchmarks/validation_round.py sheet --project bench_runs_validate2

run:   characters missing from the project are copied from --source (finished characters; their old faces are left
       behind) and redone from the colour step, so they carry today's palette grade. Then every move of the move set
       for --moves and the six preset faces (plus --custom ones) for --faces. Finished pieces are kept: run it again
       after a stop (a spending limit, no credits) and it carries on. --redo-faces draws the faces again.
sheet: rating.html in the project folder. Open it in a browser, rate, press Copy ratings.

About $0.40 a character for the moves and $0.35-0.70 for the faces (a face that fails its check is drawn twice)."""
import argparse
import html
import json
import os
import shutil
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image  # noqa: E402

from pixelagent import expressions  # noqa: E402
from pixelagent.app.server import MOVES  # noqa: E402
from pixelagent.pipeline import Pipeline  # noqa: E402
from pixelagent.store import Project  # noqa: E402

MOVE_TAGS = ["face drifts", "size jumps", "pose wrong", "off-model", "jittery"]
FACE_TAGS = ["doesn't read", "off-model", "wrong colours", "too frontal"]


def quiet(*_a, **_k):
    pass


def spent(root):
    path = os.path.join(root, "costs.jsonl")
    if not os.path.exists(path):
        return 0.0
    with open(path, encoding="utf-8") as f:
        return sum(json.loads(line).get("usd", 0.0) for line in f if line.strip())


def out_of_credit(e):
    return "credit" in str(e).lower() or "insufficient_quota" in str(e)


def run(a):
    moves_for = [c for c in a.moves.split(",") if c]
    faces_for = [c for c in a.faces.split(",") if c]
    custom = dict(kv.split("=", 1) for kv in a.custom) if a.custom else {}
    os.makedirs(os.path.join(a.project, "assets"), exist_ok=True)
    fresh = []
    for cid in dict.fromkeys(moves_for + faces_for):
        d = os.path.join(a.project, "assets", cid)
        if not os.path.exists(d):
            src = os.path.join(a.source, "assets", cid)
            if not os.path.isdir(src):
                sys.exit(f"{cid}: not in {a.project} and not in {a.source}")
            shutil.copytree(src, d, ignore=shutil.ignore_patterns("expressions"))
            fresh.append(cid)
    proj = Project(a.project, {"backend": {"mode": a.mode}})
    start = spent(a.project)
    for cid in dict.fromkeys(moves_for + faces_for):
        p = Pipeline(proj, cid)
        if cid in fresh:
            p.reset_from("rebuild")                       # today's colours
        state = proj.asset(cid).state
        if cid in fresh or state.status != "complete" or state.steps["export"].status != "done":
            try:
                print(f"{cid}: character {p.run(auto_approve=True, log=quiet)}", flush=True)
            except Exception as e:  # noqa: BLE001
                sys.exit(f"{cid}: the character could not be finished: {e}" +
                         ("\nThe API account has no credits left: add credits and run this again." if out_of_credit(e) else ""))
    stop = threading.Event()
    lock = threading.Lock()

    def note(msg):
        with lock:
            print(msg, flush=True)

    def own_project():
        """Each worker loads the project itself: runs side by side must not share one settings object."""
        return Project(a.project, {"backend": {"mode": a.mode}})

    def one_move(cid, m):
        proj = own_project()
        prompt, frames = MOVES[m]
        aid = f"{cid}-{m}"
        if aid not in proj.asset_ids():
            proj.new_asset(aid, {"kind": "animation", "character": cid, "anim": m, "prompt": prompt, "frames": frames})
        if proj.asset(aid).state.status == "complete" or stop.is_set():
            return
        try:
            st = Pipeline(proj, aid).run(auto_approve=True, log=quiet)
            s = proj.asset(aid).state.steps
            note(f"{aid}: {st}; sheet {s['anim_poses'].info.get('sheet_size')} block {s['anim_poses'].info.get('block')} "
                 f"check {s['anim_poses'].info.get('sheet_check')} read {s['anim_traces'].info.get('frame_size')}")
        except Exception as e:  # noqa: BLE001
            note(f"{aid}: FAILED {e}")
            if out_of_credit(e):
                stop.set()

    def faces(cid):
        if stop.is_set():
            return
        proj = own_project()
        path = os.path.join(proj.asset(cid).dir, "expressions", "expressions.json")
        have = {}
        if os.path.exists(path) and not a.redo_faces:
            with open(path, encoding="utf-8") as f:
                have = json.load(f).get("expressions", {})
        names = [n for n in expressions.EXPRESSIONS if n not in have]
        own = {k: v for k, v in custom.items() if expressions.safe_name(k) not in have}
        if not names and not own:
            return
        try:
            r = expressions.make(proj, cid, names, custom=own or None, log=quiet)
            for n, e in r["expressions"].items():
                note(f"{cid} face {n}: attempts {e.get('attempts')} passed {e.get('passed')} | "
                     f"{(e.get('check') or {}).get('issue', '')}")
        except Exception as e:  # noqa: BLE001
            note(f"{cid}: faces FAILED {e}")
            if out_of_credit(e):
                stop.set()

    face_threads = [threading.Thread(target=faces, args=(c,)) for c in faces_for]
    for t in face_threads:
        t.start()
    for cid in moves_for:                                 # one character's six moves at a time
        ts = [threading.Thread(target=one_move, args=(cid, m)) for m in MOVES]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    for t in face_threads:
        t.join()
    print(f"this run: ${spent(a.project) - start:.2f}; project total ${spent(a.project):.2f}")
    if stop.is_set():
        sys.exit("Stopped: the API account has no credits left. Add credits and run this again; finished pieces are kept.")


# ------------------------------------------------------------------ the rating sheet

def rate(cid, kind, item, tags):
    """The rating controls of one move or face. The key holds the kind: a 'hurt' move and a 'hurt' face of one
    character shared a key and a radio group, so rating one cleared the other (three ratings lost, 2026-11-12)."""
    key = f"{cid}:{kind}:{item}"
    tg = "".join(f'<label class="tag"><input type="checkbox" value="{t}"><span>{t}</span></label>' for t in tags)
    seg = "".join(f'<label><input type="radio" name="{key}" value="{n}"><span>{n}</span></label>'
                  for n in range(1, 6))
    return (f'<div class="rate" data-key="{key}"><div class="seg" role="radiogroup" '
            f'aria-label="{item} {kind} rating">{seg}</div><div class="tags">{tg}</div>'
            f'<input class="note" type="text" placeholder="Note (optional)" aria-label="{item} {kind} note"></div>')


CSS = """
:root{--bg:#f5f5f7;--card:#fff;--ink:#1d1d1f;--sub:#6e6e73;--line:#d2d2d7;--acc:#0071e3;--stage:#c9ccd1}
@media (prefers-color-scheme:dark){:root{--bg:#1c1c1e;--card:#2c2c2e;--ink:#f5f5f7;--sub:#98989d;--line:#3a3a3c;--acc:#0a84ff;--stage:#8e9196}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,"Segoe UI",system-ui,sans-serif}
.bar{position:sticky;top:0;z-index:2;display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:10px 16px;
background:color-mix(in srgb,var(--bg) 85%,transparent);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.bar h1{font-size:15px;margin:0 8px 0 0}.bar a{color:var(--acc);text-decoration:none}.bar .sp{flex:1}
.bar output{color:var(--sub)}button{font:inherit;border:0;border-radius:8px;padding:6px 12px;background:var(--acc);color:#fff;cursor:pointer}
button.alt{background:var(--card);color:var(--ink);border:1px solid var(--line)}
main{max-width:1280px;margin:0 auto;padding:8px 16px 64px}.intro{color:var(--sub);max-width:70ch}
section{margin-top:32px}.who{display:flex;gap:16px;align-items:flex-end}.who h2{margin:0;font-size:20px}.who p{margin:2px 0 0;color:var(--sub)}
.who img{background:var(--stage);border-radius:10px;padding:8px}
h3{font-size:13px;color:var(--sub);font-weight:600;margin:20px 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.card{margin:0;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px;display:flex;flex-direction:column;gap:8px;min-width:0}
figcaption{font-weight:600;text-transform:capitalize}figcaption small{font-weight:400;color:var(--sub);text-transform:none;margin-left:6px}
.stage{background:var(--stage);border-radius:8px;display:flex;justify-content:center;align-items:flex-end;padding:8px;min-height:120px;overflow:hidden}
.strip{overflow-x:auto;background:var(--stage);border-radius:8px;padding:4px}
.px{image-rendering:pixelated;display:block}.auto{margin:0;font-size:12px;color:var(--sub)}
.seg{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}.seg label{flex:1}
.seg input{position:absolute;opacity:0}.seg span{display:block;text-align:center;padding:5px 0;cursor:pointer;border-right:1px solid var(--line)}
.seg label:last-child span{border-right:0}.seg input:checked+span{background:var(--acc);color:#fff}
.seg input:focus-visible+span,.tag input:focus-visible+span{outline:2px solid var(--acc);outline-offset:-2px}
.tags{display:flex;flex-wrap:wrap;gap:4px}.tag input{position:absolute;opacity:0}
.tag span{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--sub);cursor:pointer}
.tag input:checked+span{border-color:#d70015;color:#d70015}
.note{font:inherit;width:100%;padding:5px 8px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
details summary{color:var(--acc);cursor:pointer;font-size:12px}details img{margin-top:6px;background:var(--stage);border-radius:6px}
"""

JS = """
const K="pxa-validate:"+location.pathname;let saved={};try{saved=JSON.parse(localStorage.getItem(K)||"{}")}catch(e){}
const rows=[...document.querySelectorAll(".rate")];
function read(r){const v=r.querySelector(".seg input:checked");return{rating:v?v.value:"",tags:[...r.querySelectorAll(".tag input:checked")].map(x=>x.value),note:r.querySelector(".note").value.trim()}}
function save(){const o={};rows.forEach(r=>o[r.dataset.key]=read(r));try{localStorage.setItem(K,JSON.stringify(o))}catch(e){}
document.getElementById("count").textContent=rows.filter(r=>read(r).rating).length+" of "+rows.length+" rated"}
rows.forEach(r=>{const s=saved[r.dataset.key];if(s){const v=r.querySelector(`.seg input[value="${s.rating}"]`);if(v)v.checked=true;
r.querySelectorAll(".tag input").forEach(x=>x.checked=(s.tags||[]).includes(x.value));r.querySelector(".note").value=s.note||""}
r.addEventListener("change",save);r.querySelector(".note").addEventListener("input",save)});save();
function csv(){const q=s=>/[",]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;return["character,kind,item,rating,tags,note"].concat(rows.map(r=>{const[c,k,i]=r.dataset.key.split(":"),d=read(r);
return[c,k,i,d.rating,q(d.tags.join("; ")),q(d.note)].join(",")})).join("\\n")}
document.getElementById("copy").onclick=async e=>{try{await navigator.clipboard.writeText(csv());e.target.textContent="Copied"}catch(_){prompt("Copy these ratings:",csv())}setTimeout(()=>e.target.textContent="Copy ratings",1500)};
document.getElementById("dl").onclick=()=>{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([csv()],{type:"text/csv"}));a.download="validation_ratings.csv";a.click()};
for(const i of document.querySelectorAll("img[data-zoom]")){const z=()=>i.style.width=i.naturalWidth*i.dataset.zoom+"px";i.complete?z():i.onload=z}
"""


def sheet(a):
    proj = Project(a.project, {"backend": {"mode": "dry_run"}})
    root = proj.root
    os.makedirs(os.path.join(root, "rating"), exist_ok=True)

    def rel(p):
        return os.path.relpath(p, root).replace("\\", "/")
    anims = {}
    chars = []
    for aid in proj.asset_ids():
        x = proj.asset(aid)
        if x.state.inputs.get("kind") == "animation":
            anims.setdefault(x.state.inputs.get("character"), []).append(x)
        elif not x.state.inputs.get("kind") and x.state.status == "complete":
            chars.append(x)
    sections, nav, missing = [], [], []
    for ch in chars:
        cid = ch.id
        final = ch.output("export", "png")
        with Image.open(final) as im:
            im = im.convert("RGBA")
        title = os.path.splitext(os.path.basename(final))[0].replace("_", " ")
        moves = []
        for an in anims.get(cid, []):
            name = an.state.inputs.get("anim") or an.id
            ex = an.state.steps.get("anim_export")
            if not ex or ex.status != "done":
                missing.append(an.id)
                continue
            n = an.state.steps["anim_plan"].info.get("frames", "?")
            moves.append(f'<figure class="card"><figcaption>{html.escape(name)} <small>{n} frames</small></figcaption>'
                         f'<div class="stage"><img class="px" data-zoom="2" src="{rel(an.output("anim_export", "gif"))}" '
                         f'alt="{html.escape(name)} animation"></div><div class="strip"><img class="px" data-zoom="2" '
                         f'src="{rel(an.output("anim_export", "sheet"))}" alt="{html.escape(name)} frames"></div>'
                         f'{rate(cid, "move", name, MOVE_TAGS)}</figure>')
        faces = []
        ej = os.path.join(ch.dir, "expressions", "expressions.json")
        if os.path.exists(ej):
            with open(ej, encoding="utf-8") as f:
                ex = json.load(f)
            x0, y0, x1, y1 = ex["face_box"]
            box = (max(0, x0 - 3), max(0, y0 - 3), min(im.width, x1 + 3), min(im.height, y1 + 3))
            neutral = os.path.join(root, "rating", f"{cid}_neutral_face.png")
            im.crop(box).save(neutral)
            faces.append('<figure class="card"><figcaption>neutral <small>the finished sprite</small></figcaption>'
                         f'<div class="stage"><img class="px" data-zoom="8" src="{rel(neutral)}" alt="neutral face"></div></figure>')
            for name, e in ex["expressions"].items():
                whole = os.path.join(ch.dir, "expressions", e["png"])
                crop = os.path.join(root, "rating", f"{cid}_{name}_face.png")
                with Image.open(whole) as w:
                    w.convert("RGBA").crop(box).save(crop)
                chk = e.get("check") or {}
                auto = "" if "passed" not in e else (
                    "Auto-check passed" if e["passed"] else "Auto-check failed: " + html.escape(chk.get("issue") or "the skin changed")
                ) + (", drawn twice" if e.get("attempts", 1) > 1 else "")
                faces.append(f'<figure class="card"><figcaption>{html.escape(name)} <small>{e.get("pixels_changed", "?")} px changed'
                             f'</small></figcaption><div class="stage"><img class="px" data-zoom="8" src="{rel(crop)}" '
                             f'alt="{html.escape(name)} face"></div><details><summary>Whole sprite</summary><img class="px" '
                             f'data-zoom="2" src="{rel(whole)}" alt="{html.escape(name)} sprite"></details>'
                             + (f'<p class="auto" title="{html.escape(e.get("note", ""))}">{auto}</p>' if auto else "")
                             + f'{rate(cid, "face", name, FACE_TAGS)}</figure>')
        if not moves and not faces:
            continue
        nav.append(f'<a href="#{cid}">{cid}</a>')
        sections.append(f'<section id="{cid}"><header class="who"><img class="px" data-zoom="2" src="{rel(final)}" '
                        f'alt="{html.escape(title)}"><div><h2>{html.escape(title)}</h2><p>{cid}, {im.width}×{im.height} px'
                        '</p></div></header>'
                        + (f'<h3>Moves</h3><div class="grid">{"".join(moves)}</div>' if moves else "")
                        + (f'<h3>Faces</h3><div class="grid">{"".join(faces)}</div>' if faces else "") + '</section>')
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>Validation round</title><style>{CSS}</style></head><body>'
            f'<div class="bar"><h1>Validation round</h1>{"".join(nav)}<span class="sp"></span><output id="count"></output>'
            '<button class="alt" id="dl">Download CSV</button><button id="copy">Copy ratings</button></div>'
            '<main><p class="intro">Rate each move and face from 1 (unusable) to 5 (ship it), against the finished sprite. '
            'Mark what is wrong with the tags. When you are done, press Copy ratings and paste the result into the chat. '
            f'Your answers are kept in this browser as you go.</p>{"".join(sections)}</main><script>{JS}</script></body></html>')
    out = os.path.join(root, "rating.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"rating sheet: {out}" + (f"\nnot finished, left out: {', '.join(missing)}" if missing else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--project", required=True)
    r.add_argument("--source", default="bench_runs_native_batch")
    r.add_argument("--moves", default="")
    r.add_argument("--faces", default="")
    r.add_argument("--custom", action="append", help='name=description of a face of your own (repeatable)')
    r.add_argument("--redo-faces", action="store_true", help="draw the faces again even where they exist")
    r.add_argument("--mode", default="openai", choices=["openai", "dry_run"])
    r.set_defaults(fn=run)
    s = sub.add_parser("sheet")
    s.add_argument("--project", required=True)
    s.set_defaults(fn=sheet)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
