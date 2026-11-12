"""Animation pipeline (M3): an animation asset is linked to an approved character asset and driven by the user's
prompt ("6-frame walk cycle", "swing the wrench overhead").

  plan    the director model turns the prompt into an AnimationSpec (frames, poses, timing, loop, in place)
  poses   the image model draws all frames side by side in ONE pose sheet (consistency), using the concept and the
          approved sprite as references -> gate P (approve, or reject with a note to regenerate)
  traces  the sheet is cut into frames, converted at ONE shared scale, aligned on a common ground line and mapped
          to the approved sprite's palette (+ outline) -> per-frame traces on one canvas
  draw    the artist agent draws frame by frame through MCP: each frame starts from the previous frame's drawing
          (frame 1 from the approved sprite), with that frame's trace underneath and the approved sprite as model
          sheet; it changes only what moves. Frame-consistency lint afterwards.
  review  gate R
  export  GIF, sprite sheet, per-frame PNGs, layered project, meta.json (durations, loop, pivot)
"""
import json
import os
import shutil

import numpy as np

from .. import imaging, prompts
from ..schemas import AnimationSpec, CharacterSpec, FramePlan


# ------------------------------------------------------------------ character access

def character(ctx):
    cid = ctx.asset.state.inputs["character"]
    ch = ctx.project.asset(cid)
    ex = ch.state.steps.get("export")
    if not ex or ex.status != "done":
        raise ValueError(f"character '{cid}' has no exported sprite yet: finish its pipeline first")
    with open(ch.output("intake", "spec"), encoding="utf-8") as f:
        spec = CharacterSpec.model_validate_json(f.read())
    with open(ch.output("export", "meta"), encoding="utf-8") as f:
        meta = json.load(f)
    return {"asset": ch, "spec": spec, "png": ch.output("export", "png"), "concept": ch.output("concept", "concept"),
            "project": ch.output("export", "project"), "palette": meta["palette"]}


def native_mode(ctx):
    """The native finish: the pose sheet is drawn as pixel art and each frame is read back pixel for pixel."""
    return ctx.cfg.get("finish", {}).get("mode") == "native"


def _sheet_block(ch, n, size):
    """Image pixels per sprite pixel for an n-frame pixel-art sheet of this character: as large as the sheet allows
    (the sprite's height fits the sheet's height, n sprites fit its width with room between them)."""
    sprite = imaging.load_rgba(ch["png"])
    sb = imaging.content_bbox(sprite, 1)
    sw, sh = max(1, sb[2] - sb[0]), max(1, sb[3] - sb[1])
    W, H = (int(v) for v in size.split("x"))
    return max(3, min(int(H * 0.82 / sh), int(W / n * 0.78 / sw))), sh


def sheet_plan(ch, n, acfg):
    """The pose sheet's size and block: the normal sheet, or the wide one when the normal one would need blocks of
    under [animation] min_block image pixels. Asked for 3-px blocks the image model drew 4-px ones on a third of the
    sheets, so those frames had 25% too few pixels; on the wide sheet the same six frames get 6-px blocks at the
    same price (validation round, 2026-11-12)."""
    size = acfg.get("sheet_size", "1536x1024")
    block, sh = _sheet_block(ch, n, size)
    wide = acfg.get("sheet_wide")
    if wide and block < int(acfg.get("min_block", 5)):
        b2, _ = _sheet_block(ch, n, wide)
        if b2 > block:
            size, block = wide, b2
    return size, block, sh


