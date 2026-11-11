"""A project's item sheet: every finished object (asset_type "prop") in one atlas, for a game's items and props.

  items.png         one atlas of uniform square cells (the largest item plus a pixel each side, rounded up to a
                    multiple of 8), each item centred in its cell at 1x
  items.json        TexturePacker JSON (hash): per item its trimmed rectangle in the atlas ("frame"), where that sits
                    in its cell ("spriteSourceSize", "sourceSize": the cell) and a bottom-centre pivot; read by Phaser,
                    Defold, LibGDX and most importers
  godot/<name>.tres a Godot 4 AtlasTexture per item (its trimmed rectangle)
  items/<name>.png  each item on its own, 1x
  README.txt        cell size, item list and how to import

Free: no model calls."""
import json
import math
import os
import shutil
import zipfile

from PIL import Image

from . import imaging
from .names import safe_filename


def _items(proj):
    """[(name, image cropped to the item)] of the project's finished objects, in creation order."""
    out, used = [], set()
    for aid in proj.asset_ids():
        a = proj.asset(aid)
        if a.state.inputs.get("asset_type") != "prop" or a.state.status != "complete":
            continue
        exp = a.state.steps.get("export")
        if not exp or "png" not in exp.outputs:
            continue
        im = imaging.load_rgba(a.path(exp.outputs["png"]))
        if not im.getbbox():
            continue
        base = safe_filename(os.path.splitext(os.path.basename(exp.outputs["png"]))[0], aid).lower()
        name, k = base, 2
        while name in used:
            name, k = f"{base}_{k}", k + 1
        used.add(name)
        out.append((name, im.crop(im.getbbox())))
    return out


def build(proj, out_dir=None):
    """Write the item sheet of the project's finished objects; returns (folder, zip path, summary)."""
    items = _items(proj)
    if not items:
        raise ValueError("there are no finished objects in this project yet")
    out_dir = out_dir or os.path.join(proj.root, "item_sheet")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "items"))
    os.makedirs(os.path.join(out_dir, "godot"))
    side = max(max(im.size) for _, im in items) + 2
    cell = int(math.ceil(side / 8.0) * 8)
    cols = int(math.ceil(math.sqrt(len(items))))
    rows = int(math.ceil(len(items) / cols))
    atlas = Image.new("RGBA", (cols * cell, rows * cell), (0, 0, 0, 0))
    frames = {}
    for k, (name, im) in enumerate(items):
        cx, cy = (k % cols) * cell, (k // cols) * cell
        ox, oy = (cell - im.width) // 2, (cell - im.height) // 2
        atlas.alpha_composite(im, (cx + ox, cy + oy))
        im.save(os.path.join(out_dir, "items", f"{name}.png"))
        frames[name] = {"frame": {"x": cx + ox, "y": cy + oy, "w": im.width, "h": im.height}, "rotated": False,
                        "trimmed": True, "spriteSourceSize": {"x": ox, "y": oy, "w": im.width, "h": im.height},
                        "sourceSize": {"w": cell, "h": cell}, "pivot": {"x": 0.5, "y": 1.0},
                        "cell": {"x": cx, "y": cy, "w": cell, "h": cell}}
        with open(os.path.join(out_dir, "godot", f"{name}.tres"), "w", encoding="utf-8") as f:
            f.write('[gd_resource type="AtlasTexture" load_steps=2 format=3]\n\n'
                    '[ext_resource type="Texture2D" path="res://item_sheet/items.png" id="1_atlas"]\n\n'
                    f'[resource]\natlas = ExtResource("1_atlas")\n'
                    f'region = Rect2({cx + ox}, {cy + oy}, {im.width}, {im.height})\n')
    atlas.save(os.path.join(out_dir, "items.png"))
    with open(os.path.join(out_dir, "items.json"), "w", encoding="utf-8") as f:
        json.dump({"frames": frames, "meta": {"app": "PixelAgent", "version": "1", "image": "items.png",
                                              "format": "RGBA8888", "size": {"w": atlas.width, "h": atlas.height},
                                              "scale": "1", "cell": {"w": cell, "h": cell, "columns": cols,
                                                                     "rows": rows}}}, f, indent=1)
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write(_readme(items, cell, cols, rows))
    zpath = os.path.join(proj.root, "item_sheet.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(out_dir):
            for fn in sorted(files):
                p = os.path.join(root, fn)
                z.write(p, os.path.join("item_sheet", os.path.relpath(p, out_dir)).replace("\\", "/"))
    return out_dir, zpath, {"items": [n for n, _ in items], "cell": cell, "columns": cols, "rows": rows}


def _readme(items, cell, cols, rows):
    lst = "\n".join(f"  {k:>3}  {n:<28} {im.width} x {im.height} px" for k, (n, im) in enumerate(items))
    return f"""Item sheet made by PixelAgent

items.png    atlas, {cols} x {rows} cells of {cell} x {cell} px, each item centred in its cell (pixel art at 1x:
             scale it by whole numbers, nearest neighbour / no filtering)
items.json   TexturePacker JSON (hash): per item its trimmed rectangle ("frame"), its place in its cell
             ("spriteSourceSize", "sourceSize") and a bottom-centre pivot
godot/       a Godot 4 AtlasTexture per item
items/       each item on its own, 1x

Items (cells counted left to right, top to bottom):
{lst}

Godot 4: copy this folder into your project as res://item_sheet/ , set items.png's import filter to Nearest, then
use godot/<item>.tres wherever a Texture2D is wanted (a Sprite2D, a TextureRect for an inventory slot).
Unity: import items.png as Sprite (2D), Multiple, Filter Point, Compression None; Sprite Editor > Slice > Grid By
Cell Size {cell} x {cell} (inventory icons), or slice by the "frame" rectangles in items.json for tight sprites.
Phaser, Defold, LibGDX and others: load items.json with items.png as a TexturePacker atlas.
"""
