# Why the faces look off: root-cause analysis

2026-11-10. The owner asked to stop iterating and find the root cause, because faces kept looking odd after many
fixes. This document consolidates the current framework, the issues that surfaced, and the evidence from a
stage-by-stage trace of the face through the pipeline.

**Summary.** The pipeline already produces a good face early on and then destroys it. At 160 px with the soft
look's big heads, the plain reduction of the concept onto the sprite grid gives faces that match the concept: eyes,
eye colour and expression. Three later stages replace that face with something worse:
1. The palette step starves the eyes of colours (green eyes became brown).
2. The model's redraw reinvents the face (the largest drift from the concept).
3. The face kit stamps generic template eyes over it.

Every fix so far (five kit redesigns, measured layout, bigger sizes) worked on that last replacement layer, so the
underlying loss was never addressed. A free test that simply keeps the reduction's face in the finished sprite
gives the best faces of the whole project.

---

## 1. The framework as it stands

### Character pipeline (14 steps)

| # | Step | What it does | Who |
|---|---|---|---|
| 1 | intake | Spec: summary, materials, view, height (160), heads_tall (4.6, soft look) | language model |
| 2 | concept | Concept image; the soft look restyles references (proportions first) | image model |
| 3 | matmap | Flat material-colour map of the concept, used for labels | image model |
| 4 | pixelize | Reduces the concept onto the sprite grid (area average in linear light); picks the best grid offset | code, plus a model pick |
| 5 | materials | Labels each pixel with a material; builds one ramp per colour family within the budget (40 colours) | code |
| 6 | rebuild | Snaps every pixel to its material's ramp | code |
| 7 | lines | Outline (soft: sel-out from the materials' own shades) | code |
| 8 | detail | Skipped in artist mode | |
| 9 | polish | Pixel cleanup → the **trace** | code |
| 10 | artist | The model redraws the whole sprite over the trace (20-40 tool rounds), then a **face pass** (rules v3: face kit, measured layout) | language model through the PixelLiner engine |
| 11-14 | critique, review, animate, export | Score, gate C, export | |

### How a face is made today

The face goes concept → reduction (step 4) → palette (5-6) → outline and cleanup (7, 9) → the model's redraw
(10, draw) → face pass (10, face). The face pass clears the eyes and mouth and builds new ones with the engine's face
kit (`pixelliner-mcp/face_kit.py`), at the size and place given by `pixelagent/face.py face_layout`.

### What changed during the face work

| When | Change | Aimed at | What remained |
|---|---|---|---|
| Face lab r1 | Face trace, rules v2 (feature-keeping reduction at 96 px) | Faces dissolving in the trace | Lost to v1 on subtle anime faces; removed later |
| Kit v3 | Template eyes and mouths | Uneven hand-placed eyes | Box eyes, dark "v" mouths |
| AIC study 1 | Lash with outer corner, soft mouths | Odd structure | Still odd |
| 160 px | Default height from 96 to 160 | Too few pixels | Better, still odd |
| AIC study 2 | Eye white, vertical iris, 7 expressions | Missing white | Still odd |
| AIC study 3 | Measured layout, `turn`, mid-tone iris, `skin` op | Small, misplaced eyes; harsh contrast | Still odd |
| Soft look | Big heads (~4 heads), light palette, soft outline | Body proportions shrinking the face | Body clearly better; eyes too small for 24-30-px faces |
| xxl | 9×7 eyes | Eye size | Flat eyes, milder expressions than the concepts |

Each step was judged on the final image and changed the last layer: the template face.

---

## 2. Evidence: one face through every stage

`benchmarks/face_stages.py` crops the same face area at every stage (all stages share one grid) and measures it
against the **plain reduction**: each grid cell's exact concept area, averaged in linear light. Images:
`benchmarks/face_lab_runs/analysis/stages.png` and `protected_face.png` (gitignored: derived from owner references).

Mean colour distance from the plain reduction, inside the face box (lower = closer to the concept):

| Stage | ref05 | ref01 | ref03 | Colours in the face area |
|---|---|---|---|---|
| plain reduction | 0 | 0 | 0 | 580-1010 |
| pixelize (step 4) | 1.6 | 2.5 | 0.2 | 580-990 |
| palette (steps 5-6) | 9.9 | 12.8 | 8.7 | 26-29 |
| outline (step 7) | 9.5 | 12.1 | 8.7 | 26-31 |
| trace (polish, step 9) | 10.2 | 12.5 | 9.2 | 26-31 |
| **model draw** (step 10) | **33.3** | **40.2** | **31.0** | 19-25 |
| face pass (step 10) | 36.9 | 40.9 | 33.5 | 20-29 |

What the images show:
- **The plain reduction and pixelize already give the right face.** ref01 has big green eyes and an open laugh;
  ref05 and ref03 have their eyes and expressions. With the soft look's big heads a face is 24-30 px wide, enough
  for the reduction to carry the features.
- **The palette step changes the face's identity.** ref01's green eyes turn brown. The `eyes` material got base
  `#ccac74` and a 2-step brown ramp, because the base colour averages everything labelled "eyes" (lashes, edges,
  skin) and the colour budget is shared by area (eyes: 101 of ~5,000 pixels). `eye_whites` and `mouth` get 2 steps
  each.
- **The model's redraw is the largest single loss**, about three times the palette step's drift. Drawing pixel by
  pixel through tool calls, the language model cannot reproduce fine structure; it repaints the face with eyes of
  its own invention.
- **The face pass then substitutes templates.** The kit's eyes are generic (Noel-derived), smaller than the
  concept's, and its layout follows the game's proportions instead of this concept's own eyes. ref01's laugh
  becomes a small mouth.

**Free confirmation.** Taking the face (face materials only) from the pixelize output and putting it into the
finished sprite (`protected_face.png`) gives the best faces of the whole project. At 1× and 2× all three match their
concepts: ref01's laugh, ref05's calm look and ref03, which had been the weakest. Zoomed in, the face is soft with
many shades, which is how the target game's sprites are made (Noel: hundreds of shades per frame).

---

## 3. Root causes

1. **The pipeline throws away its best face.** The image model draws the face well, and the reduction keeps it.
   Steps designed for clothing and hair (palette ramps, the model's redraw) then process the face like any other
   area, and the face pass rebuilds it from templates. Every fix worked downstream of the loss.
2. **The colour budget is shared by area.** Small, focal materials (eyes, eye whites, mouth) get the fewest ramp
   steps and an averaged base colour. The eyes lose their colour, and the face loses its soft shading.
3. **A language model is the wrong tool for face pixels.** It is useful for judgement and for cleaning large shapes,
   but not for reproducing 20-30 px of precise structure through tool calls. It reinvents the face; the measured
   drift triples at this step.
4. **The templates encode another character and another face.** The kit was distilled from one game character. It
   replaces this concept's eye shape, size, lashes, catchlights and expression with a generic eye, and its hard flat
   clusters clash with the soft body around them. The game itself uses about 170 distinct eye shapes across 261
   faces of one character; it does not stamp templates.
5. **The target style is not the style the pipeline was built for.** The pipeline was built for classic
   limited-palette pixel art: 32-40 colours, ramps, dark outlines, cluster merging. The target is soft,
   high-colour pixel art (hundreds of shades, anti-aliasing). Several steps push away from it.
6. **Decisions from 64/96 px were carried into 160 px.** At 96 px a face was 9-13 px wide and the reduction could not
   hold it, so faces had to be drawn or stamped. At 160 px with big heads (24-30 px faces) that premise no longer
   holds, but the drawn/stamped face path stayed.
7. **The process judged only the end result.** Previews on traces, zoomed crops and final sprites were compared,
   never the face at each stage. The loss happened two to three stages before anything we looked at. The face-only
   test rounds even started from raw traces.

---

## 4. Implemented: the reduce finish (2026-11-10)

The owner confirmed the reduction's faces were the best and the model-drawn finals "quite chaotic". A whole-sprite
check showed the model's redraw adds the chaos everywhere, not only in the face: hair turns lighter and blotchy, and
shapes are reinvented. So the soft look now **finishes the sprite from the reduction itself**:

- **`[finish] mode = "reduce"`** (the new default):
  - `build.soft_reduce` gives each material its own colours by k-means in OKLab. The 96-colour budget
    (`reduce_colors`) is shared by the square root of area, and focal materials (eyes, mouth, blush) get at least 4.
  - Only near-identical specks merge.
  - No outline ring is added (pixelize then uses the full height), and polish passes the sprite through.
  - The detail step and the model's drawing are skipped.
- **The drawing step passes the reduction through as its result.** Revise with a note, a rejection at gate C and
  versions all work as before; the model edits that sprite only when asked.
- **Cost:** a character no longer has a drawing step, so it is plan + concept + material map + grid pick + critique,
  about $0.15-0.20 (the app quotes $0.30). It takes about 2 seconds after the concept.
- **Free test** on the three soft-look runs (`bench_runs_reduce`, stand-in models): all three look like their
  concepts at 1× and 2× (ref01's green eyes and laugh, ref05, ref03), with 97-100 colours.
- **`mode = "artist"`** keeps the drawing finish (and the face kit) for the classic look and small sprites.
- **First fresh run** (ref09 from its reference, $0.15, 3 min) looked like its concept. It showed three issues,
  now fixed:
  1. **Too chibi** (~3 heads tall): the restyle prompt now says "NOT a chibi or super-deformed figure (a head of a
     third of the height or more is too big)". Not yet confirmed on a new concept.
  2. **Eyes lightened** by cluster averages: `build.reduce_material` gives the darkest shade of every material
     from its darkest third, and focal materials (at least 6 shades) also keep their lightest. The darkest eye
     value on ref09 went 83 → 75 (plain shrink: 74).
  3. **The critic judged by strict-palette rules:** in reduce mode the plan's `max_colors` is the reduce budget,
     and the critic gets `prompts.CRITIC_SOFT` (many soft shades and single shading pixels are intended). Lint
     scores are information only for this look.
- **Second fresh run** (ref10, $0.14): the critic now judges the soft style sensibly (palette 8, cleanliness 7; its
  issues are pose, small eyes, hands, feet). The proportions overshot the other way: ~5.7 heads, because a seated
  reference needed invented legs. Prompt wording alone cannot hold the proportions.
- **Measure and retry:**
  - `face.heads_tall` measures the reduced figure: height over hair-top-to-chin. On saved runs: ref09 2.79, ref10
    5.68, the soft-look runs the owner liked 3.27-3.65, the realistic 160-px ref01 6.04.
  - `materials.proportion_check` runs when the labels exist. Outside `[concept] heads_range` (3.2-5.2), it adds a
    correction note to the concept ("make the head LARGER/SMALLER relative to the body") and sets `redo_from`.
  - The pipeline then redoes from the concept, once (`proportion_retries`). At gate B the new concept waits for
    approval as usual; test runs never retry.
  - Cost: ~$0.11 more, only when a concept misses.
- **Validation batch** (2026-11-10, `benchmarks/batch.py`, all ten owner references, `bench_runs_batch`):
  - 10/10 complete, $2.22 in total ($0.14 without a retry, ~$0.25 with one), 2.5-5.5 min each.
  - The proportion retry fired on 7/10. First attempts leaned too tall: 6 of 7 misses measured 5.3-6.4 heads, an
    overcorrection by the "not chibi" wording. ref06 measured 3.1 and its retry overshot to 5.5. Final 3.3-5.5.
  - Owner ratings (`benchmarks/batch_review.py`, 1-5): **face 4.0, overall 3.7** on average. Top: ref08 5/5
    (4.18 heads) and ref10 5/4 (4.42). Lowest: ref02 2/4 (3.31) and ref09 3/4 (3.70).
  - Tendencies: about 4.2-5.0 heads rates best and the chibi side lowest; busy compositions (big hair volume,
    heavy dark uniforms, props across the torso, wide poses) rate lower; the two faces rated 3 are three-quarter
    faces narrowed by long dark hair.
- **Tuned from the ratings:**
  - target `heads_tall` 4.5 (was 4.6) and `heads_range` 4.0-5.0 (was 3.2-5.2);
  - softer wording ("a stylised game figure between chibi and realistic") in place of "NOT a chibi... a head of a
    third of the height or more is too big", which pushed first attempts to 5.3-6.4;
  - corrections in proportion to the miss ("a little" under 0.9 heads off, otherwise "clearly"), after ref06's
    strong correction overshot from 3.1 to 5.5.
- **Five new concepts** (ref01/02/03/06/09, up to the proportion check, $1.14, `bench_runs_tune`):
  - All five ended in range (4.18-4.82) after at most one retry, and the proportional corrections did not
    overshoot.
  - First attempts swung the other way with the softer wording: 2.9-3.7 heads (too chibi), so 4 of 5 retried. The
    image model's first try follows the wording's emphasis ("not chibi": too tall; "between chibi and
    realistic": too chibi). The measure-and-correct step is what reliably lands the proportions (batch 9/10,
    here 5/5).
  - Expected cost per character is therefore ~$0.25 (one retry) rather than $0.14.
- **Animations in the reduce finish** (`anim.reduce_mode`):
  - The frames are the pose sheet's own reductions: `anim_traces` already shrinks every figure at one shared scale
    and maps it to the character's palette. In reduce mode it adds no outline ring and merges only near-identical
    specks; `anim_draw` passes the traces through instead of the model drawing each frame (~$0.33 per frame).
  - The pose sheet is asked for in the concept's smooth style, not pixel art (`prompts.POSE_SHEET_SOFT`).
  - A note at gate R goes to the pose sheet: it is redone once per new note and waits at gate P.
  - **First real walk** (ref08, 6 frames, `bench_runs_batch/assets/ref08-walk`): $0.06 in 78 s, against $1.46-1.95
    for the model-drawn walks. Frames are consistent and in the character's soft style. The motion is subtle (a
    kimono walk) and turned three-quarters to the side, as the sheet drew it.
- **Calmer concepts** (owner: eyes at a suitable size, not enlarged). The restyle and look prompts ask for eyes
  "of a natural size for the face (as the reference draws them)" and a calm, readable composition: a relaxed
  standing pose, arms close to the body, hair at a moderate volume close to the figure, props small and held close.
  The four lowest-rated references re-run ($0.81, `bench_runs_calm`): ref03's hair hugs the figure, ref09 is
  slimmer with a smaller plush, ref01 stands calmer (5.3 heads, kept after one retry), ref02 is similar.
- **Revising through the concept.** In the reduce finish, `Pipeline.request_fix` returns 'concept':
  - the approved concept is edited with the note (`prompts.REVISE_CONCEPT`);
  - an area fix sends a mask, takes back only that area into the old concept, keeps the previous grid (pixelize)
    and restores everything outside the area from the old sprite (`revise.confine`);
  - no second concept approval and no proportion retry; about $0.12-0.15 a revision. The review-gate rejection
    goes the same way.
- **App:** a Look setting (Soft = reduce finish, Classic = model-drawn finish with the face kit; `server.LOOKS`),
  estimates per look ($0.30 vs $1.50 a character), and the item page shows the measured proportions and any
  concept redo.

## 5. Direction (as proposed before implementation)

**Protect the face; draw only the body.**
- **Face from the reduction.** Take the face area (face materials within the face box) from the pixelize output.
  Optionally clean it lightly: merge isolated specks; keep it symmetric where the concept is front-facing.
- **Exempt the face from the palette step**, or give focal materials their own colours: base colour from the most
  saturated/lit cluster (not the average), a minimum of 3-4 steps.
- **The model must not repaint the face.** It draws hair, body and costume around a locked face layer. The face pass
  becomes optional cleanup (stray pixels, a lost catchlight, a hair strand over the face), not replacement.
- **The face kit stays** for small sprites (≤ 96 px), the classic look, and expression changes the concept does
  not have (animation frames, a different emotion).

**Match the target style's colour depth.** For the soft look, raise the colour budget (e.g. 64-128, with
anti-aliasing shades), or skip ramp quantization for skin, face and hair and only merge noise.

