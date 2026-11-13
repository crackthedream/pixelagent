"""Instructions sent to models (used by real backends from M1; dry runs only record them)."""

INTAKE = """You are the art director of a pixel-art game. Turn the user's request (text and/or reference images)
into a CharacterSpec for ONE asset. Be concrete: list the materials (skin, hair, each garment, metal, eyes...)
with a hex base colour taken from the reference when there is one, the identity features that must survive at
low resolution (must_keep), the view, and proportions in head-heights. Keep the style guide values unless the
user asked otherwise."""

CONCEPT = """Full-body {asset_type} concept for a pixel-art sprite, {view} view, standing neutral pose, the whole
figure visible, centered, on a plain flat {background} background. Clear readable silhouette, large simple
shapes, strong value separation between materials, soft single light from the {light}, no cast shadow on the
ground, no text. Subject: {summary}. Must keep: {must_keep}.{look}"""

# the "soft" look (config [style] look): the proportions and finish of hand-made anime action-game sprites. Written
# as a description of the look, without naming any game or character, so the image model restyles rather than copies.
LOOK_SOFT = """
Art style (this matters as much as the design): a cute anime-style character for a 2D side-scrolling action game.
Proportions about {heads} heads tall: a LARGE head (with the hair about a fifth of the full height; a stylised
game figure between chibi and realistic) with a large, soft face, expressive anime eyes of a natural size for the
face (as the reference draws them) set fairly low, a tiny nose and a small mouth; a small, slim body with short
slender limbs and small hands and feet. A calm, readable composition: a relaxed standing pose with the arms close to the body, the hair at a
moderate volume close to the figure (not spreading wide), props small and held close, no busy overlapping
layers. A smooth, clean digital illustration (not pixel art, no visible
pixels). Light, soft, pastel-leaning colours, gentle low-contrast shading, soft coloured edges, no heavy black
outlines. Costume simplified into a few large clear shapes: keep the
defining colours, the hair shape and the two or three signature accessories; drop small ornaments, fine patterns
and busy folds."""

CLEAN_REFERENCE = """Redraw the character in this image as a clean concept for pixel-art conversion.
Keep the identity: the same expression, hairstyle and hair colour, eye colour, costume colours and signature
accessories{identity_extra}. Character: {summary}. Must keep: {must_keep}.
{view} view, FULL BODY from the top of the head to the shoes, standing in a relaxed neutral pose, centered, with
some empty space around the figure. If the image shows only part of the body or a seated pose, complete the full
standing figure (legs, shoes) consistently in the same style and costume. Plain flat light-grey background, no
scenery, no furniture, no props that are not part of the character, no text. Large simple shapes, clear value
separation between materials, soft single light from the {light}, no cast shadow.{look}"""

# the native finish: the character drawn AS a hand-pixelled sprite (pixelart.py reads it back pixel for pixel). The
# rules are measured from Alice in Cradle's sprites (docs/FACE_ROOT_CAUSE.md section 7), described without naming it.
PIXEL_RULES = """SCALE: the sprite is about {height} pixels tall, drawn pixel by pixel. Show it enlarged so that EVERY
sprite pixel is a crisp, perfectly square block of exactly {block}x{block} image pixels on one regular grid
(nearest-neighbour: no smoothing, no blur, no anti-aliasing between blocks). Do NOT draw grid lines or pixel borders.
PROPORTIONS for that size: about {heads} heads tall -- the head with hair about {head_px} pixels, a large face about
{face_px} pixels wide, slim legs about {leg_px} pixels wide (the legs about 40% of the height), small hands of a few
pixels. A stylised game figure: NOT chibi (a head of a third of the height or more is too big), and not a tall
realistic figure either.
PIXEL-ART RULES: a light, soft, high-key palette. Each material (skin, hair, each garment) uses 4-6 flat tones laid
down as clean, deliberate clusters -- no gradients, no dithering, no noise. NO dark outline around the figure: its
edges are soft, LIGHTER or slightly darker tones of the material itself; dark pixels only inside the design (lashes,
pupils, dark materials). Light from the top-left. Hair as large shaped masses with a few drawn strands and bright
highlight bands. The FACE is designed in pixels, simple and vivid: each eye a small shape of a dark lash line, an
iris of 2-3 pixels with a 1-pixel highlight and a hint of white; a tiny mouth of 1-3 pixels; no nose; soft blush
pixels if the character has them.
A calm, readable figure STANDING upright on both feet in a relaxed pose -- even if the reference sits, kneels or
crouches -- with the arms close to the body, props small and held close, {view} view, centered, full body. NO
furniture, bench, chair or other scenery objects. Plain flat light-grey background, no shadow, no text."""

