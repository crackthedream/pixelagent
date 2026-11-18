"""Artist agent: a vision model draws a sprite itself through the pixelliner MCP tools, guided by a reference.

The model sees the reference image and a faint tracing layer of it under its canvas (never exported). It works
like a pixel artist: study -> palette -> silhouette -> flats -> shading -> lines -> details -> self-review,
using draw/view/compare_reference/lint/undo over MCP. Every tool call and its cost are logged; spend and turns
are capped. Output: PNG + layered project + transcript + metrics.
"""
import json
import math
import os
import time

from . import imaging
from .config import load_engine
from .agent_session import DEFAULT_TOOLS as ALLOWED_TOOLS, AgentSession, interrupted
from .mcp_client import MCPClient


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
- keep a clean, consistent outline; no stray single pixels except intentional highlights.{look_rules}
`view` mode 'grid' with layer 'reference' and a small region (x, y, width, height) shows the trace's exact pixels as
palette keys: use it to copy regions that are already good, and improve the rest. pixel_map rows are the fastest way
to lay down areas. Check progress with `view` (image) or `compare_reference` (distance to the trace) after each
part, not after every op. Budget: about {turns} tool rounds; images are expensive. When done, call `finish`."""


# drawing rules per project look (config [style] look); "soft" = hand-made anime action-game sprites
LOOK_RULES = {"soft": """
This game's look is SOFT (like hand-made anime action-game sprites): light colours and gentle shading with few hard
contrasts; outlines in a darker shade of the colour they enclose, never a black line around the whole figure (dark
only where the material itself is dark: dark hair, shoes); the head and face stay large; simplify small costume
details into clean shapes rather than adding detail."""}


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


FACE_INSTRUCTIONS_V3 = """You are a senior pixel artist doing the FACE PASS on a {height}-px-tall game sprite, using the
pixelliner tools. Only the face matters now: it is the first thing a player reads and must carry the character's
expression from the concept.

The face is in region x={fx}, y={fy}, w={fw}, h={fh} of the {w}x{h} canvas (an estimate; adjust if the face is
clearly elsewhere). You get: the current sprite zoomed on that region with a pixel grid, the same region of the
trace, and a high-resolution close-up of the concept's face.

At this size an anime-style face is DESIGNED, not shrunk. Hand-made sprites in this style (e.g. Alice in Cradle's
Noel) build it the same way every time:
- an open eye is TALLER than wide: a dark lash across the top that continues down the OUTER side as a wing, a soft
  white inside that corner, the iris body hanging under the lash with the pupil low and toward the nose, lighter
  at the bottom. No lower outline, no box;
- the eyes are big: the two eyes and the gap between them span almost the whole face width, the gap a little under
  one eye width. They sit just under the fringe, low in the face. No nose; brows are usually hidden by the fringe;
- on a three-quarter face the far eye (the side the face turns toward) is narrower and the face's centre line
  moves toward it;
- the skin is FLAT: one skin colour, no blotches or specks around the eyes;
- the mouth is small and SOFT, about halfway between the eyes and the chin: none or a 1-2 px line a little darker
  than the skin for calm faces, a round salmon blob for an open mouth -- never a dark outline or an emoticon "v";
- the expression lives in the lash: lowered (half: calm, smug, sleepy), slanting up to the inner end (sad,
  worried), slanting down to the inner end (angry), ^ arcs (happy), a soft line (closed), more white (surprised).
Build the face with the face-kit ops of `draw` (skin, eyes, mouth, brows, blush); they draw exactly these parts. Do
not place eye or mouth pixels by hand. The first message gives the face MEASURED on this sprite with the kit's
sizes and positions in the game's proportions: start from those values.

1. Read the concept close-up: the eye colour; front or three-quarter view (which way the face turns); the
   expression (open, half, happy, closed, wide, sad, angry, or a wink); the mouth (none, line, smile, open smile, o,
   frown...); whether brows show below the fringe; blush.
2. Clear the old eyes and mouth: paint them over with the face's skin colour (`draw` rect or pixel_map), keeping hair,
   fringe strands and outline intact. Then flatten the skin: {{"op": "skin", "x": .., "y": .., "w": .., "h": ..}}
   over the face (the measured box).