**Check each stage, not only the end.** Run `benchmarks/face_stages.py` on every benchmark run and track the face
drift per stage, so a step that damages the face shows up immediately.

**Risks and open questions.**
- The reduction face is soft when zoomed in. That suits the soft look, but a light sharpening pass may help.
- Hair crossing the face: the face mask must follow material labels, not a box.
- Animation frames need the face redrawn per pose, where the reduction is not available; the kit (or a face sheet
  from the image model) stays useful there.
- Cost: protecting the face should *lower* cost (no face pass, a shorter redraw).

---

## 6. Spend on the face work since the last commit

| Folder | Cost |
|---|---|
| `bench_runs_160` (four 160-px runs) | $4.16 |
| `bench_runs_soft` (six concepts, three soft-look drawings) | $4.13 |
| Face lab rounds r4, r5, r6 | $1.13 |
| **Total** | **$9.42** |

Nothing from this work is committed yet: the 160-px default, the soft look, face kit v4 with the measured layout,
and this analysis are uncommitted in `pixelagent` (last commit `841d9ea`) and `pixelliner-mcp` (last commit
`7768ace`).


---

## 7. Next direction: sprites designed as pixel art (2026-11-10)

The owner: an overly realistic recreation loses the pixel-art style. Making a standard illustration chibi and then
converting it is not the goal; the characters should follow how Alice in Cradle builds them. Noel's faces are not
detailed, but they have a vividness unique to pixel art, and so do her proportions and scale.