# objects and props in the native finish: one item, drawn as pixel art at its game size (the longest side)
PIXEL_OBJECT_RULES = """SCALE: the item is about {size} pixels across its LONGEST side, drawn pixel by pixel. Show it
enlarged so that EVERY sprite pixel is a crisp, perfectly square block of exactly {block}x{block} image pixels on one
regular grid (nearest-neighbour: no smoothing, no blur, no anti-aliasing between blocks). Do NOT draw grid lines or
pixel borders.
PIXEL-ART RULES: a light, soft palette. Each material (wood, metal, glass, cloth, liquid...) uses 3-5 flat tones laid
down as clean, deliberate clusters -- no gradients, no dithering, no noise. NO dark outline ring around the item: its
edges are its own darker or lighter tones; dark pixels only for real dark materials and small inner details. Light
from the top-left, with 1-2 pixel highlights on shiny or glassy parts. A clear, recognisable silhouette that reads at
1x; details simplified to what {size} pixels can hold.
ONE single item, whole and centred, alone, shown {view} as a game shows it in the world or an inventory: no hands, no
character, no other objects, no scenery, no shadow, no text. Plain flat light-grey background."""

PIXEL_OBJECT = """Redraw the item in this image as an authentic hand-pixelled game SPRITE for a 2D anime action game, in
the style of modern hand-made pixel-art action platformers. Keep what the item is: its shape, colours and signature
details, simplified for this size. Item: {summary}. Must keep: {must_keep}.
""" + PIXEL_OBJECT_RULES

PIXEL_OBJECT_TEXT = """An authentic hand-pixelled game SPRITE of one item for a 2D anime action game, in the style of
modern hand-made pixel-art action platformers. Item: {summary}. Must keep: {must_keep}.
""" + PIXEL_OBJECT_RULES

PIXEL_SPRITE = """Redraw the character in this image as an authentic hand-pixelled SPRITE for a 2D side-scrolling anime
action game, in the style of modern hand-made pixel-art action platformers. Keep who the character is: hairstyle and
hair colour, eye colour, costume colours and the signature accessories, simplified for this size. Character:
{summary}. Must keep: {must_keep}.
""" + PIXEL_RULES

PIXEL_SPRITE_TEXT = """An authentic hand-pixelled SPRITE of a character for a 2D side-scrolling anime action game, in
the style of modern hand-made pixel-art action platformers. Character: {summary}. Must keep: {must_keep}.
""" + PIXEL_RULES

# the soft look's reference redraw: proportions come FIRST and with numbers -- an image edit anchors strongly on the
# reference, and a style note at the end left realistic proportions untouched (ref03, 2026-11-10)
RESTYLE_REFERENCE = """Restyle the character in this image into a full-body concept for a cute 2D side-scrolling
action game.
PROPORTIONS FIRST -- do NOT keep the reference's body proportions: the figure is about {heads} heads tall, so the
head (with hair) is about {head_pct}% of the full height -- about one fifth: a stylised game figure between chibi
and realistic proportions. A large head and a large, soft face with expressive anime eyes of a natural size for the
face (as the reference draws them) set fairly low, a tiny nose and a small mouth. A small, slim body: short torso,
slender legs (about 40% of the height), small hands and feet.
A calm, readable composition: a relaxed standing pose with the arms close to the body, the hair at a
moderate volume close to the figure (not spreading wide), props small and held close, no busy overlapping
layers.
Keep who the character is: the expression, hairstyle and hair colour, eye colour, costume colours and the signature
accessories. Character: {summary}. Must keep: {must_keep}.
Finish: a smooth, clean digital anime illustration (not pixel art, no visible pixels); light, pastel-leaning colours,
gentle low-contrast shading, soft coloured edges, no heavy black outlines; the costume simplified into a few large
clear shapes, small ornaments, fine patterns and busy folds dropped.
{view} view, FULL BODY from the top of the head to the shoes, standing in a relaxed neutral pose, centered, with some
empty space around the figure. Plain flat light-grey background, no scenery, no props that are not part of the
character, no text, soft single light from the {light}, no cast shadow."""

