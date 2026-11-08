"""Artist agent: a vision model draws a sprite itself through the pixelliner MCP tools, guided by a reference.

The model sees the reference image and a faint tracing layer of it under its canvas (never exported). It works
like a pixel artist: study -> palette -> silhouette -> flats -> shading -> lines -> details -> self-review,
using draw/view/compare_reference/lint/undo over MCP. Every tool call and its cost are logged; spend and turns
are capped. Output: PNG + layered project + transcript + metrics.
"""
import base64
import json
import math
import os
import time

from . import imaging
from .config import load_engine
from .mcp_client import MCPClient

ALLOWED_TOOLS = ("palette", "draw", "layer", "view", "lint", "undo", "redo", "compare_reference", "canvas")

INSTRUCTIONS = """You are a professional pixel artist. You draw a {height}-px-tall game sprite yourself, using only the
pixelliner tools, so that it matches the reference character.

Setup already done: canvas '{name}' {w}x{h} px. Layer 'reference' (bottom) is a faint tracing copy of the reference at
sprite size: it shows in `view` images but is never exported. Draw on your own layers above it (layer 'Layer 1'
exists; add more, e.g. one per material, with `layer`). The reference occupies about x={rx}..{rx2}, y={ry}..{ry2}.

Work in phases, and check with `view` (image) or `compare_reference` at the end of each phase, not after every op:
1. Study the reference: proportions, pose, materials, light direction ({light}), key identity details.
2. Palette: `palette` with `ramp` for each material (3-5 steps, single-character keys, hue-shifted), plus an
   outline colour. Stay within ~{max_colors} colours.
3. Silhouette: block the whole figure in flat colour following the tracing layer (polygon/ellipse/rect/pixel_map).
   Run `compare_reference`: fix missing (red) / extra (blue) areas until the silhouette matches.
4. Flats: fill every material region with its base ramp colour (pixel_map rows work well for complex shapes).
5. Shading: light from the {light}, darker ramp steps on the shadow side, a few highlights; clusters, no noise,
   no pillow shading.
6. Lines: outline (draw op `outline`, optionally `selout`), interior lines in dark ramp colours.
7. Details: face/eyes, hands, held items, emblems: edit small regions precisely with pixel_map.
8. Review: `compare_reference` + `lint`, fix the worst problems, then call `finish`.

Rules: integer pixel coordinates, origin top-left. Batch many ops per `draw` call. `view` mode 'grid' with a small
region (x, y, width, height) shows exact pixels as text when you need precision. Budget: about {turns} tool rounds;
images are expensive, so view/compare only when you need to check. When done, call `finish` with a short summary."""


def _schema(tool):
    s = dict(tool.get("inputSchema") or {"type": "object", "properties": {}})
    s.setdefault("type", "object")
    s.setdefault("properties", {})
    return s


def _price(cfg, model, usage):
    p = cfg.get("pricing", {}).get(model, {})
    if usage is None:
        return 0.0
    return (usage.input_tokens * p.get("input", 0) + usage.output_tokens * p.get("output", 0)) / 1e6


HYBRID_INSTRUCTIONS = """You are a professional pixel artist producing the FINAL {height}-px-tall game sprite of this
character, drawing it yourself with the pixelliner tools.

Setup already done: canvas '{name}' {w}x{h} px. Layer 'reference' (bottom, never exported) is a palette-clean
pre-pixelisation made by an automated converter from the concept art, on exactly this pixel grid. Its proportions,
colour placement and light/shadow are reliable; its weaknesses are converter artefacts: noisy, mottled areas, mushy
small shapes, weak faces and thin props. The canvas palette already holds its colours (single-character keys) plus
the outline colour; use these keys. You also see the full-resolution concept: it is the authority for design
intent (faces, props, emblems, how shapes should read).

Your job: draw the sprite cleanly on your own layers (start on 'Layer 1', add layers if useful), following the trace
for shape and colour placement, but as deliberate pixel art:
- merge noisy, speckled areas into clean clusters and readable light/shadow shapes (light from the {light});
- redraw focal features (face/eyes, hands, held items, emblems, thin props) crisply, checking the concept;
- keep a clean, consistent outline; no stray single pixels except intentional highlights.
`view` mode 'grid' with layer 'reference' and a small region (x, y, width, height) shows the trace's exact pixels as
palette keys: use it to copy regions that are already good, and improve the rest. pixel_map rows are the fastest way
to lay down areas. Check progress with `view` (image) or `compare_reference` (distance to the trace) after each
part, not after every op. Budget: about {turns} tool rounds; images are expensive. When done, call `finish`."""


