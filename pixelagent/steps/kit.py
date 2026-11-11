"""Walkable kits: the tiles and decorations a level is built from (docs/BACKGROUNDS_DESIGN.md, section 3B).

  plan    the director turns the theme (and the matching scene, if one is given) into the ground's surface and fill,
          a one-way platform, 6-10 decorations, a palette and light
  sheet   the image model paints a TERRAIN CHUNK over a layout image -- a grey 6x4-tile ground block and a 4x1-tile
          platform at exact positions, as tile artists draw a chunk and then slice it -- and the decorations in the
          empty right side. Neighbouring tiles come from one painting, so they fit together.
  read    the sheet is read as pixel art (4 image pixels per pixel; alpha binary, one palette for the whole kit,
          graded like the characters), the chunk is sliced into 32-px tiles by role (corners, edges, top and fill
          variants, platform ends), the repeating ones made to wrap onto themselves (the scene's ordered-dither
          blend), and the decorations are cut out
  review  gate K: a small test level built from the tiles, in front of the matching scene, with the project's
          character standing on it
  export  tileset PNG, Tiled tileset (.tsx) and test level (.tmx), decorations (PNGs, atlas, Tiled image collection),
          a Godot 4 TileSet (.tres, with collision and one-way platforms), meta.json and a README (Unity, LDtk)"""
import json
import os
from typing import List

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from .. import prompts
from ..names import safe_filename
from .build import GRADES
from .scene import BAYER4, to_pixel