# revising a finished character in the reduce finish: the change is made on the concept, which is then reduced again
REVISE_CONCEPT = """Edit this character concept. Change requested: {note}
{scope}Keep everything else exactly as it is: the same character, pose, proportions, framing, colours, style and
background. The result must look like the same illustration with only the requested change."""

LOCATE_FACE = """This is a pixel-art game character shown enlarged. Give the tight box around its FACE FEATURES: both
eyes, the brows and the mouth (not the whole head, not the hair), as fractions 0-1 of the image width and height.
found=false when the face cannot be seen. Also name the colour of the irises in one or two plain words, and what the
face wears or is marked with (glasses, goggles, a mask, face paint, a scar), if anything."""

# scene backdrops (pixelagent/steps/scene.py)
SCENE_PLAN = """Plan a parallax backdrop for a 2D side-scrolling pixel-art action game (the look of modern hand-made
pixel-art platformers such as Alice in Cradle) from the request below. Give 3-5 layers from far to near: first the
sky (opaque, the whole image), then far, middle and near layers. Each layer: a short name, what it shows concretely
(shapes, objects, where they sit on the screen), its depth (0 = sky .. 1 = nearest) and a colour budget (12-16 for
far layers, 24-40 for near ones). The near layer frames the play area (foreground plants, posts, rocks along the
bottom or the sides) without covering the middle of the screen, where the characters walk. One horizon, one light
and one palette shared by every layer; a light, slightly muted palette."""

SCENE_LAYER = """One layer of a parallax backdrop for a 2D side-scrolling pixel-art action game. Scene: {summary}.
Light: {light}. Palette (shared by every layer): {palette}. The horizon is at {horizon}% of the image height.
Draw ONLY the "{name}" layer: {content}. Distance: {distance}.
A wide side view that continues to the left and right (no frame, nothing cut off at the sides). Hand-pixelled style
like modern pixel-art action platformers: very small crisp pixels, clean flat tone clusters, no gradients, no blur,
no text, no characters, no UI.{fill}"""

SCENE_SKY = """
This layer fills the WHOLE image: the sky with its light, clouds and atmosphere, nothing in front of it. It must
REPEAT sideways: its colours change only from top to bottom, never from left to right (no sun or glow on one side),
and no cloud touches the left or right edge."""

SCENE_TRANSPARENT = """
Everything that is not part of this layer is fully transparent (no sky, no background colour, no other layers):
the layer's shapes stand alone and are fully opaque. It must REPEAT sideways: leave a clear, empty gap at both the
left and the right edge (no tree, house or object touches the sides); only a ground strip along the bottom may run
from edge to edge, as an even, repeating texture."""

# walkable kits (pixelagent/steps/kit.py)
KIT_PLAN = """Plan a walkable tile kit for a 2D side-scrolling pixel-art action game (the look of modern hand-made
pixel-art platformers such as Alice in Cradle) from the request below: the ground's top surface (what you walk on)
and its fill (what it is made of inside), a floating one-way platform, a palette and light (keep the scene's if one
is given), and 6-10 decorations that stand on the ground (plants, rocks, props of the place), each with a short
description and a size of 1-3 tiles of 32 pixels wide and high. A light, slightly muted palette."""

KIT_SHEET = """This is a layout for a 2D side-scrolling pixel-art game tileset ({theme}). Paint it as authentic
hand-pixelled game art with very small, crisp pixels, clean flat tone clusters, no gradients, no blur, no text.
Palette: {palette}. Light: {light}.
- The large grey block at the bottom left becomes one solid piece of ground: {surface} along its whole top edge
  (it may poke a few pixels above it), {fill} inside, and finished left, right and bottom edges. Keep its exact
  shape and position, and paint the inside as an even texture with no big single features, so it can be cut into
  repeating 32-pixel tiles.
- The thin grey bar at the top left becomes a floating one-way platform: {platform}. Keep its shape and position.
- In the empty area on the right, draw these decorations, each standing alone on its own invisible ground line with
  clear transparent space around it (they must not touch each other):
{decorations}
Everything else stays fully transparent: no sky, no background, no shadows on the background."""

