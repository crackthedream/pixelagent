"""A self-contained HTML review page per asset (assets/<id>/review/index.html), regenerated whenever the
pipeline stops. It shows every finished step's outputs and, at a gate, the exact command to answer it."""
import html
import json
import os

CSS = """
:root { --bg:#f6f5f2; --fg:#1d1b22; --muted:#6b6875; --card:#fff; --line:#e3e0da; --accent:#5b4bd6; --warn:#b5541b; }
@media (prefers-color-scheme: dark) { :root { --bg:#17161b; --fg:#ece9f2; --muted:#a19dab; --card:#211f27; --line:#34313c; --accent:#a99cff; --warn:#f0a36b; } }
body { margin:0; padding:24px 16px 64px; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
main { max-width:1100px; margin:0 auto; }
h1 { margin:0 0 4px; font-size:22px; } .muted { color:var(--muted); }
.gate { border:2px solid var(--accent); border-radius:10px; padding:12px 16px; margin:16px 0; background:var(--card); }
.gate code { display:block; margin-top:6px; padding:6px 8px; background:var(--bg); border-radius:6px; overflow-x:auto; }
section { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; margin:14px 0; }
section h2 { margin:0 0 6px; font-size:16px; } .status { font-size:12px; padding:1px 8px; border-radius:99px; border:1px solid var(--line); margin-left:6px; }
.imgs { display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; }
figure { margin:0; } figcaption { font-size:12px; color:var(--muted); }
img.px { image-rendering:pixelated; background:repeating-conic-gradient(#ddd 0 25%, #f4f4f4 0 50%) 0 0/16px 16px; max-width:100%; }
pre { white-space:pre-wrap; font-size:12px; background:var(--bg); padding:8px; border-radius:6px; max-height:260px; overflow:auto; }
.failed { color:var(--warn); }
"""

ZOOM = {"pixel_base": 6, "preview": 6, "labels": 6, "final_1x": 1, "final_zoom": 1, "png": 6, "ramps": 2, "concept": 1}


def write_review(pipe):
    a = pipe.asset
    st = a.state
    out_dir = os.path.join(a.dir, "review")
    os.makedirs(out_dir, exist_ok=True)
    gate = pipe.pending_gate()
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Review {html.escape(a.id)}</title><style>{CSS}</style></head><body><main>",
             f"<h1>{html.escape(a.id)}</h1><div class='muted'>status: {html.escape(st.status)} · cost so far: ${pipe.project.cost_total(a.id):.2f}</div>"]
    if gate:
        cmd = f"python -m pixelagent --project \"{pipe.project.root}\" approve {a.id}"
        extra = ""
        if gate.name == "concept":
            extra = " --pick N"
        parts.append(f"<div class='gate'><b>Gate {gate.gate}: {html.escape(gate.gate_title)}</b>"
                     f"<code>{html.escape(cmd + extra)}</code>"
                     f"<code>{html.escape(f'python -m pixelagent --project \"{pipe.project.root}\" reject {a.id} --note \"what to change\"')}</code></div>")
    for s in pipe.steps:
        rec = st.steps.get(s.name)
        if not rec or rec.status == "pending":
            continue
        cls = " failed" if rec.status == "failed" else ""
        parts.append(f"<section><h2>{s.index}. {s.name}<span class='status{cls}'>{rec.status}</span>"
                     f"<span class='muted' style='font-size:12px'> v{rec.version:03d}</span></h2>")
        imgs = []
        for key, rel in rec.outputs.items():
            if rel.lower().endswith(".png"):
                src = os.path.relpath(a.path(rel), out_dir).replace("\\", "/")
                z = ZOOM.get(key, 1)
                imgs.append(f"<figure><img class='px' src='{html.escape(src)}' data-zoom='{z}' alt='{html.escape(key)}'>"
                            f"<figcaption>{html.escape(key)}</figcaption></figure>")
        if imgs:
            parts.append("<div class='imgs'>" + "".join(imgs) + "</div>")
        for key, rel in rec.outputs.items():
            if key in ("spec", "critique", "lint") and rel.endswith(".json"):
                with open(a.path(rel), encoding="utf-8") as f:
                    parts.append(f"<details{' open' if key != 'lint' else ''}><summary>{key}.json</summary><pre>{html.escape(f.read())}</pre></details>")
        info = {k: v for k, v in rec.info.items() if k not in ("traceback", "dir")}
        if info:
            parts.append(f"<pre>{html.escape(json.dumps(info, indent=1, ensure_ascii=False))}</pre>")
        if rec.error:
            parts.append(f"<pre class='failed'>{html.escape(rec.error)}\n{html.escape(rec.info.get('traceback', ''))}</pre>")
        parts.append("</section>")
    parts.append("""<script>
for (const img of document.querySelectorAll('img.px')) {
  img.addEventListener('load', () => { const z = +img.dataset.zoom || 1; if (z > 1) img.style.width = (img.naturalWidth * z) + 'px'; });
}
</script></main></body></html>""")
    page = os.path.join(out_dir, "index.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return page