Side by side at 6× the difference is plain. Noel is built from crisp, deliberate clusters: hair as bright shapes with
drawn strands, each eye a distinct pixel shape. The reduce finish gives a soft blur, like a shrunk photo.

**Measured on Noel** (one standing frame, material map plus colour texture, scratchpad only):

| Rule | Noel |
|---|---|
| Proportions | ~4.7 heads tall at ~157 px; head ~32 px, face ~18 px, legs ~8 px wide |
| Colour | about 4-6 tone bands per material, as clusters |
| Edges | no dark outline; silhouette edge brightness median 225 of 255 |
| Face | a few designed pixel shapes; expression from lash and eye shape (~170 eye variants), a tiny mouth |

**Experiment** (`benchmarks/pixel_native.py`, `bench_runs_native`, $0.17 for three):
- The image model draws the character as an authentic pixel-art sprite from these rules, each sprite pixel an 8×8
  block. Grid recovery (period search, smallest near-best period, block centres read by their dominant colour)
  recovered synthetic tests at 6, 7.6, 8 and 9.3 px blocks pixel-exactly.
- **Result:** genuine pixel art with crisp clusters and pixel-designed faces (ref08's eyes: lash, red iris,
  highlight).
- **Open issues:** dark outlines and higher contrast than Noel's light, soft edges; proportions and size vary
  (ref02 ~6 heads, 174-186 px); ref05 drew grid lines (recovery locked onto them); the block reading leaves a few
  hundred colours, which needs a palette step.