# an expression of a finished pixel-art character: a masked edit of its pixel-art concept, the face only
EXPRESSION = """Edit this pixel-art character sprite: change ONLY the facial expression, inside the transparent area of
the mask, to {expression}. It is hand-pixelled art shown enlarged: keep exactly the same block size and grid, crisp
square blocks (no smoothing, no blur, no new detail finer than one block), the same palette and the same face shape.
Do NOT repaint the skin or the hair: every skin pixel that the expression does not need keeps its exact colour, and
the fringe and side locks stay pixel for pixel. This is the SAME character: open eyes keep their own iris colour,
lashes and highlights and the way they are drawn (change their shape and openness only as the expression needs);
brows and mouth are a few designed pixels in the original face's style -- vivid and simple, like a pixel-art game
character's expression. Whatever the face wears or is marked with (glasses, goggles, a mask, face paint, a scar,
freckles) stays exactly as it is, the same shape, size and colours: draw the expression behind or around it.
Everything else stays exactly as it is."""

EXPRESSION_EYES = """
Whenever the eyes are open, the irises keep exactly the colours they have in the original image (they look
{colour}): the same hue, not brighter and not more saturated."""

EXPRESSION_WEARS = """
The face wears {wears}: keep it exactly as it is drawn in the original image -- the same shape, size, colours and
place. The eyes show through or behind it as they do in the original."""

EXPRESSION_RETRY = """
IMPORTANT: an earlier attempt was rejected: {issue}. Fix exactly that."""

# the check of a drawn expression (expressions.judge): the neutral face on the left, the new one on the right
FACE_CHECK = """Two pixel-art faces of the same game character: LEFT the original, RIGHT a new expression that was
asked to be: {expression}. Only the eyes, brows and mouth were meant to change. Check the RIGHT face against the
LEFT one:
- reads: it shows the requested expression (judge the feeling, not the exact wording of the request);
- same_skin: the skin has the same base tone (a blush, tears or a shadow that the expression needs is fine; a
  lighter, darker or differently tinted skin is not);
- same_hair: the fringe and side locks have the same shape and colours (small pixel noise is fine);
- eyes_in_style: open eyes keep the original IRIS COLOUR and are drawn in the same style -- an iris that changed
  colour (for example green to red or brown) or blank white eyes fail; true when the expression closes the eyes;
- same_wear: everything worn or marked on the original face (glasses, goggles, a mask, face paint, a scar) is
  still there with the same shape and colours -- goggles that became thin glasses, or vanished, fail; true when
  the original face wears nothing.
Fail only what is clearly wrong."""

REFINE_CONCEPT = """Refine this concept for pixel-art conversion without changing the design: keep identity, pose,
colours and proportions; simplify noisy detail into large clear shapes, make the silhouette crisp, keep the
plain flat light-grey background and the single soft light from the {light}."""

LIST_MATERIALS = """List every visually distinct material of this {asset_type} as it appears in the image, so the
image can be segmented into flat regions: skin, hair, eyes, each garment layer (coat, inner shirt, tights...),
trims and belts, gloves, boots, each part of held items (staff wood, crystal...), etc. Merge things that share
one colour family and surface (e.g. both boots = 'boots'). 2-16 materials, short snake_case names, the base
colour as it appears in the image (lit side, not shadow). Spec materials for reference: {spec_materials}"""

MATMAP = """Recolor this image into a flat segmentation map for pixel-art production. Keep every shape, edge,
proportion and position EXACTLY as in the input (same framing, same pose, nothing moved, added or removed).
Paint each region in its exact flat key colour below. Flat fills only: no shading, no gradients, no outlines,
no texture, no antialiasing, no lighting. Dark line art belongs to the region it borders. The background
becomes pure white #FFFFFF.
{legend}"""

PICK_GRID = """These are {n} pixelizations of the same concept on slightly different pixel grids (numbered from 0).
Pick the one where the face (eyes symmetric, readable), hands and silhouette land most cleanly on the grid,
with the fewest broken or doubled features. Answer with the index."""

LABEL_MATERIALS = """Each numbered swatch is a colour cluster of the sprite; the mask shows where it is used.
Assign every cluster to the material it MOSTLY covers, preferably from this list: {materials}. If the sprite shows
a material the list misses (inner shirt, tights, belt, gloves...), use a new short snake_case name instead of
forcing a wrong one. Use 'outline' for near-black edge clusters. Lighter/darker shades of one material share a
name. Look at where each cluster sits on the figure (face vs hair, coat vs inner clothing), not only its colour."""

