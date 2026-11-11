# Backgrounds and scenes: design (draft for the owner's decisions)

Status: design only, no code yet (2026-11-11). Characters, animations, expressions and objects exist (see
`docs/HANDOFF.md` section 13). This document proposes how PixelAgent makes the places those characters stand in.
The decisions it needs are at the end.

## 1. What a stage is in the target style

The *Alice in Cradle* stage data was studied locally (file layout only: names and the map format, nothing copied):

- **Stages are assembled, not painted.** There are 733 map files (`m2d/*.tmap`). Each one places named pieces
  (for example a chest piece, `ship_attach/chest_5.png`) at positions on named depth layers (`bg2`, ...), using the
  theme's chip set (`MapChip_forest`).
- **One chip set per theme:** 36 atlases in `MapChips/`, such as `mapchip_forest`, `mapchip_glacier`,
  `mapchip_house`, `mapchip_ruins` and `mapchip_mech`. Most themes also have a separate **scenery** set
  (`mapchip_forest_scape`) for the far background.
- **Effect layers** sit on top: dust, mist and the like (`__effect_dust_2.tmap`, `__effect_theroom_mist.tmap`).
- The themes are reused across many maps: about 290 maps use the mountain theme and 170 the forest theme.

So a level is a **kit** of hand-pixelled pieces (ground and walls, decorations, props) arranged on several
**depth layers** in front of a **scenery backdrop**. It is not one large picture. That suits an image model
well: it draws good small and medium pieces (our characters and objects), while a large picture drawn pixel by
pixel exceeds what one image can hold (section 2).

## 2. Constraints that shape the design

- **Image size.** The image model draws at most 1536x1024 (also 1024x1536 and 1024x1024). The native finish needs
  each sprite pixel to be a block of several image pixels: about 8 for characters and 8-32 for objects. A
  background layer at the characters' scale (a 160-px character, so a screen of about 960x540 or 1280x720 sprite
  pixels) would need blocks of 1-1.5 image pixels. At that size the model draws an illustration, not pixel art.
- **Seams.** A layer that scrolls must repeat horizontally without a visible seam. The image model does not
  guarantee that.
- **Palette and light** must match the characters (the palette grade) and get lighter, lower in contrast and
  cooler with distance (atmospheric perspective).
- **Transparency.** Every layer except the sky needs transparent areas. The backend already passes a `background`
  option to the image API; whether "transparent" works with our model is **unverified** (test it first). The
  fallback is a flat key colour, cut out the way the pose sheets are.

## 3. Proposal: two products, then a composer

### A. Scenery backdrop (the parallax layers behind the action)

One asset of kind `scene`, made from a prompt such as "a forest at dusk".

1. **Plan:** the director writes the layer plan: sky, far, mid and near, each with its content, colour key and
   distance. It also sets the size (default 960x540 sprite pixels, the screen) and the repeat width (one or two
   screens).
2. **Layers, drawn back to front.** Each layer is one image at 1536x1024. Earlier layers are given as references,
   so light, palette and horizon stay consistent. Far layers are simple shapes with few tones; nearer layers have
   more detail.
3. **Pixelize per layer.** Each layer is read at about 2 image pixels per sprite pixel (768x512 per image, or two
   images side by side for a 1536-wide layer). It gets the reduce treatment: per-material colours, the palette
   grade, and its own palette with distance. The owner already approved converted pixels for backgrounds
   (decision 2026-11-09).
4. **Seamless repeat:** shift the layer by half its width (wrap-around), mask a band over the old seam, and let
   the image model repaint only that band (a masked edit, as expressions already do). A seam lint then compares
   the left and right edge columns.
5. **Gate:** the owner approves the assembled backdrop with a parallax preview: the layers scroll at different
   speeds in the app, with a character standing in front.
6. **Export:** layered PNGs (one per depth), a flattened preview, a Godot `ParallaxBackground` scene (`.tscn`)
   with scroll factors, and JSON (layer order, scroll factors, repeat widths).

Cost per backdrop, estimated: four layers × (~$0.07 to draw + ~$0.05 for the seam) + plan ≈ **$0.50**.

### B. Theme kit (what the characters walk on and stand among)