FACE_INSTRUCTIONS = """You are a senior pixel artist doing the FACE PASS on a {height}-px-tall game sprite, using the
pixelliner tools. Only the face matters now: it is the first thing a player reads, so it must carry the
character's expression from the concept.

The face is in region x={fx}, y={fy}, w={fw}, h={fh} of the {w}x{h} canvas (an estimate: include the eyes, brows
and mouth area; adjust if the face is clearly elsewhere). You get: the current sprite zoomed on that region with a
pixel grid, the same region of the trace, and a high-resolution close-up of the concept's face.

Pixel-art face rules at this size (face about {fw} px wide):
- Eyes are the darkest, most contrasting marks on the face: usually 1 px wide and 1-2 px tall each, in the
  concept's eye colour or near-black, with a 1-px light highlight only if the eye is 2x2 or larger.
- Eye spacing and height decide the character: match the concept (usually 1-3 px between the eyes, at about
  the vertical middle of the face). Both eyes must be clearly separate and symmetric unless the view is turned.
- Brows: optional 1-px dark marks above the eyes for expression (calm = flat, cheerful = raised).
- Mouth: 1-2 px in a darker skin or red tone; a calm face may need none; a smile is 2-3 px with the ends up.
- Nose: usually omitted, or 1 px of skin shadow. Blush: 1 px each side if the concept has it.
- Skin uses at most 2-3 shades from the palette; no noise on the face. Keep hair/fringe edges crisp around it.
Use `view` mode 'grid' on the region (layer of your drawing and layer 'reference') to read exact pixels, then
redraw the face region with a `draw` pixel_map (use '=' to keep pixels). Check with `view` zoomed on the region
(scale 12-16). You have about {turns} rounds. Call `finish` when the face reads clearly and matches the concept."""


