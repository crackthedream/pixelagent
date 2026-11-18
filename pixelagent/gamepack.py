"""A character's game pack: everything a game engine needs, from the finished character, its animations and its
expressions, in one folder (and a zip of it).

  <name>.png        one atlas of uniform cells: every frame, the standing sprite and each face, placed so the feet
                    (the pivot) land on the same point of every cell -- one origin for everything, and Unity can slice
                    it with "Grid By Cell Size"
  <name>.json       Aseprite / TexturePacker JSON (array): frame rectangles, durations, frameTags per animation; read
                    by Phaser, Defold, LibGDX and most importers
  <name>.tres       a Godot 4 SpriteFrames resource: every animation with its frame timing and loop flag
  README.txt        cell size, pivot, animation list and how to import

Free: no model calls."""
import json
import math
import os
import shutil
import zipfile

from PIL import Image

from . import expressions, imaging
from .names import safe_filename

GODOT_FPS = 100.0    # a Godot animation's speed; each frame's duration is its milliseconds / 10 (exact timing)


def _latest(asset, step):
    rec = asset.state.steps.get(step)
    return rec if rec and rec.status == "done" else None


def _clips(proj, cid):
    """[(name, [(image, duration_ms)], loop, pivot)] of the character: its standing sprite, its expressions and each
    finished animation."""
    a = proj.asset(cid)
    exp = _latest(a, "export")
    if not exp:
        raise ValueError("the character is not finished yet")
    sprite = imaging.load_rgba(a.path(exp.outputs["png"]))
    with open(a.path(exp.outputs["meta"]), encoding="utf-8") as f:
        meta = json.load(f)
    pivot = tuple(meta.get("pivot") or (sprite.width // 2, sprite.height - 1))
    clips = [("stand", [(sprite, 1000)], False, pivot)]
    ex = expressions.load_current(a)
    for n, e in ex.get("expressions", {}).items():
        p = os.path.join(a.dir, "expressions", e["png"])
        if os.path.exists(p):
            clips.append((f"face_{n}", [(imaging.load_rgba(p), 1000)], False, pivot))
    used = {c[0] for c in clips}
    for aid in proj.asset_ids():
        b = proj.asset(aid)
        if b.state.inputs.get("kind") != "animation" or b.state.inputs.get("character") != cid:
            continue
        rec = _latest(b, "anim_export")
        if not rec:
            continue
        with open(b.path(rec.outputs["meta"]), encoding="utf-8") as f:
            am = json.load(f)
        files = sorted(os.listdir(b.path(rec.outputs["frames"])))
        frames = [(imaging.load_rgba(os.path.join(b.path(rec.outputs["frames"]), fn)), int(fr.get("duration_ms", 100)))
                  for fn, fr in zip(files, am["frames"])]
        name = safe_filename(b.state.inputs.get("anim") or aid[len(cid) + 1:] or aid, "anim").lower().replace(" ", "_")
        base, k = name, 2
        while name in used:
            name, k = f"{base}_{k}", k + 1
        used.add(name)
        clips.append((name, frames, bool(am.get("loop", True)), tuple(am.get("pivot") or (0, 0))))
    return a, clips


def build(proj, cid, out_dir=None):
    """Write the game pack of character `cid`; returns (folder, zip path, summary)."""
    a, clips = _clips(proj, cid)
    name = safe_filename(os.path.splitext(os.path.basename(a.state.steps["export"].outputs["png"]))[0], cid)
    out_dir = out_dir or os.path.join(a.dir, "game_pack")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    # one cell for everything: as far left/up of the pivot as the furthest image reaches, and right/down likewise
    left = up = right = down = 0
    for _, frames, _, (px, py) in clips:
        for im, _ in frames:
            left, up = max(left, px), max(up, py)
            right, down = max(right, im.width - px), max(down, im.height - py)
    cw, ch = left + right, up + down
    n = sum(len(f) for _, f, _, _ in clips)
    cols = max(1, min(n, math.ceil(math.sqrt(n * ch / max(1, cw)))))
    rows = math.ceil(n / cols)
    atlas = Image.new("RGBA", (cols * cw, rows * ch), (0, 0, 0, 0))
    frames_json, tags, k = [], [], 0
    for clip, frames, loop, (px, py) in clips:
        first = k
        for i, (im, ms) in enumerate(frames):
            x, y = (k % cols) * cw, (k // cols) * ch
            atlas.alpha_composite(im, (x + left - px, y + up - py))
            frames_json.append({"filename": f"{clip} {i}", "frame": {"x": x, "y": y, "w": cw, "h": ch},
                                "rotated": False, "trimmed": False,
                                "spriteSourceSize": {"x": 0, "y": 0, "w": cw, "h": ch},
                                "sourceSize": {"w": cw, "h": ch}, "duration": ms})
            k += 1
        tags.append({"name": clip, "from": first, "to": k - 1, "direction": "forward", "loop": loop})
    png = f"{name}.png"
    atlas.save(os.path.join(out_dir, png))
    pivot = {"x": left, "y": up}
    with open(os.path.join(out_dir, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump({"frames": frames_json,
                   "meta": {"app": "PixelAgent", "version": "1", "image": png, "format": "RGBA8888",
                            "size": {"w": atlas.width, "h": atlas.height}, "scale": "1", "frameTags": tags,
                            "pivot": pivot, "cell": {"w": cw, "h": ch, "columns": cols, "rows": rows}}},
                  f, indent=1)
    with open(os.path.join(out_dir, f"{name}.tres"), "w", encoding="utf-8") as f:
        f.write(_godot(png, name, clips, frames_json))
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write(_readme(name, cw, ch, cols, rows, pivot, tags))
    zpath = os.path.join(os.path.dirname(out_dir), f"{name}_game_pack.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(out_dir)):
            z.write(os.path.join(out_dir, fn), f"{name}/{fn}")
    return out_dir, zpath, {"cell": [cw, ch], "pivot": [left, up], "frames": n,
                            "animations": [t["name"] for t in tags]}


def _godot(png, name, clips, frames_json):
    """A Godot 4 SpriteFrames resource; the atlas is expected next to it under res://<name>/ (README.txt)."""
    lines = [f'[gd_resource type="SpriteFrames" load_steps={len(frames_json) + 2} format=3]', "",
             f'[ext_resource type="Texture2D" path="res://{name}/{png}" id="1_atlas"]', ""]
    for i, fr in enumerate(frames_json):
        r = fr["frame"]
        lines += [f'[sub_resource type="AtlasTexture" id="AtlasTexture_{i}"]', 'atlas = ExtResource("1_atlas")',
                  f'region = Rect2({r["x"]}, {r["y"]}, {r["w"]}, {r["h"]})', ""]
    anims, k = [], 0
    for clip, frames, loop, _ in clips:
        fs = []
        for _, ms in frames:
            fs.append(f'{{\n"duration": {ms / (1000.0 / GODOT_FPS):.1f},\n"texture": SubResource("AtlasTexture_{k}")\n}}')
            k += 1
        anims.append(f'{{\n"frames": [{", ".join(fs)}],\n"loop": {"true" if loop else "false"},\n'
                     f'"name": &"{clip}",\n"speed": {GODOT_FPS:.1f}\n}}')
    lines += ["[resource]", f"animations = [{', '.join(anims)}]", ""]
    return "\n".join(lines)


def _readme(name, cw, ch, cols, rows, pivot, tags):
    anims = "\n".join(f"  {t['name']:<16} frames {t['from']}-{t['to']}{'  (loops)' if t['loop'] else ''}"
                      for t in tags)
    return f"""{name}: game pack made by PixelAgent

{name}.png   atlas, {cols} x {rows} cells of {cw} x {ch} px (pixel art at 1x: scale it by whole numbers, nearest
             neighbour / no filtering)
{name}.json  Aseprite / TexturePacker JSON (array): frames, durations in ms, frameTags = animations
{name}.tres  Godot 4 SpriteFrames

Pivot (the feet) in every cell: x {pivot['x']}, y {pivot['y']} from the cell's top-left corner.

Animations (cells counted left to right, top to bottom; face_* are the same sprite with another face):
{anims}

Godot 4: copy this folder into your project as res://{name}/ , set the atlas's import filter to Nearest, then use
{name}.tres as an AnimatedSprite2D's Sprite Frames. Offset the sprite by (-{pivot['x']}, -{pivot['y']}) or turn off
"centered" and move it so the feet sit on the node's position.
Unity: import {name}.png as Sprite (2D), Multiple, Filter Point, Compression None; Sprite Editor > Slice > Grid By
Cell Size {cw} x {ch}, custom pivot ({pivot['x'] / cw:.3f}, {1 - pivot['y'] / ch:.3f}).
Phaser, Defold, LibGDX and others: load {name}.json with {name}.png as an Aseprite / TexturePacker atlas.
"""
