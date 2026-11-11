# pixelagent

Reference image or concept prompt → layered, palette-clean pixel-art sprite, built on the PixelLiner MCP
engine (`~/pixelliner-mcp`). Output opens in PixelLiner for final touch-up.

**Status:** characters (prompt or reference image) and prompt-driven animations work end to end with OpenAI
models, drawn by the model through MCP; a desktop app (`PixelAgent.exe`) covers the whole flow. Every step also
runs with stub models (`--dry-run` / Test run, no API calls, no cost). Next steps and the full picture for a new
developer: [`docs/HANDOFF.md`](docs/HANDOFF.md).

## API key (OpenAI mode)
Set it yourself as a user environment variable, never in a file:
```powershell
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "<your key>", "User")
```
pixelagent reads it from the Windows user environment (so a rotated key works without restarting apps),
falling back to the process environment. It is never written to config, logs, cost records or review pages.
Spending caps: `[budget] max_usd_per_asset` and `monthly_usd` (set to $90 for the Pro $100 credit).
Image-model prices in `[pricing]` are unverified third-party figures; `costs.jsonl` keeps raw token counts.

## The app (PixelAgent.exe)
A local desktop app over the same pipeline: a character library, a page per character with its progress, and
the approval steps (check the plan, pick a concept, approve or redraw the sprite, plan and approve animations).
It runs only on your computer (a small server on 127.0.0.1:8765, shown in an Edge app window) and quits by
itself a few minutes after its window closes, unless something is still being drawn.
```powershell
python -m pixelagent.app                                   # from source
powershell -ExecutionPolicy Bypass -File packaging\build.ps1   # build dist\PixelAgent.exe (needs PyInstaller)
```
The exe bundles the PixelLiner engine and serves it to the artist with `PixelAgent.exe --mcp-server`.
Settings (mode, default height, monthly limit, projects folder, default `Documents\PixelAgent`) live in
`~/.pixelagent/settings.json`; Settings → Mode → Test run uses the stub models at no cost. Errors from the
windowed exe go to `~/.pixelagent/app.log`. The API key can be saved from Settings (user environment only).

## Quick start (dry run)
```
cd ~/pixelagent
python -m pixelagent --project D:/pixel_projects/mygame new hero --ref path/to/reference.png --height 64
python -m pixelagent --project D:/pixel_projects/mygame run hero --dry-run
#   stops at gate A: open assets/hero/review/index.html, optionally edit spec.json, then:
python -m pixelagent --project D:/pixel_projects/mygame approve hero
#   (gate B appears only when there are several concepts: approve hero --pick 2)
#   gate C: approve the sprite, or reject with a note
python -m pixelagent --project D:/pixel_projects/mygame export hero --open     # opens the PNG in PixelLiner
```
`run --auto-approve` answers every gate with the suggested choice; `run --from lines` reruns from a step
(older versions are kept); `status` lists every step.

## Pipeline
| # | Step | Kind | Main output |
|---|---|---|---|
| 1 | intake | model (director) → **gate A** | `spec.json` (CharacterSpec; edit before approving) |
| 2 | concept | reference cleanup / image model → **gate B** | `concept.png` (background removed, cropped); a reference with scenery or a partial figure is redrawn as a clean full-body concept and waits for approval |
| 3 | matmap | image model + checks | flat key-colour material map of the concept (silhouette IoU ≥ 0.85) |
| 4 | pixelize | code + model pick | grid-alignment candidates → `pixel_base.png` |
| 5 | materials | code + model labels | colour clusters → materials → colour families → hue-shifted ramps within the colour budget |
| 6 | rebuild | code | per-material layers, shading transferred from the concept (relative lightness) |
| 7 | lines | code | pixel-perfect outline + sel-out, in a `lines` overlay layer |
| 8 | detail | agent | only in `[finish] mode = "repair"`; skipped in the default artist mode |
| 9 | polish | code | AA restricted to the two meeting ramps, stray-pixel cleanup → the **trace** |
| 10 | artist | agent via MCP | the model draws the final sprite over the trace, then a face pass |
| 11 | critique | lint + model critic | `lint.json`, `critique.json` (evaluation only) |
| 12 | review | **gate C** | approve, or reject with a note to redraw from the artist step |
| 13 | animate | — | always skipped: animations are their own assets (see Animation) |
| 14 | export | code | `<name>.png`, `_x4.png`, per-layer PNGs, engine project, `meta.json` (palette, pivot) |

Every step writes `assets/<id>/steps/NN_step/vNNN/`; nothing is overwritten. Each model call is logged to
`costs.jsonl`, and `[budget] max_usd_per_asset` stops a run that exceeds it.

## Quality mechanisms (from the first benchmark of 5 characters)
Each addresses a root cause found in the data, not a single example:

