"""Rating page for a batch run (benchmarks/batch.py): each character's reference, concept and finished sprite (1x,
2x, 3x, face zoomed), rated 1-5 overall and for the face, with a note. Measurements are not shown, so they cannot
sway the rating. Ratings go to ratings.json in the batch folder.

  python benchmarks/batch_review.py --out bench_runs_batch [--port 8793]
  python benchmarks/batch_review.py --out bench_runs_batch --report"""
import argparse
import http.server
import json
import os
import sys
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image  # noqa: E402

from pixelagent.artist import face_region  # noqa: E402

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Batch review</title><style>
:root{--bg:#F5F5F7;--card:#fff;--text:#1D1D1F;--text2:#6E6E73;--accent:#5E5CE6;--check-a:#EFEFF2;--check-b:#E4E4E9}
@media (prefers-color-scheme:dark){:root{--bg:#1C1C1E;--card:#2C2C2E;--text:#F5F5F7;--text2:#98989D;--check-a:#48484C;--check-b:#3D3D41}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,"Segoe UI Variable Text","Segoe UI",system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 20px 90px}h1{font-size:26px;margin:0 0 6px}p.lead{color:var(--text2);margin:0 0 26px;max-width:72ch}
section{background:var(--card);border-radius:14px;padding:18px;margin-bottom:22px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
section h2{font-size:15px;margin:0 0 12px}.row{display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap}
figure{margin:0;display:flex;flex-direction:column;gap:6px}figcaption{font-size:12px;color:var(--text2)}
.photo{height:300px;width:auto;border-radius:10px;display:block}
.stage{border-radius:10px;padding:8px;background-color:var(--check-a);
background-image:conic-gradient(var(--check-b) 25%,transparent 0 50%,var(--check-b) 0 75%,transparent 0);background-size:16px 16px}
.stage img{image-rendering:pixelated;display:block}
.rate{display:flex;gap:18px;margin-top:14px;flex-wrap:wrap;align-items:center}.rate .lbl{font-weight:600;font-size:13px;min-width:70px}
.seg{display:flex;gap:4px;background:rgba(120,120,128,.14);border-radius:8px;padding:2px}
.seg button{border:0;background:none;border-radius:6px;padding:5px 12px;font:inherit;font-size:13px;color:var(--text);cursor:default}
.seg button[aria-pressed=true]{background:var(--card);box-shadow:0 1px 2px rgba(0,0,0,.15);font-weight:600}
textarea{width:100%;margin-top:12px;border-radius:8px;border:1px solid rgba(120,120,128,.3);background:transparent;color:var(--text);font:inherit;padding:8px;min-height:44px}
.bar{position:sticky;bottom:16px;display:flex;justify-content:flex-end;gap:12px;align-items:center}
.save{border:0;border-radius:980px;background:var(--accent);color:#fff;font:inherit;font-weight:600;padding:9px 20px}
#msg{color:var(--text2)}
</style></head><body><main>
<h1>Rate the new characters</h1>
<p class="lead">Each character was made from your reference with the new defaults: the soft look and the reduce
finish. Rate how good the sprite is as a game character (1 = poor, 5 = great), and the face on its own. A note
helps when something specific is wrong.</p>
<div id="cases"></div>
<div class="bar"><span id="msg"></span><button class="save" id="save">Save ratings</button></div>
</main><script>
const data = __DATA__;
const ratings = {}, notes = {};
const seg = (id, kind) => `<div class="rate"><span class="lbl">${kind === "overall" ? "Overall" : "Face"}</span><div class="seg" data-id="${id}" data-kind="${kind}">${[1,2,3,4,5].map((n) => `<button data-n="${n}" aria-pressed="false">${n}</button>`).join("")}</div></div>`;
document.getElementById("cases").innerHTML = data.map((c) => `<section><h2>${c.id}</h2><div class="row">
  <figure><img class="photo" src="${c.ref}" alt="reference"><figcaption>Your reference</figcaption></figure>
  <figure><img class="photo" src="${c.concept}" alt="concept"><figcaption>Concept</figcaption></figure>
  <figure><div class="stage"><img src="${c.sprite}" style="width:${c.w}px" alt="1x"></div><figcaption>1×</figcaption></figure>
  <figure><div class="stage"><img src="${c.sprite}" style="width:${c.w * 2}px" alt="2x"></div><figcaption>2×</figcaption></figure>
  <figure><div class="stage"><img src="${c.sprite}" style="width:${c.w * 3}px" alt="3x"></div><figcaption>3×</figcaption></figure>
  <figure><div class="stage"><img src="${c.face}" style="width:${c.fw * 8}px" alt="face"></div><figcaption>Face, 8×</figcaption></figure>
  </div>${seg(c.id, "overall")}${seg(c.id, "face")}<textarea data-note="${c.id}" placeholder="Optional note"></textarea></section>`).join("");
document.querySelectorAll(".seg button").forEach((b) => b.onclick = () => {
  const g = b.parentElement; (ratings[g.dataset.id] = ratings[g.dataset.id] || {})[g.dataset.kind] = Number(b.dataset.n);
  g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
});
document.querySelectorAll("textarea").forEach((t) => t.oninput = () => notes[t.dataset.note] = t.value);
document.getElementById("save").onclick = async () => {
  const r = await fetch("/ratings", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ratings, notes})});
  document.getElementById("msg").textContent = r.ok ? "Saved. You can close this page." : "Could not save";
};
</script></body></html>"""


def prepare(out):
    """Write the page's images (concept, sprite, face crop) into <out>/review_batch and return the page data."""
    rdir = os.path.join(out, "review_batch")
    os.makedirs(rdir, exist_ok=True)
    rows = json.load(open(os.path.join(out, "summary.json"), encoding="utf-8"))
    cases = []
    for r in rows:
        d = os.path.join(out, "assets", r["id"])
        st = json.load(open(os.path.join(d, "state.json"), encoding="utf-8"))
        steps = st["steps"]
        if st.get("status") != "complete":
            continue
        ref = st["inputs"]["references"][0]
        ref_path = ref if os.path.isabs(ref) else os.path.join(d, ref)
        Image.open(ref_path).convert("RGB").save(os.path.join(rdir, f"{r['id']}_ref.jpg"), quality=88)
        concept = Image.open(os.path.join(d, steps["concept"]["outputs"]["concept"])).convert("RGBA")
        bg = Image.new("RGBA", concept.size, (236, 236, 240, 255))
        bg.alpha_composite(concept)
        bg.convert("RGB").crop(concept.getbbox()).save(os.path.join(rdir, f"{r['id']}_concept.jpg"), quality=90)
        sprite = Image.open(os.path.join(d, steps["export"]["outputs"]["png"])).convert("RGBA")
        sprite = sprite.crop(sprite.getbbox())
        sprite.save(os.path.join(rdir, f"{r['id']}_sprite.png"))
        lm = json.load(open(os.path.join(d, steps["materials"]["outputs"]["labelmap"]), encoding="utf-8"))
        full = Image.open(os.path.join(d, steps["export"]["outputs"]["png"])).convert("RGBA")
        box = face_region(lm["labels"], lm["width"], lm["height"]) or (0, 0, 24, 24)
        s = max(box[2], box[3]) + 8
        cx, cy = box[0] + box[2] // 2, box[1] + box[3] // 2
        face = full.crop((cx - s // 2, cy - s // 2, cx + s // 2, cy + s // 2))
        face.save(os.path.join(rdir, f"{r['id']}_face.png"))
        cases.append({"id": r["id"], "ref": f"/img/{r['id']}_ref.jpg", "concept": f"/img/{r['id']}_concept.jpg",
                      "sprite": f"/img/{r['id']}_sprite.png", "w": sprite.width, "face": f"/img/{r['id']}_face.png",
                      "fw": face.width})
    return rdir, cases


def serve(out, port):
    rdir, cases = prepare(out)
    page = PAGE.replace("__DATA__", json.dumps(cases)).encode("utf-8")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self._send(200, "text/html; charset=utf-8", page)
            if u.path.startswith("/img/"):
                name = os.path.basename(unquote(u.path[5:]))
                p = os.path.join(rdir, name)
                if os.path.isfile(p):
                    ctype = "image/png" if name.endswith(".png") else "image/jpeg"
                    return self._send(200, ctype, open(p, "rb").read())
            self._send(404, "text/plain", b"not found")

        def do_POST(self):
            if urlparse(self.path).path != "/ratings":
                return self._send(404, "text/plain", b"not found")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            json.dump(body, open(os.path.join(out, "ratings.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
            self._send(200, "application/json", b'{"ok": true}')

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"rating page on http://127.0.0.1:{port}/ ({len(cases)} characters)", flush=True)
    httpd.serve_forever()


def report(out):
    r = json.load(open(os.path.join(out, "ratings.json"), encoding="utf-8"))
    rows = {x["id"]: x for x in json.load(open(os.path.join(out, "summary.json"), encoding="utf-8"))}
    overall, face = [], []
    for cid, v in sorted(r.get("ratings", {}).items()):
        s = rows.get(cid, {})
        overall += [v["overall"]] if "overall" in v else []
        face += [v["face"]] if "face" in v else []
        print(f"{cid:8s} overall {v.get('overall', '-')} face {v.get('face', '-')}  heads {s.get('heads_tall')} "
              f"retries {s.get('proportion_retries')}  note: {r.get('notes', {}).get(cid, '')}")
    if overall:
        print(f"mean overall {sum(overall) / len(overall):.2f}, face {sum(face) / max(1, len(face)):.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=8793)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    report(a.out) if a.report else serve(a.out, a.port)