def face_region(labels, w, h, opaque=None):
    """Estimate the face box from per-pixel material labels: skin/face/eye materials in the upper part of the
    figure (not hair). Returns (x, y, w, h) with a 1-px margin, or None."""
    words = ("skin", "face", "eye", "cheek", "mouth", "blush", "tongue", "lip", "nose")
    pts = [(k % w, k // w) for k, m in enumerate(labels) if m and any(x in m.lower() for x in words)
           and "hair" not in m.lower()]
    allpts = [(k % w, k // w) for k, m in enumerate(labels) if m]
    if not pts or not allpts:
        return None
    top = min(p[1] for p in allpts)
    bottom = max(p[1] for p in allpts)
    limit = top + (bottom - top) * 0.45
    head = [p for p in pts if p[1] <= limit]
    if len(head) < 6:
        return None
    # a face is a GROUP of nearby marks (skin, or separate eyes + mouth + blush on a non-skin body): join
    # feature pixels within `gap` px of each other and take the biggest group (a raised hand stays separate)
    gap = 3
    pset = set(head)
    seen, best = set(), []
    for p in head:
        if p in seen:
            continue
        comp, stack = [], [p]
        seen.add(p)
        while stack:
            q = stack.pop()
            comp.append(q)
            for dx in range(-gap, gap + 1):
                for dy in range(-gap, gap + 1):
                    r = (q[0] + dx, q[1] + dy)
                    if r in pset and r not in seen:
                        seen.add(r)
                        stack.append(r)
        if len(comp) > len(best):
            best = comp
    xs, ys = [p[0] for p in best], [p[1] for p in best]
    x0, y0 = max(0, min(xs) - 1), max(0, min(ys) - 1)
    x1, y1 = min(w, max(xs) + 2), min(h, max(ys) + 2)
    return (x0, y0, max(4, x1 - x0), max(4, y1 - y0))


def run_artist(project, asset_id, reference, height=64, client=None, model=None, max_usd=3.0, max_turns=60,
               effort="medium", log=print, trace=None, palette=None, out_dir=None, start_project=None,
               phases=("draw", "face"), face=None, concept_face=None, face_turns=12):
    """reference: concept image the model sees. trace (hybrid mode): an image already on the sprite grid (e.g. the
    pipeline's cleaned sprite) used 1:1 as the tracing layer instead of a downscaled reference; palette: colours
    ({name: hex} or [hex]) loaded into the canvas palette with single-character keys.
    start_project: continue from a saved project (its reference layer stays the trace) instead of a new canvas.
    phases: "draw" (the main drawing) and/or "face" (focused face pass; needs face=(x, y, w, h); concept_face is a
    high-resolution crop of the concept's face shown to the model)."""
    cfg = project.config
    engine = load_engine(cfg)
    out = out_dir or os.path.join(project.root, "artist", asset_id)
    os.makedirs(out, exist_ok=True)
    if client is None:
        from .openai_backend import make_client
        client = make_client(cfg)
    from .openai_backend import image_data_url
    model = model or cfg["models"].get("artist", cfg["models"]["detail"])
    style = cfg["style"]
    margin = int(style.get("canvas_margin", 2))
    light = style.get("light", "top-left")

    ref = imaging.crop_to_content(imaging.remove_background(imaging.load_rgba(reference)), pad_frac=0.0)
    ref_path = os.path.join(out, "reference.png")
    ref.save(ref_path)
    if trace:
        tr = imaging.load_rgba(trace)
        trace_path = os.path.join(out, "trace.png")
        tr.save(trace_path)
        W, H = tr.size
        rw = W - 2 * margin
        layer_args = {"path": trace_path, "width": W, "height": H, "into": "layer", "x": 0, "y": 0,
                      "palette": "none", "opacity": 0.35, "layer_name": "reference"}
    else:
        rw = max(1, math.ceil(ref.width * height / ref.height))
        W, H = rw + 2 * margin, height + 2 * margin
        layer_args = {"path": ref_path, "height": height, "into": "layer", "x": margin, "y": margin,
                      "palette": "none", "opacity": 0.35, "layer_name": "reference"}

    mcp = MCPClient(os.path.join(engine, "server.py"), env={"PIXELLINER_MCP_OUT": out})
    transcript = open(os.path.join(out, "transcript.jsonl"), "a", encoding="utf-8")
    t0 = time.perf_counter()
    st = {"spent": 0.0, "turns": 0, "prev": None}
    stops, finishes = {}, {}
    try:
        if start_project:
            txt, _, err = mcp.call("load_project", {"path": start_project, "name": asset_id})
            if err:
                raise RuntimeError(f"load_project failed: {txt}")
            info, _, _ = mcp.call("canvas", {"action": "info"})
            W, H = (int(v) for v in info.split(" ")[2].rstrip(",").split("x"))
            layers = [ln.strip().lstrip("*").split(" ", 1)[1].split(" (")[0] for ln in info.splitlines()[1:]
                      if ln.strip().startswith(("*#", "#"))]
            own = [n for n in layers if n != "reference"]
            if own:
                mcp.call("layer", {"action": "select", "layer": own[-1]})
        else:
            setup = [("new_canvas", {"width": W, "height": H, "name": asset_id}),
                     ("import_reference", layer_args),
                     ("layer", {"action": "move", "layer": "reference", "index": 0}),
                     ("layer", {"action": "select", "layer": "Layer 1"})]
            if palette:
                from .regions import KEY_CHARS
                cols = list(palette.values()) if isinstance(palette, dict) else list(palette)
                uniq = list(dict.fromkeys(c.lower() for c in cols))[:len(KEY_CHARS)]
                setup.append(("palette", {"clear": True, "set": {KEY_CHARS[i]: c for i, c in enumerate(uniq)}}))
            for name, args in setup:
                txt, _, err = mcp.call(name, args)
                if err:
                    raise RuntimeError(f"setup {name} failed: {txt}")
        tools = [{"type": "function", "name": t["name"], "description": t["description"][:1024],
                  "parameters": _schema(t), "strict": False} for t in mcp.tools() if t["name"] in ALLOWED_TOOLS]
        tools.append({"type": "function", "name": "finish", "strict": False,
                      "description": "Call when the current task is finished. Give a short summary of what you drew.",
                      "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}}})

        def save_imgs(imgs, tag):
            paths = []
            for k, png in enumerate(imgs):
                p = os.path.join(out, f"t{st['turns']:03d}_{tag}{k}.png")
                with open(p, "wb") as f:
                    f.write(png)
                paths.append(p)
            return paths

        def converse(phase, instructions, items, limit):
            """Run the tool loop until finish / plain answer / limits. Returns (stop reason, finish summary)."""
            n = 0
            finished = None
            while n < limit:
                n += 1
                st["turns"] += 1
                kwargs = dict(model=model, instructions=instructions, input=items, tools=tools,
                              reasoning={"effort": effort}, parallel_tool_calls=True)
                if st["prev"]:
                    kwargs["previous_response_id"] = st["prev"]
                t = time.perf_counter()
                resp = client.responses.create(**kwargs)
                st["prev"] = resp.id
                usage = getattr(resp, "usage", None)
                usd = _price(cfg, model, usage)
                st["spent"] += usd
                cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None) if usage else None
                project.log_cost({"asset": asset_id, "kind": "llm", "role": "artist", "phase": phase, "model": model,
                                  "usd": round(usd, 5), "input_tokens": getattr(usage, "input_tokens", None),
                                  "cached_tokens": cached, "output_tokens": getattr(usage, "output_tokens", None),
                                  "ms": int((time.perf_counter() - t) * 1000)})
                calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
                said = " ".join(getattr(c, "text", "") for o in resp.output if getattr(o, "type", None) == "message"
                                for c in getattr(o, "content", []) or [])
                if not calls:
                    transcript.write(json.dumps({"phase": phase, "turn": st["turns"], "message": said[:2000]},
                                                ensure_ascii=False) + "\n")
                    return "model answered without tool calls", said[:500]
                items, images = [], []
                for c in calls:
                    try:
                        args = json.loads(c.arguments or "{}")
                    except json.JSONDecodeError as e:
                        args, txt, imgs, err = {}, f"error: arguments are not valid JSON ({e})", [], True
                    else:
                        if c.name == "finish":
                            finished = args.get("summary", "")
                            txt, imgs, err = "ok", [], False
                        elif c.name not in ALLOWED_TOOLS:
                            txt, imgs, err = f"error: tool '{c.name}' is not available", [], True
                        elif c.name == "layer" and ("reference" in args or args.get("action") == "merge_down"):
                            # the tracing layer must stay a reference: turning it (or merging into it) into real
                            # pixels would export the converted trace instead of the model's own drawing
                            txt, imgs, err = ("error: changing the reference flag and merge_down are disabled here; "
                                              "draw on your own layers"), [], True
                        else:
                            txt, imgs, err = mcp.call(c.name, args)
                    images += save_imgs(imgs, c.name)
                    items.append({"type": "function_call_output", "call_id": c.call_id,
                                  "output": txt[:6000] + (f"\n({len(imgs)} image(s) attached below)" if imgs else "")})
                    transcript.write(json.dumps({"phase": phase, "turn": st["turns"], "tool": c.name,
                                                 "args": json.dumps(args)[:1500], "error": err, "result": txt[:600],
                                                 "images": len(imgs), "usd": round(usd, 5)}, ensure_ascii=False) + "\n")
                transcript.flush()
                if images:
                    items.append({"role": "user", "content": [{"type": "input_text", "text": "Images from your tool calls:"}] +
                                  [{"type": "input_image", "image_url": image_data_url(p, 1024), "detail": "high"}
                                   for p in images]})
                log(f"[artist {asset_id} {phase}] turn {st['turns']}: {', '.join(c.name for c in calls)}  (${st['spent']:.3f})")
                if finished is not None:
                    # the model expects outputs for its last calls: keep them for the next phase's first request
                    st["pending"] = items
                    return "finished", finished
                if st["spent"] >= max_usd:
                    return f"budget ${max_usd:.2f} reached", None
            st["pending"] = items
            return f"turn limit {limit} reached", None

        pal_txt, _, _ = mcp.call("palette", {})
        if "draw" in phases:
            if trace or start_project:
                instructions = HYBRID_INSTRUCTIONS.format(height=height, name=asset_id, w=W, h=H, turns=max_turns,
                                                          light=light)
            else:
                instructions = INSTRUCTIONS.format(height=height, name=asset_id, w=W, h=H, rx=margin, rx2=margin + rw - 1,
                                                   ry=margin, ry2=margin + height - 1, light=light,
                                                   max_colors=style.get("max_colors", 32), turns=max_turns)
            _, view_imgs, _ = mcp.call("view", {"mode": "image"})
            first = [{"type": "input_text", "text": f"Concept art for '{asset_id}' (full resolution), then the current "
                                                    f"canvas with the tracing layer. Canvas {pal_txt}. Draw the sprite."},
                     {"type": "input_image", "image_url": image_data_url(ref_path, 1024), "detail": "high"}]
            first += [{"type": "input_image", "image_url": image_data_url(p, 1024), "detail": "high"}
                      for p in save_imgs(view_imgs, "view")]
            stops["draw"], finishes["draw"] = converse("draw", instructions, [{"role": "user", "content": first}],
                                                       max_turns)

        if "face" in phases and face and st["spent"] < max_usd:
            fx, fy, fw, fh = face
            pad = 3
            vx, vy = max(0, fx - pad), max(0, fy - pad)
            vw, vh = min(W - vx, fw + 2 * pad), min(H - vy, fh + 2 * pad)
            _, cur_imgs, _ = mcp.call("view", {"mode": "image", "x": vx, "y": vy, "width": vw, "height": vh, "scale": 16})
            _, trace_imgs, _ = mcp.call("view", {"mode": "image", "x": vx, "y": vy, "width": vw, "height": vh, "scale": 16,
                                                 "layer": "reference"})
            content = [{"type": "input_text", "text": f"FACE PASS. Face region x={fx}, y={fy}, w={fw}, h={fh}. Images: "
                                                      "1) your sprite zoomed on the face area (with grid), 2) the trace "
                                                      "there, 3) the concept's face close-up. Canvas " + pal_txt}]
            content += [{"type": "input_image", "image_url": image_data_url(p, 1024), "detail": "high"}
                        for p in save_imgs(cur_imgs + trace_imgs, "faceview")]
            if concept_face and os.path.exists(concept_face):
                content.append({"type": "input_image", "image_url": image_data_url(concept_face, 1024), "detail": "high"})
            items = list(st.pop("pending", [])) + [{"role": "user", "content": content}]
            instructions = FACE_INSTRUCTIONS.format(height=height, fx=fx, fy=fy, fw=fw, fh=fh, w=W, h=H, turns=face_turns)
            stops["face"], finishes["face"] = converse("face", instructions, items, face_turns)

        png_path = os.path.join(out, f"{asset_id}.png")
        mcp.call("export", {"path": png_path})
        mcp.call("save_project", {"path": os.path.join(out, "project")})
        cmp_txt, cmp_imgs, _ = mcp.call("compare_reference", {})
        if cmp_imgs:
            with open(os.path.join(out, "final_compare.png"), "wb") as f:
                f.write(cmp_imgs[0])
        lint_txt, _, _ = mcp.call("lint", {})
        summary = {"asset": asset_id, "model": model, "turns": st["turns"], "usd": round(st["spent"], 4),
                   "seconds": round(time.perf_counter() - t0, 1), "phases": list(stops),
                   "stop": "; ".join(f"{k}: {v}" for k, v in stops.items()) or "no phase ran",
                   "finish_summary": finishes.get("face") or finishes.get("draw"), "face": list(face) if face else None,
                   "png": png_path, "project": os.path.join(out, "project"), "compare": cmp_txt.splitlines()[:3],
                   "lint": json.loads(lint_txt)}
        with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        return summary
    finally:
        transcript.close()
        mcp.close()
