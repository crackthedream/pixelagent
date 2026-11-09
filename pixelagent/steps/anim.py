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
    notes = ctx.notes()
    if notes:
        prompt += "\nChanges requested: " + "; ".join(notes)
    with open(ctx.path("pose_prompt.txt"), "w", encoding="utf-8") as f:
        f.write(prompt)
    sprite_ref = ctx.save_image(None, "sprite_ref.png", imaging.zoom(imaging.load_rgba(ch["png"]), 6))
    size = ctx.cfg.get("animation", {}).get("sheet_size", "1536x1024")

    def stub(k, out_dir):  # dry run: the concept repeated with a little bob and lean per frame
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

    old = ctx.cfg["images"].get("draft_size")
    ctx.cfg["images"]["draft_size"] = size
    try:
        out = ctx.backend.images.generate("image_final", prompt, 1, ctx.path("generated"), [ch["concept"], sprite_ref],
                                          stub, quality=ctx.cfg.get("animation", {}).get("sheet_quality", "medium"))
    finally:
        ctx.cfg["images"]["draft_size"] = old
    sheet = imaging.load_rgba(out[0])
    parts = imaging.split_sheet(sheet, n)
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
    ol = 0 if ch["spec"].outline == "none" else 1
    body_h = (sb[3] - sb[1]) - 2 * ol                 # the approved sprite's height without its outline
    heights = sorted(b[3] - b[1] for b in boxes)
    s = heights[len(heights) // 2] / float(body_h)    # one scale for all frames: median figure = sprite height
    ground = max(b[3] for b in boxes)                  # common ground line of the sheet
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