def layout_sheet(sprite, n, block, size, bob=0):
    """The pose sheet's layout for the native finish: the finished sprite placed n times at its final pixel scale (one
    sprite pixel = block x block image pixels), centred in n equal cells on one ground line. The image model redraws
    each copy in its pose: without it, it drew the figures 0.8-1.35x the sprite's size and crowded them so they were
    cut apart wrongly (move-set run, 2026-11-11). bob (sprite pixels) lifts the copies on a sine: the dry-run sheet."""
    from PIL import Image
    sp = sprite.crop(sprite.getbbox())
    big = sp.resize((sp.width * block, sp.height * block), Image.NEAREST)
    W, H = (int(v) for v in size.split("x"))
    cell = W // n
    sheet = Image.new("RGBA", (W, H), (222, 222, 226, 255))
    ground = H - max(40, (H - big.height) // 3)
    for i in range(n):
        lift = block * int(round(bob * np.sin(i / n * 2 * np.pi)))
        x = i * cell + (cell - big.width) // 2 // block * block
        sheet.alpha_composite(big, (x, ground - big.height - lift))
    return sheet


def _components(mask):
    """8-connected components of a boolean mask: a label array (0 = background) and the size of each label."""
    from collections import deque
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    sizes = [0]
    for y0, x0 in zip(*np.nonzero(mask)):
        if lab[y0, x0]:
            continue
        k = len(sizes)
        lab[y0, x0] = k
        q, n = deque([(y0, x0)]), 0
        while q:
            y, x = q.popleft()
            n += 1
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not lab[yy, xx]:
                        lab[yy, xx] = k
                        q.append((yy, xx))
        sizes.append(n)
    return lab, sizes


def clean_frame(frame):
    """Drop what is not this frame's figure: pieces of a neighbouring figure that enter at the crop's edge (smaller
    than a third of the figure) and specks of under 5 pixels further than 2 pixels from it."""
    from PIL import Image
    a = np.asarray(frame.convert("RGBA")).copy()
    lab, sizes = _components(a[..., 3] > 0)
    if len(sizes) <= 2:
        return frame
    main = int(np.argmax(sizes))
    near = lab == main
    for _ in range(2):                                    # the figure grown by 2 pixels
        g = near.copy()
        g[1:] |= near[:-1]; g[:-1] |= near[1:]; g[:, 1:] |= near[:, :-1]; g[:, :-1] |= near[:, 1:]
        near = g
    h, w = lab.shape
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])).tolist())
    for k in range(1, len(sizes)):
        if k == main:
            continue
        piece = lab == k
        if (k in edge and sizes[k] < sizes[main] / 3) or (sizes[k] < 5 and not (piece & near).any()):
            a[piece] = 0
    out = Image.fromarray(a)
    return out.crop(out.getbbox()) if out.getbbox() else out


COARSE, SMALL = 1.06, 0.94      # drawn block / asked block, and silhouette scale, beyond which frames are enlarged
RETRY_COARSE, RETRY_SMALL = 1.15, 0.90   # ... and beyond which the sheet is first drawn again


def silhouette_scale(frames, sprite):
    """The figures' size against the sprite's, from their areas: the median of sqrt(frame pixels / sprite pixels).
    A pose changes the height (a lean, a crouch) far more than the area."""
    def area(im):
        return float((np.asarray(im.convert("RGBA"))[..., 3] > 0).sum())
    a0 = max(1.0, area(sprite))
    return float(np.median([np.sqrt(area(f) / a0) for f in frames])) if frames else 1.0


def too_coarse(fit):
    """Is a sheet's reading (read_pixel_frames' info) bad enough to draw the sheet again?"""
    return bool(fit) and fit.get("grid", 1.0) > RETRY_COARSE and fit.get("area", 1.0) < RETRY_SMALL