**Built in as the native finish** (`[finish] mode = "native"`, the default; the app's Soft look):
- **Concept:** the image model draws the character as a hand-pixelled sprite from the reference or the plan
  (`prompts.PIXEL_SPRITE` / `PIXEL_SPRITE_TEXT`, rules in `PIXEL_RULES`: no grid lines, a light high-key palette,
  no dark outline, about 4.5 heads, a pixel-designed face). It always waits at gate B.
- **Pixelize:** `pixelart.sprite_from_pixel_art` reads it back. The block size comes from the autocorrelation of
  the edge profile within 0.7-1.45x of the 8 px asked for. A walk along each axis snaps every block edge to the
  nearest real edge, because an image model's "pixels" drift: real images had blocks of 8.3-10.2 px, and a fixed
  grid would drift by whole blocks. Each block takes its dominant colour; the background is cut by a flood fill
  from the border. The geometry uses the pipeline's convention, so material map, labels, face crops, the
  proportion check and area revisions work unchanged.
- **Rebuild:** 3-6 tones per material (`build.native_tones`, 6 for focal ones) through `reduce_material`, which
  keeps the darkest and lightest shades. No outline ring, no drawing; the critic gets `prompts.CRITIC_NATIVE`.
- **Revisions** edit the pixel-art concept unscaled (no blur) and keep the grid for area fixes.
- **Animations** (native): the pose sheet is drawn as pixel art on one shared block grid (`prompts.POSE_SHEET_PIXEL`).
  The block is as large as the sheet allows for the frame count and sprite size (`anim._sheet_block`). One block
  size is measured on the whole sheet, each frame is read back pixel for pixel on it, colours are mapped to the
  character's palette, and no speck merging is done (single pixels are deliberate).
- **Frame size** (`anim.read_pixel_frames`): the image model draws the walking figures a little bigger than the
  standing sprite. When the frames' median height is more than 3% off the sprite's, the sheet is read again on a
  proportionally larger regular grid (no edge snapping), so each 1x pixel takes its patch's dominant colour. The
  resize happens on the drawn high-resolution sheet, not on the finished frames. `anim_traces` info records
  `frame_size` (drawn and target height, ratio).
- **Grid fixes found on the way:**
  - the autocorrelation is detrended: it is high at every small lag, so the smallest period always won (a 3-px sheet
    read as 2 px);
  - each axis has its own block size: image models draw non-square blocks (9.4 × 11.0 px seen).
- **First real pixel-art walk** (ref05, 6 frames, $0.07 in 85 s): consistent pixel-art frames in the character's
  style with a real stepping motion, 168-174 px tall, about 6% taller than the 161-px standing sprite. Read again
  at the sprite's height (ratio 1.062): 160-164 px, the faces and hands stay clean.
- **Real run** (`bench_runs_native2`, ref08/02/05/03, $0.74): genuine pixel-art sprites with crisp clusters and
  pixel faces, 4.0-4.6 heads, 147-170 px, 30-56 colours. They are still darker and more saturated than Noel (dark
  hair and navy costumes), and the hair has darker edges in places.

**Validation batch with the native finish** (2026-11-11, all ten owner references, `bench_runs_native_batch`):
- **Runs:** 10/10 complete, $2.18 ($0.14-0.15 without a retry, ~$0.26 with one), 2.5-5 min each. All readbacks
  correct (blocks 8-11.6 px, sprites 127-154 px; ref07 186 px).
- **Owner ratings: overall 4.8, face 4.5** (the reduce batch: 3.7 and 4.0). Eight of ten rated 5 overall;
  ref02 2 -> 5, ref03 3 -> 5, ref09 3 -> 5. Lowest: ref01 4/3 (face) and ref10 4/4.
- **Open issues:**
  - ref10 kept the seated reference's bench as part of the sprite (the prompt asked for a standing pose).
  - The proportion check retried 6/10 and misread at least ref09 (2.07 heads: the plush toy against the face hides
    the chin) and ref10 (no face found).
- **Fixes** (2026-11-11, checked on ref10/09/02/04/07, $1.07, `bench_runs_native_fix`):
  - seated references now stand: "STANDING upright... even if the reference sits; NO furniture, bench, chair";
    ref10's bench is gone;
  - readings outside 2.5-7.5 heads count as not believable and are not retried;
  - the proportion retry is off for pixel-art sprites, with the measurement kept as information. On them the chin
    measurement slides into the neck: ref04 read 3.1 and 4.6 for practically the same proportions, ref10 2.0 when
    she is about 5 heads. Most retries in both pixel-art batches came from misreadings, and the image model's
    proportions looked right and were rated 5. A pixel-art character now costs ~$0.15 (the app quotes $0.20).

**Palette grade** (2026-11-11, `build.grade_colours`, `[finish] grade`, the app's Colours setting):
- **Measured** in OKLab on opaque pixels:
  - Alice in Cradle's characters (Noel and nine NPC atlases): mean chroma 0.013-0.059 (~0.033), 7-56% dark pixels
    (L < 0.45). Dark designs stay dark there too: the consistent difference is saturation, plus lifted shadows.
  - our ten native sprites: mean chroma 0.039-0.099 (0.063 on average), 1-50% dark.
  - the darkness comes from the image model's drawing, not from the tone rebuild (drawn and final lightness agree
    within 0.003).
- **The grade:** a palette mapping, so every cluster stays exactly as drawn. Chroma is scaled, and tones below a
  knee are lifted (0 at the knee, `lift` at black, order kept). Eyes, mouth and blush keep their drawn colours.
- **Presets:** soft (0.8 chroma, +0.08), medium (0.65, +0.14, default), strong (0.5, +0.2); none switches it off.
- **Result:** on eight sprites, medium moves mean chroma 0.063 -> 0.041 and dark pixels 32% -> 28%. The owner picked
  medium over soft and strong (strong greys the skin and reds). ref05 through the pipeline: chroma 0.078 -> 0.051,
  dark 43.6% -> 28.2%, still 37 colours. Animations inherit it: their frames map to the character's palette.

**Move set** (2026-11-11, the app's Move set; `server.MOVES`, `benchmarks/move_set.py`, `bench_runs_moves*`):
- **What it is:** idle (4 frames), walk (6), run (6), jump (6), attack (5) and hurt (3), each its own animation that
  stops for its poses to be approved. About $0.064-0.071 a move; $0.40 for all six.
- **First run** (ref05 after the palette grade): good poses, but sizes all over the place. Idle came out 1.34x the
  character, walk 1.25x and run "0.79x"; the size fix's believability cap left them alone. The run figures were
  drawn large and crowded, so the figure boxes cut them apart (pieces of neighbours in the frames).
- **Cause:** the pixel prompt called the first reference image "the character's pixel-art sprite", but the code
  passed the concept first. The model had no size anchor and filled the room it had.
- **Fixes:**
  - **Layout reference** (`anim.layout_sheet`): the first image is the finished sprite placed once per frame at
    the final pixel scale, evenly spaced on one ground line. The model redraws each copy in its pose. Idle, walk and
    run came back at 148-151, 143-146 and 122-133 px against a 148-px character, with clean gaps. Each costs the
    same as before.
  - **Frame cleanup** (`anim.clean_frame`): neighbour pieces entering at the crop's edge and far specks are
    dropped.
  - **Size fix:** it now only SHRINKS frames drawn too big (up to 1.4x), measured on the taller, upright half of
    the frames. Shorter readings are the pose itself: a run's tallest frames still lean, and enlarging them by 11%
    made the runner bigger than the character.

**Expressions** (2026-11-11, `pixelagent/expressions.py`, the app's Expressions card; `bench_runs_moves2/ref05`):
- **Method:** a masked edit of the pixel-art concept over the face only, read back on the character's own block
  grid, then pasted into the finished sprite inside the face box and inside the figure. Pixels the edit did not
  touch are kept exactly. Each face comes as a whole sprite and as a face patch with its box.
- **Finding the face:** the material labels can't do it. Only ref05 of the ten has an eye material, and "skin" is
  labelled on hands, a plush toy and lit hair; `face_layout` also misplaced 4 of 9. The vision model
  (`vision_pick`) on the large concept found all nine correctly for $0.013 in all.
- **Colours:** each drawn colour is graded like the character, then snapped to the sprite's own colour when one is
  within 0.03 OKLab. Otherwise its graded hue is kept. Snapping everything (and a looser 0.045) turned the happy
  face's pink tongue into skin and its purple lash arcs into the costume's blue.
- **First set** (ref05, six faces, $0.327, ~4 min): happy, sad, angry, surprised, hurt and blink all read clearly
  as pixel-art faces; 135-207 pixels changed each, hair and outline untouched. The redrawn faces are simpler and
  more frontal than the original three-quarter face.

## Validation round on four more characters (2026-11-12)

`bench_runs_validate`: ref01, ref03, ref08 and ref10 with today's colours, six moves and six faces each ($2.97, no
failures), on a rating sheet (`benchmarks/validation_round.py sheet`). The owner has not rated it; the findings below
are measured, and the fixes are in the code. The second round on the fixed code is at the end of this section.

**Moves that came out small.** Five of 24 moves were 74-89% of the character's height. Two causes:
- *Coarse pixels (the real defect).* A six-frame sheet at 1536 px gives a 57-px-wide sprite blocks of 3 image
  pixels. Asked for 3, the image model drew 4 on three of nine such sheets (3.6 on a fourth): the figure has the
  layout's size on the sheet, but only 3/4 of its pixels, and soft detail. The block grid shows it (drawn block /
  asked block = 1.33) together with the silhouette's area (0.76 of the sprite's).