T = 32                     # tile size in pixels (the owner's choice, 2026-11-11)
B = 4                      # image pixels per sprite pixel on the 1536x1024 sheet
SHEET = (1536 // B, 1024 // B)          # the sheet in sprite pixels: 384 x 256
GROUND = (16, 112, 6, 4)   # x, y (sprite px), width, height (tiles)
PLATFORM = (16, 48, 4, 1)
DECO_X = 232               # decorations go right of this


class Decoration(BaseModel):
    name: str
    description: str
    tiles_wide: int = Field(default=1, description="its width in 32-px tiles (1-3)")
    tiles_high: int = Field(default=1, description="its height in 32-px tiles (1-3)")


class KitPlan(BaseModel):
    name: str
    theme: str = Field(description="the place in a few words")
    surface: str = Field(description="what covers the ground's top edge: grass, snow, cobbles...")
    fill: str = Field(description="what the ground is made of inside: soil, rock, brick...")
    platform: str = Field(description="what a floating one-way platform is made of")
    palette: str
    light: str
    decorations: List[Decoration]


def load_plan(ctx):
    with open(ctx.input("kit_plan", "plan"), encoding="utf-8") as f:
        return KitPlan.model_validate_json(f.read())


def _scene(ctx):
    """The scene this kit belongs with (inputs 'scene'), as (plan dict, composite path) or (None, None)."""
    sid = ctx.asset.state.inputs.get("scene")
    if not sid or sid not in ctx.project.asset_ids():
        return None, None
    a = ctx.project.asset(sid)
    try:
        with open(a.output("scene_plan", "plan"), encoding="utf-8") as f:
            plan = json.load(f)
    except (KeyError, OSError, ValueError):
        return None, None
    comp = None
    for step, key in (("scene_export", "png"), ("scene_pixel", "composite")):
        try:
            comp = a.output(step, key)
            break
        except KeyError:
            continue
    return plan, comp


# ------------------------------------------------------------------ plan

def plan(ctx):
    inp = ctx.asset.state.inputs
    request = inp.get("prompt") or "a forest"
    sc, _ = _scene(ctx)
    text = request + (f"\nIt belongs with this scene: {sc.get('summary')}. Its palette: {sc.get('palette')}. "
                      f"Light: {sc.get('light')}." if sc else "") + "".join(f"\n{n}" for n in ctx.notes())

    def stub():
        return KitPlan(name=inp.get("name") or ctx.asset.id, theme=request, surface="grass", fill="earth with stones",
                       platform="a wooden plank", palette=(sc or {}).get("palette", "muted greens and browns"),
                       light=(sc or {}).get("light", "soft light from the left"),
                       decorations=[Decoration(name=n, description=n) for n in
                                    ("bush", "rock", "flowers", "signpost", "mushrooms", "fern")])
    spec = ctx.backend.llm.structured("director", prompts.KIT_PLAN, text, [], KitPlan, stub)
    if inp.get("name"):
        spec.name = inp["name"]
    spec.decorations = spec.decorations[:10]
    for d in spec.decorations:
        d.tiles_wide, d.tiles_high = max(1, min(3, d.tiles_wide)), max(1, min(3, d.tiles_high))
    ctx.save_json("plan", "plan.json", spec)
    ctx.info.update(name=spec.name, decorations=[d.name for d in spec.decorations],
                    scene=inp.get("scene") if sc else None)


# ------------------------------------------------------------------ sheet

def layout():
    """The layout the image model paints over: grey shapes at the chunk's exact place, transparent elsewhere."""
    W, H = SHEET
    im = Image.new("RGBA", (W * B, H * B), (0, 0, 0, 0))
    for x, y, w, h in (GROUND, PLATFORM):
        im.paste((138, 138, 138, 255), (x * B, y * B, (x + w * T) * B, (y + h * T) * B))
    return im


def _stub_sheet(lay):
    def stub(k, out_dir):
        im = lay.copy()
        a = np.asarray(im).copy()
        grey = (a[..., 3] > 0)
        rng = np.random.default_rng(1)
        a[grey, :3] = np.array([120, 84, 52]) + rng.integers(-12, 12, (int(grey.sum()), 1))
        gy = GROUND[1] * B
        a[gy:gy + 6 * B, GROUND[0] * B:(GROUND[0] + GROUND[2] * T) * B, :3] = (90, 150, 70)
        for i in range(6):                                 # decorations: blobs on the right
            x, y = (DECO_X + 8 + (i % 3) * 48) * B, (24 + (i // 3) * 80) * B
            a[y:y + 28 * B, x:x + 28 * B] = (70 + 20 * i, 130, 90, 255)
        p = os.path.join(out_dir, "gen00.png")
        Image.fromarray(a).save(p)
        return [p]
    return stub


def sheet(ctx):
    sp = load_plan(ctx)
    lay = layout()
    lpath = ctx.save_image("layout", "layout.png", lay)
    decos = "\n".join(f"- {d.name}: {d.description} (about {d.tiles_wide * T} x {d.tiles_high * T} pixels)"
                      for d in sp.decorations)
    prompt = prompts.KIT_SHEET.format(theme=sp.theme, surface=sp.surface, fill=sp.fill, platform=sp.platform,
                                      palette=sp.palette, light=sp.light, decorations=decos)
    notes = ctx.notes()
    if notes:
        prompt += "\nChanges requested: " + "; ".join(notes)
    with open(ctx.path("sheet_prompt.txt"), "w", encoding="utf-8") as f:
        f.write(prompt)
    icfg = ctx.cfg.setdefault("images", {})
    old = {k: icfg.get(k) for k in ("draft_size", "background")}
    icfg.update(draft_size=f"{SHEET[0] * B}x{SHEET[1] * B}", background="transparent")
    try:
        out = ctx.backend.images.generate("image_final", prompt, 1, ctx.path("generated"), refs=[lpath],
                                          stub=_stub_sheet(lay), quality=ctx.cfg.get("kit", {}).get("quality", "medium"))
    finally:
        for k, v in old.items():
            if v is None:
                icfg.pop(k, None)
            else:
                icfg[k] = v
    im = Image.open(out[0]).convert("RGBA")
    if im.size != lay.size:
        im = im.resize(lay.size, Image.LANCZOS)
    ctx.save_image("sheet", "sheet.png", im)


# ------------------------------------------------------------------ read

def _wrap_h(strip, x0, w, blend):
    """Columns [x0, x0+w) of an RGBA array, their first `blend` columns dithered in from what follows x0+w, so the
    piece wraps onto itself sideways (scene.wrap_layer's idea on a tile)."""
    H = strip.shape[0]
    out = strip[:, x0:x0 + w].copy()
    b = min(blend, strip.shape[1] - (x0 + w))
    for c in range(b):
        t = (c + 0.5) / b
        own = BAYER4[np.arange(H) % 4, c % 4] < t
        out[~own, c] = strip[~own, x0 + w + c]
    return out


def _wrap_v(strip, y0, h, blend):
    return np.ascontiguousarray(np.transpose(_wrap_h(np.ascontiguousarray(np.transpose(strip, (1, 0, 2))), y0, h, blend),
                                             (1, 0, 2)))


def _roles():
    """The chunk's tiles by role, as (role, column, row) in the ground block (6x4) and the platform (4x1)."""
    gw, gh = GROUND[2], GROUND[3]
    out = []
    for r in range(gh):
        for c in range(gw):
            v = "t" if r == 0 else "b" if r == gh - 1 else "m"
            h = "l" if c == 0 else "r" if c == gw - 1 else "c"
            out.append(("ground", f"{v}{h}{'' if h != 'c' else c}{'' if v != 'm' else r}", c, r))
    for c in range(PLATFORM[2]):
        out.append(("platform", "pl" if c == 0 else "pr" if c == PLATFORM[2] - 1 else f"pc{c}", c, 0))
    return out


def read(ctx):
    sp = load_plan(ctx)
    gname = str(ctx.cfg.get("finish", {}).get("grade", "medium"))
    colours = int(ctx.cfg.get("kit", {}).get("colours", 48))
    px = to_pixel(Image.open(ctx.input("kit_sheet", "sheet")), SHEET, colours, False, GRADES.get(gname))
    a = np.asarray(px).copy()
    ctx.save_image("sheet_pixel", "sheet_pixel.png", px)
    blend = int(ctx.cfg.get("kit", {}).get("tile_blend", 8))
    tiles = {}
    for part, role, c, r in _roles():
        bx, by, bw, bh = GROUND if part == "ground" else PLATFORM
        x, y = bx + c * T, by + r * T
        row = a[y:y + T, bx:bx + bw * T]
        middle = role[1] == "c" if part == "ground" else role.startswith("pc")
        if middle and c < bw - 2:                      # a repeating tile with a repeating neighbour after it: wrap
            tile = _wrap_h(row, c * T, T, blend)
        else:
            tile = row[:, c * T:(c + 1) * T].copy()
        if part == "ground" and role.startswith("m") and r < bh - 2:     # fill: also wrap downwards
            col = a[by:by + bh * T, x:x + T].copy()
            col[r * T:(r + 1) * T] = tile
            tile = _wrap_v(col, r * T, T, blend)
        tiles[role] = tile
    order = [role for _, role, _, _ in _roles()]
    cols = 6
    ts = np.zeros(((len(order) + cols - 1) // cols * T, cols * T, 4), np.uint8)
    for k, role in enumerate(order):
        ts[(k // cols) * T:(k // cols + 1) * T, (k % cols) * T:(k % cols + 1) * T] = tiles[role]
    ctx.save_image("tileset", "tileset.png", Image.fromarray(ts))
    # decorations: the opaque pieces right of the chunk, in reading order
    from .anim import _components
    region = a[:, DECO_X:]
    lab, sizes = _components(region[..., 3] > 0)
    pieces = []
    for k in range(1, len(sizes)):
        if sizes[k] < 24:
            continue
        ys, xs = np.nonzero(lab == k)
        pieces.append((int(ys.min()), int(xs.min()), int(ys.max()) + 1, int(xs.max()) + 1, k))
    pieces.sort(key=lambda p: (p[0] // 48, p[1]))           # rows of about 48 px, then left to right
    names = [d.name for d in sp.decorations]
    decos = []
    for n, (y0, x0, y1, x1, k) in enumerate(pieces):
        piece = region[y0:y1, x0:x1].copy()
        piece[lab[y0:y1, x0:x1] != k] = 0
        name = names[n] if len(pieces) == len(names) else f"decoration_{n + 1}"
        fn = f"deco_{n:02d}_{safe_filename(name, 'decoration').lower()}.png"
        ctx.save_image(f"deco{n}", fn, Image.fromarray(piece))
        decos.append({"name": name, "file": fn, "size": [x1 - x0, y1 - y0]})
    ctx.save_json("tiles", "tiles.json", {"tile": T, "columns": cols, "order": order, "decorations": decos})
    ctx.info.update(tiles=len(order), decorations=len(decos), grade=gname,
                    named=len(pieces) == len(names))


# ------------------------------------------------------------------ test level

LEVEL = [   # 26 x 9 tiles: '.' empty, '#' ground, '=' platform, 'd' a decoration standing on the ground below it
    "..........................",
    "..........................",
    "..........................",
    "...............====.......",
    "..........................",
    "......d........####.......",
    "..#####........####.......",
    "..#####....d.########.d...",
    "########...###############",
]
GROUND_ROW = 6             # where the preview's character stands (the top of the left block)


def level_tiles():
    """The test level as a grid of tile roles (or None), choosing corners, edges, top and fill by neighbours."""
    H, W = len(LEVEL), len(LEVEL[0])
    solid = [[ch == "#" for ch in row] for row in LEVEL]
    grid = [[None] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            ch = LEVEL[y][x]
            if ch == "=":
                left, right = x > 0 and LEVEL[y][x - 1] == "=", x < W - 1 and LEVEL[y][x + 1] == "="
                grid[y][x] = "pl" if not left else "pr" if not right else f"pc{1 + x % 2}"
            elif solid[y][x]:
                up = y > 0 and solid[y - 1][x]
                down = y < H - 1 and solid[y + 1][x]
                lft = x > 0 and solid[y][x - 1]
                rgt = x < W - 1 and solid[y][x + 1]
                v = "t" if not up else ("b" if not down and y < H - 1 else "m")
                h = "l" if not lft else "r" if not rgt else "c"
                if h == "c":
                    col = 1 + (x % 4)
                    grid[y][x] = f"{v}c{col}" + ("" if v != "m" else str(1 + (y % 2)))
                else:
                    grid[y][x] = f"{v}{h}" + ("" if v != "m" else str(1 + (y % 2)))
    return grid


def _level_image(tiles, decos, tile_of):
    grid = level_tiles()
    H, W = len(grid), len(grid[0])
    im = Image.new("RGBA", (W * T, H * T), (0, 0, 0, 0))
    for y in range(H):
        for x in range(W):
            role = grid[y][x]
            if role:
                im.alpha_composite(tile_of(role), (x * T, y * T))
    k = 0
    for y in range(H):
        for x in range(W):
            if LEVEL[y][x] == "d" and decos:
                d = decos[k % len(decos)]
                k += 1
                im.alpha_composite(d, (x * T + T // 2 - d.width // 2, (y + 1) * T - d.height))
    return im


def review(ctx):
    with open(ctx.input("kit_read", "tiles"), encoding="utf-8") as f:
        meta = json.load(f)
    ts = Image.open(ctx.input("kit_read", "tileset")).convert("RGBA")
    cols = meta["columns"]
    index = {role: k for k, role in enumerate(meta["order"])}

    def tile_of(role):
        k = index.get(role)
        if k is None:                                     # e.g. a fill variant the chunk does not have: nearest kind
            alts = [r for r in meta["order"] if r[:2] == role[:2]] or [meta["order"][0]]
            k = index[alts[0]]
        return ts.crop(((k % cols) * T, (k // cols) * T, (k % cols + 1) * T, (k // cols + 1) * T))
    decos = [Image.open(ctx.input("kit_read", f"deco{n}")).convert("RGBA") for n in range(len(meta["decorations"]))]
    lvl = _level_image(ts, decos, tile_of)
    bg = _scene_backdrop(ctx, lvl.width)
    H = max(lvl.height, bg.height if bg else 0, 12 * T)   # room above the level for the scene's sky
    out = Image.new("RGBA", (lvl.width, H), (150, 170, 190, 255))
    if bg is not None:
        out.alpha_composite(bg, (0, H - bg.height))
    top = H - lvl.height                                   # the level sits at the bottom
    out.alpha_composite(lvl, (0, top))
    from .scene import _character
    ch = _character(ctx)
    if ch is not None:
        out.alpha_composite(ch, (3 * T, top + GROUND_ROW * T - ch.height))
    ctx.save_image("level", "test_level.png", out)
    ctx.save_image("level_x2", "test_level_x2.png", out.resize((out.width * 2, out.height * 2), Image.NEAREST))
    ctx.info["suggested"] = None


def _scene_backdrop(ctx, width):
    """The matching scene at `width`: each of its layers repeated at its own repeat width (repeating the flattened
    composite would jump where layers of different widths meet), or the composite if the layers are missing."""
    from .scene import tiled
    sid = ctx.asset.state.inputs.get("scene")
    if not sid or sid not in ctx.project.asset_ids():
        return None
    a = ctx.project.asset(sid)
    rec = a.state.steps.get("scene_pixel")
    keys = sorted((k for k in (rec.outputs if rec else {}) if k.startswith("layer")), key=lambda k: int(k[5:]))
    if keys:
        ims = [Image.open(a.path(rec.outputs[k]) if not os.path.isabs(rec.outputs[k]) else rec.outputs[k])
               .convert("RGBA") for k in keys]
        out = Image.new("RGBA", (width, ims[0].height))
        for im in ims:
            out.alpha_composite(tiled(im, width))
        return out
    _, comp = _scene(ctx)
    return tiled(Image.open(comp).convert("RGBA"), width) if comp else None


# ------------------------------------------------------------------ export

def export(ctx):
    sp = load_plan(ctx)
    name = safe_filename(sp.name, ctx.asset.id)
    with open(ctx.input("kit_read", "tiles"), encoding="utf-8") as f:
        meta = json.load(f)
    ts = Image.open(ctx.input("kit_read", "tileset")).convert("RGBA")
    ctx.save_image("tileset", "tileset.png", ts)
    ddir = ctx.path("decorations")
    os.makedirs(ddir, exist_ok=True)
    decos = []
    for n, d in enumerate(meta["decorations"]):
        im = Image.open(ctx.input("kit_read", f"deco{n}")).convert("RGBA")
        im.save(os.path.join(ddir, d["file"]))
        decos.append({**d, "image": im})
    ctx.outputs["decorations"] = ddir
    # decorations atlas: one row, bottom-aligned
    if decos:
        w = sum(d["image"].width + 2 for d in decos)
        h = max(d["image"].height for d in decos)
        atlas = Image.new("RGBA", (w, h))
        x = 0
        frames = {}
        for d in decos:
            atlas.alpha_composite(d["image"], (x, h - d["image"].height))
            frames[d["name"]] = {"frame": {"x": x, "y": h - d["image"].height, "w": d["image"].width,
                                           "h": d["image"].height}, "pivot": {"x": 0.5, "y": 1.0}}
            x += d["image"].width + 2
        ctx.save_image("decorations_atlas", "decorations.png", atlas)
        with open(ctx.path("decorations.json"), "w", encoding="utf-8") as f:
            json.dump({"frames": frames, "meta": {"app": "PixelAgent", "image": "decorations.png",
                                                  "size": {"w": atlas.width, "h": atlas.height}}}, f, indent=1)
    roles = meta["order"]
    cols = meta["columns"]
    with open(ctx.path("tileset.tsx"), "w", encoding="utf-8") as f:
        f.write(_tsx(name, ts, roles, cols))
    ctx.outputs["tiled_tileset"] = ctx.path("tileset.tsx")
    with open(ctx.path("decorations.tsx"), "w", encoding="utf-8") as f:
        f.write(_deco_tsx(name, decos))
    with open(ctx.path("test_level.tmx"), "w", encoding="utf-8") as f:
        f.write(_tmx(roles, decos))
    ctx.outputs["tiled_level"] = ctx.path("test_level.tmx")
    with open(ctx.path(f"{name}_tileset.tres"), "w", encoding="utf-8") as f:
        f.write(_godot(name, ts, roles, cols))
    ctx.outputs["godot"] = ctx.path(f"{name}_tileset.tres")
    import shutil
    shutil.copyfile(ctx.input("kit_review", "level"), ctx.path("test_level.png"))
    ctx.outputs["level"] = ctx.path("test_level.png")
    ctx.save_json("meta", "meta.json", {"name": sp.name, "theme": sp.theme, "tile": T, "columns": cols,
                                       "tiles": [{"role": r, "id": k, "kind": _kind(r)} for k, r in enumerate(roles)],
                                       "decorations": [{k: v for k, v in d.items() if k != "image"} for d in decos]})
    with open(ctx.path("README.txt"), "w", encoding="utf-8") as f:
        f.write(_readme(name, roles, cols, decos))
    ctx.outputs["readme"] = ctx.path("README.txt")
    ctx.info.update(tiles=len(roles), decorations=len(decos))


def _kind(role):
    return "platform" if role.startswith("p") else "solid"


ROLE_HELP = {"t": "top surface", "m": "fill", "b": "bottom", "l": "left edge", "r": "right edge", "c": "middle"}


def _tsx(name, ts, roles, cols):
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<tileset version="1.10" tiledversion="1.10.2" name="{name}" tilewidth="{T}" tileheight="{T}" '
           f'tilecount="{len(roles)}" columns="{cols}">',
           f' <image source="tileset.png" width="{ts.width}" height="{ts.height}"/>']
    for k, r in enumerate(roles):
        out += [f' <tile id="{k}">', "  <properties>", f'   <property name="role" value="{r}"/>',
                f'   <property name="kind" value="{_kind(r)}"/>', "  </properties>", " </tile>"]
    out.append("</tileset>")
    return "\n".join(out) + "\n"


def _deco_tsx(name, decos):
    mw = max([d["image"].width for d in decos] or [1])
    mh = max([d["image"].height for d in decos] or [1])
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<tileset version="1.10" tiledversion="1.10.2" name="{name} decorations" tilewidth="{mw}" '
           f'tileheight="{mh}" tilecount="{len(decos)}" columns="0">', ' <grid orientation="orthogonal" width="1" height="1"/>']
    for k, d in enumerate(decos):
        out += [f' <tile id="{k}">', f'  <image source="decorations/{d["file"]}" width="{d["image"].width}" '
                f'height="{d["image"].height}"/>', " </tile>"]
    out.append("</tileset>")
    return "\n".join(out) + "\n"


def _tmx(roles, decos):
    grid = level_tiles()
    H, W = len(grid), len(grid[0])
    index = {r: k for k, r in enumerate(roles)}

    def gid(role):
        if role is None:
            return 0
        k = index.get(role)
        if k is None:
            alts = [r for r in roles if r[:2] == role[:2]] or roles[:1]
            k = index[alts[0]]
        return k + 1
    csv = ",\n".join(",".join(str(gid(grid[y][x])) for x in range(W)) for y in range(H))
    first_deco = len(roles) + 1
    objs, k, oid = [], 0, 1
    for y in range(H):
        for x in range(W):
            if LEVEL[y][x] == "d" and decos:
                d = decos[k % len(decos)]
                dk = k % len(decos)
                k += 1
                w, h = d["image"].width, d["image"].height
                objs.append(f'  <object id="{oid}" gid="{first_deco + dk}" x="{x * T + T // 2 - w // 2}" '
                            f'y="{(y + 1) * T}" width="{w}" height="{h}"/>')
                oid += 1
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<map version="1.10" tiledversion="1.10.2" orientation="orthogonal" renderorder="right-down" width="{W}" '
        f'height="{H}" tilewidth="{T}" tileheight="{T}" infinite="0" nextlayerid="3" nextobjectid="{oid}">',
        ' <tileset firstgid="1" source="tileset.tsx"/>',
        f' <tileset firstgid="{first_deco}" source="decorations.tsx"/>',
        f' <layer id="1" name="ground" width="{W}" height="{H}">', '  <data encoding="csv">', csv, "</data>",
        " </layer>", ' <objectgroup id="2" name="decorations">', *objs, " </objectgroup>", "</map>", ""])


def _godot(name, ts, roles, cols):
    h = T // 2
    lines = ['[gd_resource type="TileSet" load_steps=3 format=3]', "",
             f'[ext_resource type="Texture2D" path="res://{name}/tileset.png" id="1_tiles"]', "",
             '[sub_resource type="TileSetAtlasSource" id="TileSetAtlasSource_1"]', 'texture = ExtResource("1_tiles")',
             f"texture_region_size = Vector2i({T}, {T})"]
    for k, r in enumerate(roles):
        c, row = k % cols, k // cols
        lines.append(f"{c}:{row}/0 = 0")
        if _kind(r) == "platform":
            pts = f"-{h}, -{h}, {h}, -{h}, {h}, -{h - 6}, -{h}, -{h - 6}"
            lines += [f"{c}:{row}/0/physics_layer_0/polygon_0/points = PackedVector2Array({pts})",
                      f"{c}:{row}/0/physics_layer_0/polygon_0/one_way = true"]
        else:
            lines.append(f"{c}:{row}/0/physics_layer_0/polygon_0/points = "
                         f"PackedVector2Array(-{h}, -{h}, {h}, -{h}, {h}, {h}, -{h}, {h})")
    lines += ["", "[resource]", f"tile_size = Vector2i({T}, {T})", "physics_layer_0/collision_layer = 1",
              'sources/0 = SubResource("TileSetAtlasSource_1")', ""]
    return "\n".join(lines)


def _readme(name, roles, cols, decos):
    tl = "\n".join(f"  {k:>3}  ({k % cols},{k // cols})  {r:<6} {_kind(r)}" for k, r in enumerate(roles))
    dl = "\n".join(f"  {d['file']:<36} {d['image'].width} x {d['image'].height} px  {d['name']}" for d in decos)
    return f"""{name}: walkable kit made by PixelAgent

Pixel art at 1x (the characters' scale), {T}-px tiles; scale by whole numbers with nearest-neighbour filtering.

tileset.png        the tiles, {cols} per row (index, column/row, role, kind):
{tl}
  Roles: t = top surface, m = fill, b = bottom; l / r = left / right edge, c = middle (numbered variants);
  pl / pc / pr = a one-way platform's left end, middle, right end. Middle tiles wrap onto themselves sideways
  (and fill tiles downwards), so any can sit next to any.
decorations/       each decoration on its own (stand it on the ground by its bottom centre):
{dl}
decorations.png/.json   the decorations in one atlas (TexturePacker-style JSON, bottom-centre pivots)
test_level.png     the approval preview

Tiled: tileset.tsx (roles as tile properties), decorations.tsx (an image collection) and test_level.tmx.
Godot 4: copy this folder into your project as res://{name}/, set tileset.png's import filter to Nearest, and use
{name}_tileset.tres in a TileMapLayer: solid tiles have full-square collision, platform tiles a one-way top edge.
Unity: import tileset.png as Sprite (2D), Multiple, Filter Point, Compression None; Sprite Editor > Slice > Grid By
Cell Size {T} x {T}; then Tile Palette > Create New Palette and drag the slices in.
LDtk: Project > Tilesets > add tileset.png with a grid of {T} px; build an Auto-layer or paint with the roles above
(an LDtk project file is not written yet).
"""