3. eyes: {{"op": "eyes", "cx": .., "y": .., "size": .., "gap": .., "style": .., "turn": "front"|"left"|"right",
   "iris": <the concept's eye colour>, "lash": <a very dark colour: the darkest hair or brown shade, not grey>}}
   with the measured cx, y, size and gap. cx is the centre of the face, so the eyes sit symmetrically between its
   edges (for a three-quarter face, a pixel toward the side it turns to).
4. mouth: {{"op": "mouth", "cx": <same cx>, "y": <the measured mouth row>, "shape": ...}} -- leave the colours to
   the kit (they are mixed from the skin). none or line for calm, smile for a gentle smile, open_smile for a
   cheerful open mouth, o for surprise, frown for sad.
5. brows: usually none -- in this style the fringe hides them, and hard dark brows make a small face look stern.
   Only if the concept shows clear brows below the fringe: {{"op": "brows", ...}} (the kit draws them faint).
   blush only if the concept has it: {{"op": "blush", "cx": .., "y": <the row under the eyes>, "gap": ..}}.
6. Check with `view` zoomed on the region (scale 12-16) and at scale 1-2. To adjust, clear the eyes or mouth again and
   repeat the op with new values (cx, y, size, gap, style). You have about {turns} rounds. Call `finish` when the
   face is symmetric, sits correctly in the head, and reads at 1x with the concept's expression."""

FACE_RULES = {"v1": FACE_INSTRUCTIONS, "v3": FACE_INSTRUCTIONS_V3}  # v3: the face kit (default)

REVISE_INSTRUCTIONS = """You are a senior pixel artist revising a finished {height}-px-tall game sprite with the pixelliner
tools. The user reviewed it and asked for this change:

    {note}

{scope}
The drawing is already on your layers; the reference layer (bottom, never exported) is the original trace. You get:
the whole sprite, {zoom}and the concept art{concept_close}. Make the requested change well and keep the rest of the
sprite exactly as it is: same palette, outline and shading style, no other edits. Read exact pixels with `view`
mode 'grid' on a small region, change them with `draw` (a pixel_map with '=' for kept pixels is safest), and check
with `view` zoomed in. You have about {turns} rounds. Call `finish` as soon as the request is done."""


def face_region(labels, w, h, opaque=None):
    """Estimate the face box from per-pixel material labels: skin/face/eye materials in the upper part of the
    figure (not hair). Returns (x, y, w, h) with a 1-px margin, or None."""
    from .face import is_face_material  # the name must START with a face word ('shield_face' is not a face)
    face_mats = {m for m in set(labels) if m and is_face_material(m)}
    pts = [(k % w, k // w) for k, m in enumerate(labels) if m in face_mats]
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
    # feature pixels within `gap` px of each other
    gap = 3
    pset = set(head)
    label_at = {(k % w, k // w): m for k, m in enumerate(labels) if m in face_mats}

    def is_feature(p):  # eyes, mouth, blush...: what marks a face, unlike skin (a hand is skin too)
        n = label_at[p].lower().replace("-", "_").replace(" ", "_")
        return not n.startswith(("skin", "face"))

    seen, best, best_key = set(), [], (-1, -1)
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
        # the group holding the eyes/mouth is the face, even when a raised hand is bigger; else the biggest
        key = (sum(1 for q in comp if is_feature(q)), len(comp))
        if key > best_key:
            best, best_key = comp, key
    xs, ys = [p[0] for p in best], [p[1] for p in best]
    gx0, gy0, gx1, gy1 = min(xs), min(ys), max(xs), max(ys)
    feats = [q for q in best if is_feature(q)]
    if len(feats) >= 2:
        # size the box around the features, not the whole skin group: a hand near the head joins the group
        fx0, fx1 = min(q[0] for q in feats), max(q[0] for q in feats)
        fy0, fy1 = min(q[1] for q in feats), max(q[1] for q in feats)
        pad = max(3, round((fx1 - fx0 + 1) * 0.5))
        gx0, gx1 = max(gx0, fx0 - pad), min(gx1, fx1 + pad)
        gy0, gy1 = max(gy0, fy0 - 4), min(gy1, fy1 + 5)
    else:
        # no eye/mouth labels: hands near the face can join the skin group, so keep a face-sized box from the
        # top of the group (a face is about 18% of the figure's height)
        cap = max(8, round((bottom - top + 1) * 0.18))
        if gy1 - gy0 + 1 > cap:
            gy1 = gy0 + cap - 1
            row_xs = [q[0] for q in best if gy0 <= q[1] <= gy1]
            gx0, gx1 = min(row_xs), max(row_xs)
        if gx1 - gx0 + 1 > cap:
            mid = (gx0 + gx1) // 2
            gx0, gx1 = mid - cap // 2, mid - cap // 2 + cap - 1
    x0, y0 = max(0, gx0 - 1), max(0, gy0 - 1)
    x1, y1 = min(w, gx1 + 2), min(h, gy1 + 2)
    return (x0, y0, max(4, x1 - x0), max(4, y1 - y0))


def _measured_face(mcp, out, face):
    """The face measured on the current sprite (pixelagent.face.face_layout) as text for the face pass: where the skin,
    fringe line and chin are, and the face kit's sizes and positions in the game's proportions."""
    from PIL import Image
    from .face import face_layout
    path = os.path.join(out, "face_measure.png")
    try:
        _, _, err = mcp.call("export", {"path": path})
        if err:
            return ""
        with Image.open(path) as im:
            L = face_layout(im, face)
    except Exception:  # noqa: BLE001 - the measurement is advice; the pass works without it
        return ""
    if not L:
        return ""
    return (f"\nMeasured on this sprite: face skin x={L['left']}..{L['right']} ({L['skin_w']} px wide), fringe line "
            f"y={L['fringe_y']}, chin y={L['chin_y']}, skin {L['skin']}. Placement in the game's proportions: "
            f"skin {{x: {L['left']}, y: {L['fringe_y']}, w: {L['skin_w']}, h: {L['chin_y'] - L['fringe_y'] + 1}}}; "
            f"eyes cx={L['cx']}, y={L['eye_y']}, size {L['size']}, gap {L['gap']}; mouth cx={L['cx']}, y={L['mouth_y']}. "
            "Use these unless the sprite clearly shows otherwise (for a three-quarter face, shift cx a pixel toward "
            "the side the face turns to and set turn).")


def run_artist(project, asset_id, reference, height=64, client=None, model=None, max_usd=3.0, max_turns=60,
               effort="medium", log=print, trace=None, palette=None, out_dir=None, start_project=None,
               phases=("draw", "face"), face=None, concept_face=None, face_turns=12, notes=None,
               live=None, face_rules="v1", revise=None, revise_turns=14, look=None):
    """reference: concept image the model sees. trace (hybrid mode): an image already on the sprite grid (e.g. the
    pipeline's cleaned sprite) used 1:1 as the tracing layer instead of a downscaled reference; palette: colours
    ({name: hex} or [hex]) loaded into the canvas palette with single-character keys.
    start_project: continue from a saved project (its reference layer stays the trace) instead of a new canvas.
    phases: "draw" (the main drawing) and/or "face" (focused face pass; needs face=(x, y, w, h); concept_face is a
    high-resolution crop of the concept's face shown to the model).
    live: a LivePreview that receives the drawing after every round (the app shows it while the model works)."""
    cfg = project.config
    engine = load_engine(cfg)
    out = out_dir or os.path.join(project.root, "artist", asset_id)
    os.makedirs(out, exist_ok=True)
    if client is None:
        from .openai_backend import make_client
        client = make_client(cfg)
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
    sess = None
    t0 = time.perf_counter()
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
                setup.append(("palette", palette_args(palette)))
            for name, args in setup:
                txt, _, err = mcp.call(name, args)
                if err:
                    raise RuntimeError(f"setup {name} failed: {txt}")
        sess = AgentSession(project, asset_id, mcp, client, model, out, effort=effort, allowed=ALLOWED_TOOLS,
                            max_usd=max_usd, log=log,
                            on_round=(lambda se, ph: live.publish(mcp, ph, se.turns)) if live else None)
        img = sess.image_item

        pal_txt, _, _ = mcp.call("palette", {})
        if "draw" in phases:
            if trace or start_project:
                instructions = HYBRID_INSTRUCTIONS.format(height=height, name=asset_id, w=W, h=H, turns=max_turns,
                                                          light=light, look_rules=LOOK_RULES.get(look, ""))
            else:
                instructions = INSTRUCTIONS.format(height=height, name=asset_id, w=W, h=H, rx=margin, rx2=margin + rw - 1,
                                                   ry=margin, ry2=margin + height - 1, light=light,
                                                   max_colors=style.get("max_colors", 32), turns=max_turns)
            _, view_imgs, _ = mcp.call("view", {"mode": "image"})
            first = [{"type": "input_text", "text": f"Concept art for '{asset_id}' (full resolution), then the current "
                                                    f"canvas with the tracing layer. Canvas {pal_txt}. Draw the sprite."
                                                    + (" The user reviewed an earlier version and asked: " + "; ".join(notes)
                                                       if notes else "")},
                     img(ref_path)] + [img(p) for p in sess.save_imgs(view_imgs, "view")]
            stops["draw"], finishes["draw"] = sess.converse("draw", instructions, first, max_turns)

        if "face" in phases and face and not any(interrupted(reason) for reason in stops.values()):
            fx, fy, fw, fh = face
            pad = 3
            vx, vy = max(0, fx - pad), max(0, fy - pad)
            vw, vh = min(W - vx, fw + 2 * pad), min(H - vy, fh + 2 * pad)
            _, cur_imgs, _ = mcp.call("view", {"mode": "image", "x": vx, "y": vy, "width": vw, "height": vh, "scale": 16})
            _, trace_imgs, _ = mcp.call("view", {"mode": "image", "x": vx, "y": vy, "width": vw, "height": vh, "scale": 16,
                                                 "layer": "reference"})
            measured = ""
            if face_rules == "v3":
                measured = _measured_face(mcp, out, face)
            content = [{"type": "input_text", "text": f"FACE PASS. Face region x={fx}, y={fy}, w={fw}, h={fh}. Images: "
                                                      "1) your sprite zoomed on the face area (with grid), 2) the trace "
                                                      "there, 3) the concept's face close-up. Canvas " + pal_txt + measured}]
            content += [img(p) for p in sess.save_imgs(cur_imgs + trace_imgs, "faceview")]
            if concept_face and os.path.exists(concept_face):
                content.append(img(concept_face))
            instructions = FACE_RULES[face_rules].format(height=height, fx=fx, fy=fy, fw=fw, fh=fh, w=W, h=H,
                                                         turns=face_turns)
            # fresh conversation, as in the face-pass benchmark: its instructions and images stand alone, while carrying
            # the whole drawing conversation along re-sent ~30 rounds of images per call ($0.51 vs ~$0.05-0.08)
            stops["face"], finishes["face"] = sess.converse("face", instructions, content, face_turns, fresh=True)

        if "revise" in phases and revise and not any(interrupted(reason) for reason in stops.values()):
            box = revise.get("box")
            if box:
                bx, by, bw, bh = box
                scope = (f"Change ONLY the area x={bx}..{bx + bw - 1}, y={by}..{by + bh - 1} (w={bw}, h={bh}). Pixels outside "
                         "it are restored automatically afterwards, so edits there are wasted.")
                pad = 3
                vx, vy = max(0, bx - pad), max(0, by - pad)
                _, zoom_imgs, _ = mcp.call("view", {"mode": "image", "x": vx, "y": vy, "width": min(W - vx, bw + 2 * pad),
                                                    "height": min(H - vy, bh + 2 * pad), "scale": 12})
                zoom = "a zoomed view of that area with a pixel grid, "
            else:
                scope = "Change only what the request needs."
                zoom_imgs, zoom = [], ""
            _, whole, _ = mcp.call("view", {"mode": "image"})
            content = [{"type": "input_text", "text": f"REVISION. The user asked: {revise['note']}. Canvas {pal_txt}"}]
            content += [img(p) for p in sess.save_imgs(whole + zoom_imgs, "revise")] + [img(ref_path)]
            close = revise.get("concept_close")
            if close and os.path.exists(close):
                content.append(img(close))
            instructions = REVISE_INSTRUCTIONS.format(height=height, note=revise["note"], scope=scope, zoom=zoom,
                                                      concept_close=", with a close-up of the matching part" if close else "",
                                                      turns=revise_turns)
            stops["revise"], finishes["revise"] = sess.converse("revise", instructions, content, revise_turns, fresh=True)

        png_path = os.path.join(out, f"{asset_id}.png")
        mcp.call("export", {"path": png_path})
        mcp.call("save_project", {"path": os.path.join(out, "project")})
        cmp_txt, cmp_imgs, _ = mcp.call("compare_reference", {})
        if cmp_imgs:
            with open(os.path.join(out, "final_compare.png"), "wb") as f:
                f.write(cmp_imgs[0])
        lint_txt, _, _ = mcp.call("lint", {})
        summary = {"asset": asset_id, "model": model, "turns": sess.turns, "usd": round(sess.spent, 4),
                   "seconds": round(time.perf_counter() - t0, 1), "phases": list(stops),
                   "stop": "; ".join(f"{k}: {v}" for k, v in stops.items()) or "no phase ran",
                   "finish_summary": finishes.get("revise") or finishes.get("face") or finishes.get("draw"),
                   "face": list(face) if face else None,
                   "png": png_path, "project": os.path.join(out, "project"), "compare": cmp_txt.splitlines()[:3],
                   "lint": json.loads(lint_txt)}
        with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        return summary
    finally:
        if sess:
            sess.close()
        mcp.close()
        if live:
            live.clear()


def palette_args(palette):
    """Palette tool arguments: colours ({name: hex} or [hex]) under single-character keys (pixel_map friendly)."""
    from .regions import KEY_CHARS
    cols = list(palette.values()) if isinstance(palette, dict) else list(palette)
    uniq = list(dict.fromkeys(c.lower() for c in cols))[:len(KEY_CHARS)]
    return {"clear": True, "set": {KEY_CHARS[i]: c for i, c in enumerate(uniq)}}
