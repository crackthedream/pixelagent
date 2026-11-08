"""Instructions sent to models (used by real backends from M1; dry runs only record them)."""

INTAKE = """You are the art director of a pixel-art game. Turn the user's request (text and/or reference images)
into a CharacterSpec for ONE asset. Be concrete: list the materials (skin, hair, each garment, metal, eyes...)
with a hex base colour taken from the reference when there is one, the identity features that must survive at
low resolution (must_keep), the view, and proportions in head-heights. Keep the style guide values unless the
user asked otherwise."""

CONCEPT = """Full-body {asset_type} concept for a pixel-art sprite, {view} view, standing neutral pose, the whole
figure visible, centered, on a plain flat {background} background. Clear readable silhouette, large simple
shapes, strong value separation between materials, soft single light from the {light}, no cast shadow on the
ground, no text. Subject: {summary}. Must keep: {must_keep}."""

CLEAN_REFERENCE = """Redraw this character as a clean concept for pixel-art conversion: same identity, colours,
costume and proportions; {view} view, full body, standing neutral pose, centered, plain flat light-grey
background, large simple shapes, clear value separation between materials, soft single light from the {light},
no cast shadow, no text, no background scenery."""

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
outline; the canvas adds a transparent margin on every side, so the canvas is larger than height_px.
Objective lint results: {lint}"""