| Root cause | Mechanism | Where |
|---|---|---|
| Independent ramp per material: 11-16 materials vs 32 colours left ~2 shades per material | **Colour families** (materials of similar hue + relative saturation share a ramp; only true greys share regardless of tint; focal materials keep their own) and **area-weighted steps** (each extra shade goes where it removes the most quantisation error) | `steps/materials.py: build_palette` |
| Contrast lost to count-weighted levels | **Range-preserving levels**: end steps at the 1st/99th lightness percentile, k-means only for interior steps | `art.lightness_levels`, `art.ramp_for_levels` |
| Per-pixel colour decisions (shading, sel-out) produce speckle | **Amplitude-aware coherence**: same-material 3x3 median for low-amplitude texture only; merge small same-shade clusters (4-connected for areas, 8-connected for outlines) only between near shades, so glints and crease lines survive | `art.smooth_lightness`, `art.merge_small_clusters`, `steps/build.py` |
| Edits judged only by a noisy critic (which once asked to grey out a shield the concept has) | **Concept anchor**: candidates are compared with the concept on the sprite grid after a 3x3 blur (tolerates crisp detail, catches deleted/changed colours; calibrated: good edits +0.002..0.039, deleting an emblem +0.069 -> tolerance 0.05); critic told the concept is ground truth | `art.fidelity`, `regions.rewrite_region` |
| Fixed compute regardless of value | **One call for all variants**, fewer variants in repairs, rounds kept only if quality rises by more than the critic's noise (min_gain), failed regions not retried | `regions.py`, `steps/critique.py` |

Measured on the same 5 concepts (v1 = before, v3 = after): speck pixels 925 -> 496, neighbour shade changes
0.56 -> 0.48 (mean), distance to concept 0.050 -> 0.041, model calls after pixelize 210 -> 99, cost after
pixelize $1.96 -> $1.09; critic scores equal within its noise except the archer (7.3 -> 6.5) and mushroom
(6.9 -> 7.2). `benchmarks/compare_rebuild.py` and `benchmarks/run.py --rerun-from STEP --source DIR` reproduce
such comparisons.

## Artist mode: the model draws the sprite (default finish)
The final sprite is drawn by the model itself through the pixelliner MCP tools (a real MCP client talks to the
engine over stdio), not converted:

1. The conversion steps (concept -> material map -> pixelize -> materials -> rebuild -> lines -> polish) produce a
   palette-clean **trace** on the final pixel grid.
2. Step `artist` gives a vision model (`[models] artist`) the concept (design authority), the trace as a faint
   tracing layer (never exported, cannot be converted into pixels) and the trace's palette with single-character
   keys. It redraws the sprite on its own layers with `draw`/`pixel_map`, reads the trace exactly via
   `view` grid (palette keys), checks with `view`, `compare_reference` and `lint`, and calls `finish`.
3. **Face pass**: the face is located from the material labels (skin/eyes/mouth/blush marks grouped together; skipped
   for helmets), and the model gets the sprite and trace zoomed on it plus a high-resolution crop of the concept's
   face, with size-specific pixel-art face rules.
4. The critique then only evaluates (no automatic rewrites over the drawing); export takes the drawing.

`[finish] mode = "repair"` restores the older region-rewrite + repair path. Standalone use:
`python -m pixelagent --project P artist ID --ref concept.png --height 64` (pure agent, tracing the reference).

Measured on the 5 benchmark characters: at 64 px the hybrid costs $0.35-0.44 and 2-3 min per sprite for the drawing
(upper bound; ~90% of input is cached context), fewest specks of all variants; the face pass ($0.05-0.08) gave modest
gains because a 64-px face is only ~8x10 px. 96 px gave faces 9-13 px wide on anime references, still too few
pixels for natural expressions. **Default is now 160 px** ([style] sprite_height), the scale of hand-made anime
sprites (Alice in Cradle: ~150 px tall, faces 16-18 px): faces are ~16 px wide and the face kit's eyes and soft
mouths read naturally. A real 160-px run cost about the same as at 96 px ($0.62 for drawing + face pass + critique
with the concept reused, 4 min). **The default look is "soft"** (`[style] look`): characters are restyled toward
hand-made anime action-game sprites (Alice in Cradle as the reference). That means ~4.6 heads tall with a large head
and face, light low-contrast colours, soft coloured outlines and a simplified costume; `look = "classic"` keeps the
concept's proportions and a dark outline. Use `--height` (CLI `new`, benchmark runners) for other sizes, or game-style
head proportions in the concept prompt if a smaller sprite is required. Model judges are unreliable for faces this small (pairwise judging with swapped
order showed position bias), so faces should be judged by eye. Tools: `benchmarks/artist_run.py`
(`--hybrid`, `--agent-tag`), `benchmarks/face_pass.py`, `pixelagent/judge.py`.