One asset of kind `kit`, made from a theme such as "forest", that fits a backdrop.

1. **Plan:** the director lists the pieces:
   - ground: top, fill, left and right edges, inner and outer corners, a one-way platform;
   - walls and ceiling;
   - 8-12 decorations: bushes, rocks, flowers, a signpost, roots.

   It also sets the tile size: 16 or 32 sprite pixels, decided by the owner.
2. **Kit sheet:** the image model draws all pieces as pixel art on **one sheet** over a layout image, a labelled
   grid of cells. This is the same approach that fixed animation sizes, and it keeps one palette and light across
   the kit. Cost is about $0.07 per sheet; a large kit takes two sheets.
3. **Read back per cell:**
   - tile pieces are snapped to the tile grid;
   - pieces that must tile (ground fill, wall fill) get the wrap-and-repaint seam fix and a wrap-around lint;
   - decorations are cut like objects.
4. **Gate:** the owner approves the kit shown as a small test level: a few platforms built from the tiles, with
   the decorations placed.
5. **Export:** a tileset PNG plus a Tiled `.tsx` / Godot `TileSet` `.tres`, and a decorations atlas (the
   existing item sheet format).

Cost per kit: about **$0.20-0.35**.

### C. Composer (later)

A simple stage preview in the app: backdrop, kit pieces placed on two or three depth layers, a character and an
effect layer (mist or dust as a semi-transparent texture). Export to Godot. Build this only once A and B are
rated well.

## 4. Benchmarks

Three prompts, each made as a backdrop and a matching kit:

| Prompt | Tests |
|---|---|
| Forest at dusk | organic shapes, foliage edges, atmospheric depth |
| Town street by day | straight lines, perspective, windows and signs, a busy mid layer |
| Crystal cave | dark scene, glowing accents, the palette grade on a low-key image |

Each is checked by the owner (ratings 1-5, as with the characters) and by lint: seam error, colours per layer, and
contrast falling with distance. Cost for all three backdrops and kits is about **$2.50**.

## 5. Order of work

1. **Spike, about $0.30:**
   - does `background="transparent"` work;
   - one layer pixelized at about 2 image pixels per sprite pixel: does it read as pixel art next to a character;
   - one seam repaint.

   The results go to the owner before anything else is built.
2. **Backdrop (A):** new asset kind and steps, the app's New scene sheet with a parallax preview, export, and the
   three benchmark backdrops.
3. **Kit (B):** kit sheet, tiles, test level, export, and the three benchmark kits.
4. **Composer (C)**, if wanted.

## 6. Decisions (owner, 2026-11-11)

1. **Scale: 1x**, the characters' pixel scale, as in Alice in Cradle (a screen is about 960x540 sprite pixels).
2. **First: the spike** (section 5, step 1), with results before anything is built.
3. **Tile size: 32** sprite pixels for kits.
4. **Exports:** Godot, Tiled, LDtk and Unity (a sliced atlas plus setup notes), besides PNG layers.

## 7. Spike results (2026-11-11, `benchmarks/bg_spike.py`, `bench_runs_bg_spike/`, $0.116)

1. **Transparent layers work.** A far layer (mountains and a treeline) came back with 76% of its pixels fully
   clear. The shapes themselves are slightly see-through: alpha 192-254, never 255. A layer step must therefore
   threshold alpha, making it fully opaque or fully clear as pixel art needs, before the colours are reduced.
2. **A 2:1 pixelized scene reads as pixel art at 1x.** A forest-at-dusk scene drawn at 1536x1024, box-downsampled
   to 768x512, cut to 40 colours (k-means in OKLab) and graded medium looks like a hand-pixelled platformer
   background. Next to the 148-px character at 1x its detail scale matches: leaves, rocks and flowers are about the
   character's pixel size. For a 540-px-high screen, read at about 1.9:1 instead (808x540).
3. **The seam repaint works.** After a half-width wrap shift and a repaint of a 192-px band, the edge difference
   fell from 32.4 to 5.6 (mean colour difference across the wrap), and the band is not visible.

Not tested yet: separate layers drawn to fit together (the spike drew one full scene and one far layer
independently), colour keying when transparency fails, and a kit sheet.
Next step: build the backdrop (section 3A) on these three results.