PLAN_DETAIL = """You are a lead pixel artist. The sprite is shown zoomed with a coordinate grid (labels every 8 px;
x to the right, y down, origin top-left). Choose up to {max_regions} focal regions that most need hand-refined
pixels for this character to read well at 1x: the face/eyes first, then hands, held items, emblems or other
small identity details from the spec. Each region at most {max_side}x{max_side} px, tight around the feature
plus 1 px of context. Give a concrete goal per region. Spec detail priorities: {priorities}"""

REWRITE_REGION = """You are a senior pixel artist refining one small region of a {sprite_w}x{sprite_h} sprite.
Region '{name}' at x={x}, y={y}, size {w}x{h}. Goal: {goal}
Images: 1) the whole sprite zoomed, region outlined in magenta; 2) the current region zoomed with a pixel grid;
3) the same area of the high-resolution concept art the sprite must stay faithful to.
Each variant is the region as exactly {h} rows of exactly {w} characters. Use only these palette keys:
{legend}
'.' = transparent, '=' = keep the current pixel. Rules: stay inside the silhouette unless the goal needs a
contour change; keep the 1-px border consistent with the neighbouring pixels outside the region (outline
continuity); read clearly at 1x: deliberate clusters, no noise, no single stray pixels except intentional
highlights (eye glints, gem facets); follow the light from the {light}. Do not redraw areas that are fine.
Current region (x labels on top, y labels on the left):
{grid}"""

PICK_REGION = """Candidates for the region '{name}' (goal: {goal}) are shown side by side, each as the sprite area
around the region (zoomed) with the full sprite at 1x below. #0 is the current version. Pick the candidate that
best achieves the goal while staying faithful to the concept and clean at 1x. Pick #0 if no candidate is a
real improvement."""

CRITIC = """You are a senior pixel artist reviewing a sprite (shown at 1x and zoomed) against its concept and spec.
The concept image is the ground truth for design, colours and details: judge fidelity only against what the
concept actually shows (if the concept's shield is red with an emblem, that is correct); never ask to remove or
recolour something the concept has.
Score 0-10: silhouette (readable at 1x), proportions (vs spec), fidelity (identity features), palette
(discipline, ramps), lighting (consistent from {light}, no pillow shading), lines (clean, sel-out), detail (face,
hands), cleanliness (no noise/orphans/jaggies). List concrete issues with the area and the fix. For every
LOCAL issue give region = [x, y, w, h] in sprite pixel coordinates, tight around the problem (at most 24x24);
the first image is the sprite zoomed with a grid labelled every 8 px (x right, y down, origin top-left).
verdict: pass if overall >= 8 and no high-severity issue, else repair.
Size convention (by design, do not report it as an issue): height_px is the sprite's height INCLUDING its
outline; the canvas adds a transparent margin on every side, so the canvas is larger than height_px.{style_note}
Objective lint results: {lint}"""

# the critic's note for the native finish: hand-pixelled sprites
CRITIC_NATIVE = """
STYLE (by design, do not report it as an issue): this game's look is hand-pixelled anime action-game sprites. A
light palette with 4-6 tones per material in deliberate clusters, soft edges and no dark outline ring are intended;
do not ask to add an outline or reduce to a strict palette. Judge what matters: readability at 1x, proportions,
identity against the concept, the face and its expression, hands and held items, and clean clusters (no noise).
The lint counts below follow strict-palette rules: treat them as information."""

CRITIC_NATIVE_OBJECT = """
STYLE (by design, do not report it as an issue): this game's look is hand-pixelled anime action-game sprites. A
light palette with 3-5 tones per material in deliberate clusters, soft edges and no dark outline ring are intended;
do not ask to add an outline or reduce to a strict palette. This is an ITEM, not a character: judge readability of
the silhouette at 1x, whether it is clearly the item described, its materials, highlights and clean clusters (no
noise). The lint counts below follow strict-palette rules: treat them as information."""

# the critic's note for the soft look's reduce finish (config [finish] mode = "reduce")
CRITIC_SOFT = """
STYLE (by design, do not report it as an issue): this game's look is SOFT, like hand-made anime action-game
sprites. Many gentle shades (up to {colors} colours), soft anti-aliased edges, single-pixel shading steps and no
dark outline ring are intended; do not ask to reduce colours, remove single pixels, add an outline or harden edges.
Judge what matters for this look: readability at 1x, proportions, identity against the concept, the face and its
expression, hands and held items. The lint counts below follow strict-palette rules: treat them as information."""