## Animation (M3): driven by your prompt
```
python -m pixelagent --project P animate CHARACTER walk --prompt "6-frame walk cycle in place, the backpack bobs" --frames 6 --backend openai
python -m pixelagent --project P approve CHARACTER-walk     # gate P: pose sheet  (or: reject ... --note "longer strides")
python -m pixelagent --project P approve CHARACTER-walk     # gate R: animation   (reject --note redraws the frames)
```
An animation is its own asset, linked to a finished character. Steps:
1. `anim_plan`: the director turns the prompt into frames, poses, timing, loop/in-place and what must stay identical.
2. `anim_poses`: the image model draws all frames in ONE pose sheet (consistency), from the concept and the approved
   sprite; the sheet is cut into figures. **Gate P.**
3. `anim_traces`: figures converted at ONE shared scale (a crouch stays lower), on a common ground line, mapped to the
   character's palette with its outline.
4. `anim_draw`: the artist agent draws frame by frame through MCP, one fresh conversation per frame; each frame
   starts from the previous frame's drawing (frame 1 from the approved sprite), with its trace underneath, onion skin
   and the approved sprite as model sheet. It changes only what moves. Frame-consistency lint afterwards.
5. `anim_review`: **gate R**; rejecting with a note redraws the frames with that note (the pose sheet is kept).
6. `anim_export`: GIF, sprite sheet (one row), per-frame PNGs, layered project, meta.json (durations, poses, loop, pivot).

First real run (Mushroom Traveler, 96 px, 6-frame walk): plan + pose sheet $0.07, drawing $1.46 (upper bound,
69 rounds); identical 25-colour palette in all frames, opaque-area spread 3.3 %.

With the pixel-art (Soft) look there is no drawing step: the pose sheet is drawn as pixel art on a layout of the
finished sprite and each frame is read back from it (~$0.07 an animation). **Move set** (the app's button, or
`benchmarks/move_set.py`): idle, walk, run, jump, attack and hurt at once, each its own animation (~$0.40 in all).

## Expressions
A finished pixel-art character gets other faces (happy, sad, angry, surprised, hurt, blink): the vision model finds
the face, the image model redraws only that area of the pixel-art concept, which is read back on the character's
own grid and graded like it. Only the face changes, pixel for pixel; each comes as a whole sprite and as a face patch
with its box (`expressions/expressions.json`) for games that swap faces. About $0.055 a face. In the app: the
character's Expressions card; in code: `pixelagent.expressions.make(project, id, names)`.

## Files
```
config.toml            engine path, backend mode, model per role, budgets, style guide
pixelagent/
  cli.py               commands
  pipeline.py          runner: steps, gates, versions, resume, rerun-from
  steps/               intake, concept, matmap, pixelize, materials, build (rebuild/lines/polish), detail,
                       artist_step, critique, anim (animation pipeline), later, export
  models.py            backend interfaces + dry-run stubs
  openai_backend.py    OpenAI Responses (structured outputs, tool loops) and Images API, cost logging
  agent_session.py     tool loop shared by the artist and the animator
  artist.py            the artist agent: draws through MCP, face pass
  mcp_client.py        MCP client over stdio for the pixelliner engine
  regions.py, judge.py region rewrites (repair mode), pairwise judging
  prompts.py           model instructions
  schemas.py           CharacterSpec, Pick, MaterialLabels, Critique, state (pydantic)
  imaging.py           background removal, linear-light pixelize with grid offsets, contact sheets
  store.py             project / asset folders, versions, cost log
  review.py            HTML review page
  synth.py             procedural stand-in concepts for dry runs
  app/                 desktop app: server.py (local JSON API) + static/ (UI)
packaging/             PyInstaller spec, build.ps1, icon
benchmarks/            cases.csv, refs/, ratings.csv, run.py, artist_run.py, face_pass.py
docs/HANDOFF.md        status, decisions and remaining work
tests/                 python -m unittest discover -s tests -v
```

## Engine additions made for M0 (in ~/pixelliner-mcp)
`project.py` (layered project folders), `art.lint` / `art.lint_frames`, `art.luminance_transfer`,
`art.split_by_labels`, pair-restricted anti-aliasing, and MCP tools `save_project`, `load_project`, `lint`.

## Known limits of the dry run
The stub labeller groups colour clusters by hue, so it sometimes merges materials (e.g. hands with blond hair)
and small features like eyes can lose their colour. Real labelling (M1) and the detail pass (M2) are what fix
these; the dry run exists to exercise the plumbing.

## Still open
- About 10 reference images in `benchmarks/refs` to measure the reference path as well as the prompt path.
- The full prioritized list is in [`docs/HANDOFF.md`](docs/HANDOFF.md#13-remaining-work-in-priority-order).