def read_pixel_frames(sheet, boxes, block, target_h, tol=0.03, sprite=None):
    """Read each figure of a pixel-art pose sheet back at 1x on ONE block size measured on the whole sheet. The image
    model draws walking figures a little bigger or smaller than asked (~6% taller than the standing sprite seen);
    the scale comes from the TALLER half of the frames -- the upright ones: a jump's or an attack's crouching frames
    are short on purpose. Only figures drawn BIGGER are corrected (up to 1.4x): with the layout reference the size is
    pinned, and a shorter reading is the pose itself (a run's tallest frames still lean: enlarging them by 11% made
    the runner bigger than the character, 2026-11-11). When it is more than tol too big, the sheet is read again on a
    proportionally larger
    (smaller) regular grid -- a resize done on the drawn high-resolution sheet, where each 1x pixel takes its patch's
    dominant colour, instead of on the finished 1x frames.
    sprite (the finished sprite, cropped): also catches pixels drawn COARSER than asked. The figure then has the
    layout's size on the sheet but too few pixels (blocks of 4 px for 3 asked: frames at 0.75x). Told apart from a
    pose by the block grid itself (drawn block / asked block > COARSE) together with the silhouette's area (< SMALL
    of the sprite's); the sheet is then read on the asked block size.
    Returns (frames, image px per sprite px, info)."""
    from ..pixelart import find_grid, sprite_from_pixel_art
    g = find_grid(sheet, expected=block)

    def read(period, snap):
        out = []
        for b in boxes:
            pad = int(block * 2)
            crop = sheet.crop((max(0, b[0] - pad), max(0, b[1] - pad), min(sheet.width, b[2] + pad),
                               min(sheet.height, b[3] + pad)))
            sp, _ = sprite_from_pixel_art(crop, margin=0, period=period, snap=snap)
            out.append(clean_frame(sp.crop(sp.getbbox())) if sp.getbbox() else sp)
        return out
    frames = read((g["px"], g["py"]), True)
    heights = sorted((f.height for f in frames), reverse=True)
    upright = heights[:(len(heights) + 1) // 2]
    drawn = upright[len(upright) // 2]
    ratio = drawn / float(max(1, target_h))
    info = {"drawn_height": drawn, "target_height": int(target_h), "resized": False}
    coarse = (g["px"] + g["py"]) / 2 / float(block)
    if sprite is not None:
        info.update(grid=round(coarse, 3), area=round(silhouette_scale(frames, sprite), 3))
    if 1 + tol < ratio <= 1.4:
        frames = read((g["px"] * ratio, g["py"] * ratio), False)
        info.update(resized=True, ratio=round(ratio, 3))
    elif sprite is not None and coarse > COARSE and info["area"] < SMALL:
        ratio = 1.0 / coarse
        frames = read((g["px"] * ratio, g["py"] * ratio), False)
        info.update(resized=True, enlarged=True, ratio=round(ratio, 3))
    return frames, (g["px"] + g["py"]) / 2 * (ratio if info["resized"] else 1.0), info


def reduce_mode(ctx):
    """The reduce finish (soft look): frames are the pose sheet's own reductions, like the character itself."""
    # native characters animate through the same path for now (a pixel-art pose sheet is a later step)
    return ctx.cfg.get("finish", {}).get("mode") in ("reduce", "native")


def load_plan(ctx):
    with open(ctx.input("anim_plan", "plan"), encoding="utf-8") as f:
        return AnimationSpec.model_validate_json(f.read())


# ------------------------------------------------------------------ steps

def plan(ctx):
    ch = character(ctx)
    inp = ctx.asset.state.inputs
    request = inp.get("prompt") or "idle animation"
    n_req = inp.get("frames")
    sprite_zoom = ctx.save_image(None, "model_sheet.png", imaging.zoom(imaging.load_rgba(ch["png"]), 4))

    def stub():
        n = int(n_req or 4)
        return AnimationSpec(name=inp.get("anim") or "anim", summary=request,
                             frames=[FramePlan(index=i, pose=f"pose {i + 1} of '{request}'", duration_ms=120)
                                     for i in range(n)], loop=True, in_place=True,
                             moves=["limbs"], constant=["face", "costume colours", "outline"])

    text = request + "".join(f"\n{n}" for n in ctx.notes()) + (f"\nUse exactly {n_req} frames." if n_req else "")
    spec = ctx.backend.llm.structured("director", prompts.ANIM_PLAN.format(request=request, view=ch["spec"].view),
                                      text, [sprite_zoom, ch["concept"]], AnimationSpec, stub)
    if n_req and len(spec.frames) != int(n_req):
        raise ValueError(f"plan has {len(spec.frames)} frames, the request asked for {n_req}")
    if not 1 <= len(spec.frames) <= 12:
        raise ValueError(f"plan has {len(spec.frames)} frames (1-12 allowed)")
    for i, f in enumerate(spec.frames):
        f.index = i
    ctx.save_json("plan", "animation.json", spec)
    ctx.info.update(frames=len(spec.frames), loop=spec.loop, in_place=spec.in_place, summary=spec.summary[:200])


def poses(ctx):
    ch = character(ctx)
    spec = load_plan(ctx)
    n = len(spec.frames)
    frames_txt = "\n".join(f"{i + 1}) {f.pose}" for i, f in enumerate(spec.frames))
    prompt = prompts.POSE_SHEET.format(n=n, view=ch["spec"].view.replace("_", "-"), summary=spec.summary,
                                       frames=frames_txt)
    acfg = ctx.cfg.get("animation", {})
    size = acfg.get("sheet_size", "1536x1024")
    block = None
    if native_mode(ctx):
        size, block, sprite_h = sheet_plan(ch, n, acfg)
        ctx.info["sheet_size"] = size
        prompt += prompts.POSE_SHEET_PIXEL.format(block=block, height=sprite_h, n=n)
        ctx.info["block"] = block
    elif reduce_mode(ctx):
        # the frames are reduced straight from this sheet: it must be drawn like the concept, not as pixel art
        prompt += prompts.POSE_SHEET_SOFT
    notes = ctx.notes()
    if notes:
        prompt += "\nChanges requested: " + "; ".join(notes)
    with open(ctx.path("pose_prompt.txt"), "w", encoding="utf-8") as f:
        f.write(prompt)
    sprite = imaging.load_rgba(ch["png"])
    if block:
        # the layout pins the figures' size, scale and spacing; the sprite alone at the same scale shows the design
        refs = [ctx.save_image(None, "layout.png", layout_sheet(sprite, n, block, size)),
                ctx.save_image(None, "sprite_ref.png", imaging.zoom(sprite.crop(sprite.getbbox()), block))]
    else:
        refs = [ch["concept"], ctx.save_image(None, "sprite_ref.png", imaging.zoom(sprite, 6))]

    def native_stub(k, out_dir):  # dry run, native: the layout with a little bob per frame
        sheet = layout_sheet(sprite, n, block, size, bob=2)
        p = os.path.join(out_dir, "gen00.png")
        sheet.convert("RGB").save(p)
        return [p]

    def stub(k, out_dir):  # dry run: the concept repeated with a little bob and lean per frame
        if block:
            return native_stub(k, out_dir)
        from PIL import Image
        c = imaging.load_rgba(ch["concept"])
        W, H = (int(v) for v in size.split("x"))
        cell = W // n
        sc = min((cell - 20) / c.width, (H - 80) / c.height)
        fig = c.resize((max(1, int(c.width * sc)), max(1, int(c.height * sc))))
        sheet = Image.new("RGBA", (W, H), (222, 222, 226, 255))
        for i in range(n):
            bob = int(6 * np.sin(i / n * 2 * np.pi))
            f2 = fig.rotate(3 * np.sin(i / n * 2 * np.pi), expand=True)
            sheet.alpha_composite(f2, (i * cell + (cell - f2.width) // 2, H - 40 - f2.height - bob))
        p = os.path.join(out_dir, "gen00.png")
        sheet.convert("RGB").save(p)
        return [p]

    def generate(text, folder):
        return ctx.backend.images.generate("image_final", text, 1, ctx.path(folder), refs, stub,
                                           quality=acfg.get("sheet_quality", "medium"), size=size)[0]

    def measure(path):
        """The sheet, its figures, and how they read back against the sprite (native finish only)."""
        sh = imaging.load_rgba(path)
        ps = imaging.split_sheet(sh, n)
        fit = None
        if block:
            sp = sprite.crop(sprite.getbbox())
            fit = read_pixel_frames(sh, [b for _, b in ps], block, sp.height, sprite=sp)[2]
        return sh, ps, fit

    sheet, parts, fit = measure(generate(prompt, "generated"))
    if fit:
        tries = [fit]
        for k in range(int(acfg.get("sheet_retries", 1))):
            if not too_coarse(fit):
                break
            ctx.log(f"[anim_poses] the sheet's pixels came out coarse (x{fit['grid']}, figures at {fit['area']} of "
                    "the sprite): drawing it once more")
            try:
                again = measure(generate(prompt + prompts.POSE_SHEET_RETRY.format(block=block), f"generated_retry{k + 1}"))
            except (ValueError, OSError) as e:      # a sheet that cannot be cut into its figures: keep the first
                ctx.log(f"[anim_poses] the second sheet could not be used: {e}")
                break
            tries.append(again[2])
            if again[2]["area"] > fit["area"]:
                sheet, parts, fit = again
        ctx.info["sheet_check"] = {"attempts": len(tries), "readings": [{k: t.get(k) for k in ("grid", "area")} for t in tries],
                                   "kept": {k: fit.get(k) for k in ("grid", "area")}, "coarse": too_coarse(fit)}
    for i, (fig, box) in enumerate(parts):
        ctx.save_image(f"figure{i}", f"figure_{i:02d}.png", fig)
    ctx.save_image("sheet", "pose_sheet.png", sheet)
    ctx.save_json("boxes", "figures.json", {"boxes": [list(b) for _, b in parts], "sheet_size": list(sheet.size)})
    thumbs = [f.resize((max(1, f.width * 200 // f.height), 200)) for f, _ in parts]
    ctx.save_image("figures", "figures_sheet.png", imaging.contact_sheet(thumbs, [f"#{i + 1}" for i in range(n)], 1))
    ctx.info.update(frames=n, suggested=None)


def traces(ctx):
    art = ctx.art
    ch = character(ctx)
    spec = load_plan(ctx)
    n = len(spec.frames)
    with open(ctx.input("anim_poses", "boxes"), encoding="utf-8") as f:
        boxes = json.load(f)["boxes"]
    figs = [imaging.load_rgba(ctx.input("anim_poses", f"figure{i}")) for i in range(n)]
    sprite = imaging.load_rgba(ch["png"])
    sb = imaging.content_bbox(sprite, 1)
    margin = int(ctx.style["canvas_margin"])
    soft = reduce_mode(ctx)
    ol = 0 if ch["spec"].outline == "none" or soft else 1   # the reduce finish adds no outline ring
    body_h = (sb[3] - sb[1]) - 2 * ol                 # the approved sprite's height without its outline
    ground = max(b[3] for b in boxes)                  # common ground line of the sheet
    if native_mode(ctx):
        # each figure is pixel art on the sheet's block grid: read back pixel for pixel on one block size, and at the
        # standing sprite's height (read_pixel_frames)
        sheet = imaging.load_rgba(ctx.input("anim_poses", "sheet"))
        block = ctx.asset.state.steps["anim_poses"].info.get("block") or 8
        small, s, fit = read_pixel_frames(sheet, boxes, block, sb[3] - sb[1], sprite=sprite.crop(sprite.getbbox()))
        ctx.info["frame_size"] = fit
    else:
        heights = sorted(b[3] - b[1] for b in boxes)
        s = heights[len(heights) // 2] / float(body_h)    # one scale for all frames: median figure = sprite height
        small = [imaging.pixelize(f, body_h, margin=0, scale=s, alpha_threshold=int(ctx.style["alpha_threshold"]))
                 for f in figs]
    lifts = [int(round((ground - b[3]) / s)) for b in boxes]
    pad = margin + ol + 2                              # outline + margin + a little room for motion
    W = max(sprite.width, max(im.width for im in small) + 2 * pad)
    H = max(sprite.height, max(im.height + l for im, l in zip(small, lifts)) + 2 * pad)
    ground_y = H - pad                                 # bottom edge of the figure (before outline)
    pal = {k: v for k, v in ch["palette"].items()}
    from canvas import parse_hex
    pal_rgb = [parse_hex(v)[:3] for v in pal.values()]
    oc = parse_hex(pal.get("outline", ctx.style["outline_color"]))
    out = []
    for i, im in enumerate(small):
        d = imaging.to_bytes(im)
        art.quantize(im.width, im.height, d, pal_rgb)
        canvas_img = bytearray(W * H * 4)
        ox = (W - im.width) // 2
        oy = ground_y - lifts[i] - im.height
        for y in range(im.height):
            ty = oy + y
            if 0 <= ty < H:
                row = d[y * im.width * 4:(y + 1) * im.width * 4]
                for x in range(im.width):
                    tx = ox + x
                    if 0 <= tx < W and row[x * 4 + 3]:
                        canvas_img[(ty * W + tx) * 4:(ty * W + tx) * 4 + 4] = row[x * 4:x * 4 + 4]
        labels = ["f" if canvas_img[k * 4 + 3] else None for k in range(W * H)]
        if native_mode(ctx):
            pass   # pixel art as drawn: its single pixels are deliberate (eye highlights, strands)
        elif soft:   # keep the soft shading: only near-identical specks merge, as in the character's reduction
            art.merge_small_clusters(W, H, canvas_img, labels, min_size=2, max_dl=0.04)
        else:
            art.merge_small_clusters(W, H, canvas_img, labels, int(ctx.cfg["pixelize"].get("min_cluster", 3)))
        if ol:
            tmp = ctx.canvas.Document("t", W, H)
            tmp.layers[0].cels[0] = canvas_img
            ctx.canvas.Painter(tmp, 0).outline(oc, clean=True)
            canvas_img = tmp.layers[0].cels[0]
        out.append(canvas_img)
        ctx.save_image(f"trace{i}", f"trace_{i:02d}.png", imaging.from_bytes(W, H, canvas_img))
    # frame 1 of the drawing starts from the approved sprite, standing on the same ground line
    base = bytearray(W * H * 4)
    sd = imaging.to_bytes(sprite)
    sx = (W - (sb[2] - sb[0])) // 2 - sb[0]
    sy = (ground_y + ol) - sb[3]
    for y in range(sprite.height):
        for x in range(sprite.width):
            k = (y * sprite.width + x) * 4
            tx, ty = x + sx, y + sy
            if sd[k + 3] and 0 <= tx < W and 0 <= ty < H:
                base[(ty * W + tx) * 4:(ty * W + tx) * 4 + 4] = sd[k:k + 4]
    ctx.save_image("base", "base.png", imaging.from_bytes(W, H, base))
    strip = imaging.contact_sheet([imaging.from_bytes(W, H, t) for t in out], [f"#{i + 1}" for i in range(n)], 3)
    ctx.save_image("strip", "traces_strip.png", strip)
    ctx.info.update(canvas=[W, H], scale=round(s, 3), lifts=lifts)


def draw(ctx):
    ch = character(ctx)
    spec = load_plan(ctx)
    n = len(spec.frames)
    canvas, projectio = ctx.canvas, ctx.projectio
    traces = [imaging.to_bytes(imaging.load_rgba(ctx.input("anim_traces", f"trace{i}"))) for i in range(n)]
    base = imaging.load_rgba(ctx.input("anim_traces", "base"))
    W, H = base.size
    doc = canvas.Document(ctx.asset.id, W, H)
    doc.durations = [int(f.duration_ms) for f in spec.frames]
    ref = canvas.Layer("reference", W, H, frames=0)
    ref.cels = [bytearray(t) for t in traces]
    ref.reference, ref.opacity = True, 0.35
    drawing = canvas.Layer("drawing", W, H, frames=n)
    drawing.cels[0] = imaging.to_bytes(base)
    doc.layers = [ref, drawing]
    doc.active = 1
    from ..artist import palette_args
    from canvas import parse_hex
    pa = palette_args(ch["palette"])
    doc.palette = {k: parse_hex(v) for k, v in pa["set"].items()}
    agent_dir = ctx.path("agent")
    os.makedirs(agent_dir, exist_ok=True)
    start = os.path.join(agent_dir, "start_project")
    projectio.save_project(doc, start)

    if reduce_mode(ctx):
        # the frames ARE the pose sheet's reductions (no drawing, docs/FACE_ROOT_CAUSE.md). A note from the review
        # (gate R) cannot change them here: it goes to the pose sheet, which is redone once per new note
        drawing.cels = [bytearray(t) for t in traces]
        ctx.save_project("project", doc)
        notes = ctx.notes()
        handled = int(ctx.asset.state.inputs.get("pose_notes_handled", 0))
        if len(notes) > handled:
            ctx.asset.state.inputs["pose_notes_handled"] = len(notes)
            ctx.asset.state.inputs.setdefault("notes", {}).setdefault("anim_poses", []).extend(notes[handled:])
            ctx.info["redo_from"] = "anim_poses"
            ctx.info["proportions"] = "review note sent to the pose sheet"
        ctx.info["reason"] = "[finish] mode = reduce: frames are the pose sheet's reductions (no drawing)"
        _finish(ctx, doc, loop=spec.loop)
        return

    from ..models import StubLLM
    if isinstance(ctx.backend.llm, StubLLM):
        # a stub model cannot draw: each frame becomes its trace so the pipeline runs end to end
        drawing.cels = [bytearray(t) for t in traces]
        ctx.save_project("project", doc)
        ctx.info["dry_run"] = "frames = traces (the artist agent needs a real model)"
        _finish(ctx, doc, loop=spec.loop)
        return

    from ..agent_session import AgentSession
    from ..config import load_engine
    from ..mcp_client import MCPClient
    acfg = ctx.cfg.get("animation", {})
    engine = load_engine(ctx.cfg)
    mcp = MCPClient(os.path.join(engine, "server.py"), env={"PIXELLINER_MCP_OUT": agent_dir})
    from ..live import LivePreview
    live = LivePreview(ctx.asset.dir, "anim_draw", frames=n)
    sess = None
    try:
        txt, _, err = mcp.call("load_project", {"path": start, "name": ctx.asset.id})
        if err:
            raise RuntimeError(txt)
        mcp.call("layer", {"action": "select", "layer": "drawing"})
        client = getattr(ctx.backend.llm, "client", None)
        sess = AgentSession(ctx.project, ctx.asset.id, mcp, client, ctx.cfg["models"].get("artist"), agent_dir,
                            effort=acfg.get("effort", "medium"),
                            allowed=("draw", "view", "compare_reference", "lint", "undo", "redo"),
                            max_usd=float(acfg.get("max_usd", 4.0)), log=ctx.log,
                            on_round=lambda se, ph: live.publish(mcp, ph, se.turns, frame=int(ph[5:])))
        model_sheet = ctx.save_image(None, "model_sheet.png", imaging.zoom(imaging.load_rgba(ch["png"]), 6))
        pal_txt, _, _ = mcp.call("palette", {})
        stops = []
        turns = int(acfg.get("turns_per_frame", 14))
        # frames finished by an earlier attempt that stopped at a spending limit are kept (finding F06)
        ckpt = FrameCheckpoint(ctx, n)
        first = ckpt.resume(mcp)
        if first:
            ctx.log(f"[anim_draw] resuming at frame {first + 1} of {n}: frames 1-{first} were finished before")
        for k, fr in enumerate(spec.frames):
            if k < first:
                continue
            if k > 0:  # start this frame from the previous frame's finished drawing
                mcp.call("draw", {"frame": k, "layer": "drawing",
                                  "ops": [{"op": "transform", "from_frame": k - 1, "copy": True}]})
            mcp.call("frame", {"action": "select", "frame": k})
            _, cur, _ = mcp.call("view", {"mode": "image", "frame": k, "onion": k > 0})
            _, cmp_img, _ = mcp.call("compare_reference", {"frame": k})
            fix = (" The user reviewed an earlier version and asked: " + "; ".join(ctx.notes())) if ctx.notes() else ""
            content = [{"type": "input_text", "text": f"Frame {k + 1} of {n}.{fix} Images: 1) the model sheet (approved "
                                                      f"sprite), 2) this frame's pose sketch, 3) the canvas for this "
                                                      f"frame{' with the previous frame ghosted' if k else ''}, "
                                                      f"4) drawing vs trace. Canvas {pal_txt}"},
                       sess.image_item(model_sheet), sess.image_item(ctx.input("anim_poses", f"figure{k}"))]
            content += [sess.image_item(p) for p in sess.save_imgs(cur + cmp_img, f"f{k}")]
            instr = prompts.ANIM_FRAME.format(name=ch["spec"].name, k1=k + 1, n=n, anim=spec.name, pose=fr.pose,
                                              start="the approved sprite" if k == 0 else f"frame {k} (the previous frame)",
                                              k=k, constant=", ".join(spec.constant) or "the design", turns=turns)
            live.publish(mcp, f"frame{k}", sess.turns, frame=k)  # the frame as it starts (from the previous one)
            stop, _ = sess.converse(f"frame{k}", instr, content, turns, fresh=True)
            stops.append(stop)
            if stop == "cancelled":  # the user cancelled: keep finished frames; Continue resumes at this frame
                ckpt.save(mcp, k)
                from ..cancel import Cancelled
                raise Cancelled(f"cancelled at frame {k + 1} of {n}")
            if stop.startswith("stopped") or sess.spent >= sess.max_usd:
                # a spending limit stopped this frame: keep the finished frames and stop honestly, instead of
                # copying frames forward and presenting an incomplete animation for review
                ckpt.save(mcp, k)
                from ..models import BudgetExceeded
                why = stop[len("stopped: "):] if stop.startswith("stopped") else f"the ${sess.max_usd:.2f} drawing limit for one animation was reached"
                raise BudgetExceeded(f"stopped at frame {k + 1} of {n}: {why}. Frames 1-{k} are kept; raise the limit "
                                     "in Settings and choose Try again to draw the rest")
            ckpt.save(mcp, k + 1)
        mcp.call("save_project", {"path": os.path.join(agent_dir, "project")})
        doc = projectio.load_project(os.path.join(agent_dir, "project"))
        ctx.save_project("project", doc)
        ctx.info.update(usd=round(sess.spent, 4), turns=sess.turns, stops=stops)
        _finish(ctx, doc, loop=spec.loop)
        ckpt.clear()
    finally:
        if sess:
            sess.close()
        mcp.close()
        live.clear()


class FrameCheckpoint:
    """Frames finished so far, kept per asset (not per version folder), so a rerun after a spending stop resumes
    instead of redrawing them. Valid only for the same plan, pose sheet and traces it was made from."""

    def __init__(self, ctx, n):
        self.dir = os.path.join(ctx.asset.dir, "checkpoints", "anim_draw")
        self.made_from = {s: (ctx.asset.state.steps[s].info or {}).get("dir") for s in ("anim_plan", "anim_poses", "anim_traces")
                          if s in ctx.asset.state.steps}
        self.n = n

    def _meta(self):
        try:
            with open(os.path.join(self.dir, "checkpoint.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def resume(self, mcp):
        """Load the saved frames into the canvas and return how many are finished (0 if none or outdated)."""
        m = self._meta()
        if not m or m.get("made_from") != self.made_from or m.get("frames") != self.n or not m.get("done"):
            return 0
        txt, _, err = mcp.call("load_project", {"path": os.path.join(self.dir, "project")})
        if err:
            return 0
        mcp.call("layer", {"action": "select", "layer": "drawing"})
        return int(m["done"])

    def save(self, mcp, done):
        os.makedirs(self.dir, exist_ok=True)
        mcp.call("save_project", {"path": os.path.join(self.dir, "project")})
        with open(os.path.join(self.dir, "checkpoint.json"), "w", encoding="utf-8") as f:
            json.dump({"done": done, "frames": self.n, "made_from": self.made_from}, f)

    def clear(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def _finish(ctx, doc, loop=True):
    frames = [doc.composite(frame=f) for f in range(doc.nframes)]
    lint = ctx.art.lint_frames(doc.w, doc.h, frames)
    ctx.save_json("lint", "frames_lint.json", lint)
    strip = imaging.contact_sheet([imaging.from_bytes(doc.w, doc.h, d) for d in frames],
                                  [f"#{i + 1}" for i in range(doc.nframes)], 4)
    ctx.save_image("strip", "frames_strip.png", strip)
    for i, d in enumerate(frames):  # one PNG per frame, for the app's frame row
        ctx.save_image(f"frame{i}", f"frame_{i:02d}.png", imaging.from_bytes(doc.w, doc.h, d))
    from gif import encode_gif
    # one playback rule for every GIF (preview, 1x, 4x export): loop forever, or play once (finding F11)
    loop_arg = 0 if loop else None
    data, _ = encode_gif(doc.w * 4, doc.h * 4, [imaging.to_bytes(imaging.zoom(imaging.from_bytes(doc.w, doc.h, d), 4))
                                               for d in frames], doc.durations, loop=loop_arg)
    with open(ctx.path("preview.gif"), "wb") as f:
        f.write(data)
    ctx.outputs["gif_x4"] = ctx.path("preview.gif")
    data1, _ = encode_gif(doc.w, doc.h, frames, doc.durations, loop=loop_arg)  # 1x: the app zooms it by whole numbers
    with open(ctx.path("preview_1x.gif"), "wb") as f:
        f.write(data1)
    ctx.outputs["gif"] = ctx.path("preview_1x.gif")
    ctx.info["frame_lint"] = {k: v for k, v in lint["metrics"].items() if k != "areas"}
    ctx.info["frame_issues"] = [i["message"] for i in lint["issues"]]


def review(ctx):
    ctx.info["suggested"] = None


def export(ctx):
    doc = ctx.load_project("anim_draw")
    spec = load_plan(ctx)
    ch = character(ctx)
    name = ctx.asset.id
    from gif import encode_gif
    from png import encode_png
    frames = [doc.composite(frame=f) for f in range(doc.nframes)]
    data, _ = encode_gif(doc.w, doc.h, frames, doc.durations, loop=0 if spec.loop else None)
    with open(ctx.path(f"{name}.gif"), "wb") as f:
        f.write(data)
    ctx.outputs["gif"] = ctx.path(f"{name}.gif")
    sheet = bytearray(doc.w * doc.nframes * doc.h * 4)
    SW = doc.w * doc.nframes
    for i, d in enumerate(frames):
        for y in range(doc.h):
            sheet[(y * SW + i * doc.w) * 4:(y * SW + (i + 1) * doc.w) * 4] = d[y * doc.w * 4:(y + 1) * doc.w * 4]
    with open(ctx.path(f"{name}_sheet.png"), "wb") as f:
        f.write(encode_png(SW, doc.h, sheet))
    ctx.outputs["sheet"] = ctx.path(f"{name}_sheet.png")
    fdir = ctx.path("frames")
    os.makedirs(fdir, exist_ok=True)
    for i, d in enumerate(frames):
        imaging.from_bytes(doc.w, doc.h, d).save(os.path.join(fdir, f"{name}_{i:02d}.png"))
    ctx.outputs["frames"] = fdir
    ctx.save_project("project", doc)
    pivot = [doc.w // 2, doc.h - int(ctx.style["canvas_margin"]) - 1]
    ctx.save_json("meta", "meta.json", {
        "name": spec.name, "character": ch["asset"].id, "request": ctx.asset.state.inputs.get("prompt"),
        "size": [doc.w, doc.h], "frames": [{"duration_ms": d, "pose": f.pose} for d, f in zip(doc.durations, spec.frames)],
        "loop": spec.loop, "pivot": pivot, "sheet": {"columns": doc.nframes, "rows": 1, "frame_size": [doc.w, doc.h]}})
    shutil.copyfile(ctx.input("anim_draw", "gif_x4"), ctx.path(f"{name}_x4.gif"))
    ctx.outputs["gif_x4"] = ctx.path(f"{name}_x4.gif")