- *The pose.* ref08's run, jump and attack are 83-89% tall on the asked grid (grid 1.0-1.03, area 0.92-0.93): a
  lean and a crouch. These are left alone.
- **Fixes** (`steps/anim.py`): `sheet_plan` takes a sheet twice as wide (`[animation] sheet_wide = "3072x1024"`)
  when the blocks would be under `min_block = 5`. ref01's run on it: 6-px blocks, frames 120 px for 127, far more
  detail, $0.059 (the normal sheet: $0.069). `too_coarse` (grid > 1.15 and area < 0.90) draws the sheet once
  more with a note and keeps the better one. What remains above grid 1.06 with area under 0.94 is read on the
  asked grid (`read_pixel_frames(..., sprite=)`): the right size, with some doubled pixels. Height alone still
  never enlarges a frame.

**A seated reference.** ref10 sits on a bench. Idle, attack and hurt stay seated; walk, run and jump stand up and
lose the bench. Open: the agent needs a standing base sprite (or to ask) before it animates a seated character.

**Faces.** The vision check on the large drawings failed 20 of 26 drawings of ref01, ref08 and ref10 (first attempts
and retries, most of them made with the first prompt). What it found:
- *Skin tone.* ref08's sad and hurt faces repainted the skin tan. `expressions.skin_fix` measures the shift of the
  face's main colour (the median of what was drawn in its place) and moves the drawn tones back; any pixel within
  0.02 OKLab of the original is the original pixel, because an image model never returns a colour exactly.
