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
game figure between chibi and realistic) with a large, soft face, big expressive anime eyes set fairly low in the face, a tiny nose and a small mouth; a small, slim body
with short slender limbs and small hands and feet. A smooth, clean digital illustration (not pixel art, no visible
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

# the soft look's reference redraw: proportions come FIRST and with numbers -- an image edit anchors strongly on the
# reference, and a style note at the end left realistic proportions untouched (ref03, 2026-11-10)
RESTYLE_REFERENCE = """Restyle the character in this image into a full-body concept for a cute 2D side-scrolling
action game.
PROPORTIONS FIRST -- do NOT keep the reference's body proportions: the figure is about {heads} heads tall, so the
head (with hair) is about {head_pct}% of the full height -- about one fifth: a stylised game figure between chibi
and realistic proportions. A large head and a large, soft face with big expressive
anime eyes set fairly low, a tiny nose and a small mouth. A small, slim body: short torso, slender legs (about 40%
of the height), small hands and feet.
Keep who the character is: the expression, hairstyle and hair colour, eye colour, costume colours and the signature
accessories. Character: {summary}. Must keep: {must_keep}.
Finish: a smooth, clean digital anime illustration (not pixel art, no visible pixels); light, pastel-leaning colours,
gentle low-contrast shading, soft coloured edges, no heavy black outlines; the costume simplified into a few large
clear shapes, small ornaments, fine patterns and busy folds dropped.
{view} view, FULL BODY from the top of the head to the shoes, standing in a relaxed neutral pose, centered, with some
empty space around the figure. Plain flat light-grey background, no scenery, no props that are not part of the
character, no text, soft single light from the {light}, no cast shadow."""

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