ANIM_PLAN = """You plan a pixel-art sprite animation for an approved game character (images: the approved sprite,
then its concept art). User request: "{request}".
Decide the number of frames (follow the request if it gives one; otherwise 4-8, at most 12), whether it loops,
whether it plays in place, and for each frame a concrete, drawable pose: body lean and height, each arm and leg,
held items, hair/cloth secondary motion. Use classic pixel-art timing: 80-150 ms per frame, hold key poses longer.
Keep the sprite's view ({view}) and design. List what moves and what must stay identical in every frame."""

POSE_SHEET = """A horizontal pose reference sheet of {n} animation frames of THIS exact character (same design,
colours, proportions, costume and props as the reference images), {view} view. The {n} frames stand in ONE row,
evenly spaced from left to right with clear empty space between them, all at the same scale, all standing on the
same ground line, full body visible, no overlap. Plain flat light-grey background, no text, no numbers, no borders,
no shadows. Animation: {summary}. Frames, left to right:
{frames}"""

# the pose sheet for the reduce finish: its figures are reduced straight into the frames
POSE_SHEET_SOFT = """
Style: draw every frame exactly like the FIRST reference image (the concept): the same smooth, clean anime
illustration, colours and proportions -- NOT pixel art, no visible pixels (the second reference is only a small
sprite for the design). Keep each frame's face clearly visible and the same size in every frame."""

# the pose sheet for the native finish: every frame drawn as pixel art on one shared block grid, read back per frame
POSE_SHEET_PIXEL = """
Layout: the FIRST reference image is the layout -- the character's finished sprite placed {n} times at its exact final
size and pixel scale, evenly spaced on one ground line. Redraw each copy, left to right, in its frame's pose: at the
SAME size and pixel scale (an upright pose exactly as tall as the layout's figure, never bigger), in the same place
on the same ground line, keeping the empty space between the figures. The SECOND reference image is the same sprite
alone, enlarged the same way.
Style: draw every frame as an authentic hand-pixelled sprite exactly like that sprite: the same design, palette,
proportions and pixel style. Each sprite is about {height} pixels tall;
show the sheet enlarged so that every sprite pixel is a crisp, perfectly square block of exactly {block}x{block}
image pixels, on ONE regular grid shared by all frames (nearest-neighbour: no smoothing, no blur). Do NOT draw grid
lines or pixel borders. Clean clusters of flat tones, no gradients, no dithering, no dark outline around the figure;
the face a few designed pixels, the same size in every frame."""

# added when a pose sheet is drawn again because its pixels came out too coarse (anim.too_coarse)
POSE_SHEET_RETRY = """
IMPORTANT: an earlier attempt drew its pixels too large, so every figure had too few pixels and lost detail. Each
figure must have exactly as many pixels as its copy in the layout: one pixel of the layout stays ONE block of
{block}x{block} image pixels, never bigger."""

ANIM_FRAME = """You are animating the game character '{name}' with the pixelliner tools: frame {k1} of {n} of the
'{anim}' animation. Pose for this frame: {pose}

The canvas has {n} frames. Layer 'drawing' is the sprite; for this frame it starts as a copy of {start}. Layer
'reference' (never exported) holds this frame's pose trace, converted from a pose sketch: follow it for the POSE
(where the body, limbs, held items, hair and cloth are), but keep the character's DESIGN exactly as the model sheet
(the approved sprite): same palette keys, outline style, face, details and proportions. Change only what moves;
leave everything else pixel-identical so the animation does not flicker.
Always pass frame={k} to draw, view and compare_reference. `compare_reference` (frame={k}) shows where this frame
differs from the trace (red = missing, blue = extra); `view` with onion=true ghosts the previous frame; `view`
mode 'grid' with a small region reads exact pixels (layer 'drawing' or 'reference'). Typical work: clear the moved
parts (draw op 'clear' with a region, or pixel_map with '.'), redraw them at the new position with pixel_map in the
same colours, keep the outline continuous. Must stay identical: {constant}.
Budget: about {turns} rounds. Call `finish` when the frame matches the pose and the design."""
