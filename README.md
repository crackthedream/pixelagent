# pixelagent

Reference image or concept prompt → layered, palette-clean pixel-art sprite, built on the PixelLiner MCP
engine (`~/pixelliner-mcp`). Output opens in PixelLiner for final touch-up.

**Status: M1 in progress.** The pipeline runs end to end with stub models (`--dry-run`, no API calls) or
with OpenAI models (`[backend] mode = "openai"`). Detail/repair agents arrive in M2, animation in M3.

## API key (OpenAI mode)
Set it yourself as a user environment variable, never in a file:
```powershell
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "<your key>", "User")
```
pixelagent reads it from the Windows user environment (so a rotated key works without restarting apps),
falling back to the process environment. It is never written to config, logs, cost records or review pages.
Spending caps: `[budget] max_usd_per_asset` and `monthly_usd` (set to $90 for the Pro $100 credit).
Image-model prices in `[pricing]` are unverified third-party figures; `costs.jsonl` keeps raw token counts.

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
| 2 | concept | reference cleanup / image model → **gate B** | `concept.png` (background removed, cropped) |
| 3 | pixelize | code + model pick | grid-alignment candidates → `pixel_base.png` |
| 4 | materials | code + model labels | colour clusters → materials → hue-shifted ramps within the colour budget |
| 5 | rebuild | code | per-material layers, shading transferred from the concept (relative lightness) |
| 6 | lines | code | pixel-perfect outline + sel-out, in a `lines` overlay layer |
| 7 | detail | agent (M2) | skipped in M0 |
| 8 | polish | code | AA restricted to the two meeting ramps, stray-pixel cleanup, `polish` overlay layer |
| 9 | critique | lint + model critic | `lint.json`, `critique.json` (repair loop is M2) |
| 10 | review | **gate C** | you approve or reject |
| 11 | animate | M3 | skipped in M0 |
| 12 | export | code | `<name>.png`, `_x4.png`, per-layer PNGs, engine project, `meta.json` (palette, pivot, frames) |

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
gains because a 64-px face is only ~8x10 px. **Default is therefore 96 px** ([style] sprite_height): faces are
13-17 px wide and read clearly (eyes, mouths, goggles), the drawing costs $0.80-0.95 and 4-5 min (upper bound), a
full character from a prompt ~$1.10. Use `--height` (CLI `new`, benchmark runners) for other sizes, or game-style
head proportions in the concept prompt if a smaller sprite is required. Model judges are unreliable for faces this small (pairwise judging with swapped
order showed position bias), so faces should be judged by eye. Tools: `benchmarks/artist_run.py`
(`--hybrid`, `--agent-tag`), `benchmarks/face_pass.py`, `pixelagent/judge.py`.

## Files
```
config.toml            engine path, backend mode, model per role, budgets, style guide
pixelagent/
  cli.py               commands
  pipeline.py          runner: steps, gates, versions, resume, rerun-from
  steps/               intake, concept, pixelize, materials, build (rebuild/lines/polish), critique, later, export
  models.py            backend interfaces + dry-run stubs (OpenAI backend: M1)
  prompts.py           model instructions
  schemas.py           CharacterSpec, Pick, MaterialLabels, Critique, state (pydantic)
  imaging.py           background removal, linear-light pixelize with grid offsets, contact sheets
  store.py             project / asset folders, versions, cost log
  review.py            HTML review page
  synth.py             procedural stand-in concepts for dry runs
benchmarks/            cases.csv, refs/, ratings.csv, run.py
tests/                 python -m unittest discover -s tests -v
```

## Engine additions made for M0 (in ~/pixelliner-mcp)
`project.py` (layered project folders), `art.lint` / `art.lint_frames`, `art.luminance_transfer`,
`art.split_by_labels`, pair-restricted anti-aliasing, and MCP tools `save_project`, `load_project`, `lint`.

## Known limits of the dry run
The stub labeller groups colour clusters by hue, so it sometimes merges materials (e.g. hands with blond hair)
and small features like eyes can lose their colour. Real labelling (M1) and the detail pass (M2) are what fix
these; the dry run exists to exercise the plumbing.

## Before M1
- Your Pro tier (sets monthly API credit: $100 / $200 / $500), and the OpenAI API org linked to it.
- Typical sprite height(s).
- ~10 references + ~10 prompts in `benchmarks/` (see benchmarks/README.md).