- *Iris colour.* Green eyes became red, brown or purple on most first attempts. The face locator now names the
  iris colour and the prompt states it (second round below).
- *Fringe.* Half of the faces reshaped the fringe. `expressions.hair_zone` takes the hair's colours from the rows
  above the face box and puts the fringe and side locks, followed from the box's edges, out of bounds.
- *The check and the retry.* `expressions.judge` shows the vision model the neutral and the new face as crops of
  the large drawings and asks four things: reads as asked, same skin, same hair, same eye style. On the 1x faces it
  misjudged eye colours and failed good faces (ref01's angry retry kept its green eyes and was failed); on the large
  drawings its findings matched the drawings compared by eye (ref01's angry, surprised and smug). A failed face is
  drawn once more with the finding as a note. Trials: 1 of 7 hard faces passed with the check on the 1x faces, 3 of
  4 with the check on the large drawings and the fringe out of bounds.
- **Your own expression:** `make(..., custom={"smug": "a smug half-smile with one brow raised"})`, the app's "Your
  own" field. ref01's smug face passed on its second attempt.
- Cost: $0.06 a face, $0.12 when it is drawn twice. In the last trial 3 of 4 first attempts failed, all three on
  the iris colour, which the named colour is meant to prevent.

**Second round on the fixed code** (`bench_runs_validate2`, 2026-11-12; rating sheet `rating.html`, 49 items, not yet
rated by the owner):
- *Moves* (ref01, ref03, ref08, ref06; 24 moves, $1.48, $0.062 each). Every move of five or more frames took the
  wide sheet. Silhouette scale 0.91-1.04 on all 24 (first round: 0.74-1.00); six were read back a little larger
  (grid 1.07-1.15), two a little smaller. One sheet came out coarse (ref08's run, grid 1.32), was drawn again and
  the second kept (grid 1.12, area 0.97). By eye the figures match the sprites' size and design on all four.
- *Touching figures.* ref08's attack had a figure cut in two: three figures touched through a held pouch, and the
  cutter halved the merged piece. `imaging.split_sheet` now cuts a merged piece at its thinnest columns near where
  a figure of the usual width ends. The stored sheet, cut again, gives five whole frames.
- *Faces* (ref01, ref03, ref08, ref10; 25 faces, $2.46, about $0.10 each because 19 were drawn twice). 13 of 25
  passed the check: ref01 5 of 6, ref10 5 of 6, ref03 3 of 7, ref08 0 of 6. By eye: ref01 and ref10 keep their
  green and amber eyes in the open-eyed faces (red, brown or blank white in the first round) and their fringes;
  ref03's angry, surprised and smug eyes are a stronger blue than its grey-blue original; ref08, a three-quarter
  face under a heavy side-swept fringe, has its fringe reshaped in every face.
- *Naming the iris colour* helps where the name is exact (green, amber) and hurts where it is not: the locator
  called ref03's grey-blue eyes "blue" and the edits painted them blue. The prompt now says the irises keep the
  colours of the original image, with the name as a hint and "not brighter, not more saturated" (untested).
- *A bug in the first attempt at this round.* `validation_round.py` ran moves and faces side by side on one Project,
  and both changed `[images] draft_size` on its shared settings: 23 of 25 faces were drawn at the pose sheet's
  size (3072x1024 for a 1024x1536 concept) and came back squeezed ($2.25 lost). `images.generate` now takes the
  size with each call; the animation and expression steps no longer touch the shared setting, and the script loads
  the project once per job. The app already loads the project per job.
- *The face box is kept.* Adding ref03's smug face located the face again, one pixel off, and dropped the six
  faces made before from `expressions.json`. `make` now keeps the stored box (`relocate=True` starts afresh).
- **Open:** three-quarter faces under a fringe (ref08); the seated reference (ref10's moves); text-only characters.

**The owner's ratings of the second round** (2026-11-12, 1-5; `benchmarks/validation_ratings.csv`):

| | Faces | Moves |
|---|---|---|
| ref01 | 4.17 (6) | 2.2 (5), "pose wrong" on attack, run, walk and hurt |
| ref03 | 4.57 (7, with the described "smug" face at 4) | 4.0 (5) |
| ref06 | | 4.0 (6) |
| ref08 | 4.5 (6) | 4.0 (5) |
| ref10 | 4.0 (6) | |
| All | **4.32** (25), lowest 3 | **3.57** (21), lowest 2 |

- **Faces meet the bar** set for character generation (average at least 4, nothing below 3) on these four.
- **The automatic face check does not follow the owner's ratings.** The 12 faces it failed average 4.5; the 13 it
  passed average 4.15. ref08, failed six times out of six for its fringe, is rated 4.5; ref03's "bluer" eyes are
  rated 5, 5 and 4. Its hair and eye-colour findings are stricter than the owner, and each one costs a second
  drawing. It confirms the 2026-11-10 finding: faces are judged by the owner, not by a model.
- **Moves are uneven.** Three characters are at 4.0. ref01, drawn facing the viewer and waving, is at 2.2 with
  "pose wrong": a side-view walk or run cannot come from a front-facing sprite without redesigning the figure.
- The ratings of the hurt MOVE of ref01, ref03 and ref08 are missing: the sheet gave the hurt move and the hurt
  face one key, so rating one cleared the other. Fixed (`validation_round.py`, keys carry the kind).
- **The owner's view after rating** (2026-11-12): animations made from one static illustration may not be worth
  having; a few expressions may be enough.

**The face check, calibrated to the owner** (2026-11-12, `expressions.passes`, `[expressions] fail_on`): only "does
not read" and "the skin changed" (the vision finding or the measured share of skin kept) fail a face and cost a
second drawing. The hair and eye-colour findings are recorded and shown as a note. Of the 19 faces of the second
round with a stored check, 9 failed under the old rule and none fails under the new one. Not yet run with the real
models: the saving (a face back from about $0.10 to $0.06) is the expected one.
