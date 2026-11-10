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
