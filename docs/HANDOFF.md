# PixelAgent: development status and remaining work

A handoff document for an AI developer (GPT) continuing this project. It describes what exists, how it works,
what has been measured, which decisions the owner has made, and what is left to build, in priority order.
Status as of 2026-11-11 (the native pixel-art finish, palette grade, move sets, expressions and game packs: section
13; earlier status from 2026-11-09 below where not superseded; see `git log` of `crackthedream/pixelagent`
and `7801b3e` of `crackthedream/pixelliner-mcp`, both private GitHub repositories).

---

## 1. Read this first: rules for whoever continues

These come from the owner and from incidents during development. Treat them as requirements.

1. **The API key never goes into a file, log, prompt, commit or chat message.** It is read only from the
   `OPENAI_API_KEY` user environment variable (Windows `HKCU\Environment`), falling back to the process
   environment (`pixelagent/openai_backend.py: find_api_key`). The app can save it to the user environment from
   Settings, nowhere else. If you ever see a key in plain text, tell the owner to rotate it.
2. **Do not modify PixelLiner itself.** `C:\Program Files\PixelLiner` is a closed Adobe AIR application. This
   project only writes files that PixelLiner can open (PNG, GIF) and launches it on them. All drawing happens in
   the separate headless engine (`pixelliner-mcp`).
3. **How pixels are made (changed 2026-11-10).** The owner first asked for a model that *draws* with the
   pixelliner tools through MCP (the artist finish, now the app's Classic look). After the face root-cause
   analysis, the owner chose the **native finish** as the default: the image model draws the character and
   its animation sheets as pixel art, read back pixel for pixel. It was rated 4.8 against 3.7, with faces 4.5
   against 4.0. The aim is the look of Alice in Cradle's characters: pixel-art vividness, not a chibi
   conversion of an illustration. Keep the artist finish working, but improve the native one.
4. **Fix root causes, not examples.** When a test character looks wrong, find the mechanism that fails for a
   whole class of inputs, fix that, and measure it on all benchmark characters (see section 7). Do not tune for
   one sample.
5. **Money.** The owner has ChatGPT Pro at $100/month, which includes $100 of API credit. Caps: `$3` per asset,
   `$90` per month (`config.toml [budget]`), `$1.5` for the artist step, `$4` for drawing one animation. Use
   the dry-run backend (no API calls, zero cost) for every plumbing test. Ask the owner before any real run
   that is not part of what they asked for.
6. **Animation is driven by the user's prompt** (frames, motion, timing come from the text), not by fixed
   templates.
7. **UI style: Apple-like** (macOS app conventions, see section 10). The owner asked for high aesthetic quality.
8. **Git:** commit or push only when the owner asks. Scan for secrets before every commit. Commits have gone to
   `main` so far.
9. **Deleting files:** never hard-delete the owner's work. In the app, deleting moves work to **Recently Deleted**
   (`<projects folder>/.Recently Deleted`, `pixelagent/trash.py`), where it can be restored; only the owner's
   explicit, confirmed Delete Now / Delete All removes files for good. (The Windows Recycle Bin is not used for
   this: with FOF_ALLOWUNDO it silently deletes permanently when recycling is unavailable; review finding F01.)
10. **Downloads/installs** (packages, tools) need the owner's permission first: say the name, the source and the size.
11. **Environment quirks:** Windows 11, Python 3.13 at `D:\python\Python313`. The user folder contains non-ASCII
    characters (`C:\Users\孙炜`), so always open files with `encoding="utf-8"` and pass paths as arguments. Git
    Bash heredocs can mangle `\\n` escapes, so write scripts to files instead. `pytest` is not installed; tests use
    `unittest`.

---

## 2. Goal

Generate **game-quality pixel-art characters and animations** from either a text prompt or a reference image,
with the level of refinement seen in games like *Alice in Cradle* (whose author also made PixelLiner). The
output must be layered, palette-clean and openable in PixelLiner for final touch-up. The owner's priorities:

- **Characters first**, at the highest achievable detail. Props/objects and scenes are also required later.
- **Inputs:** detailed concept prompts or character reference images.
- **Animation sequences** for those characters.
- **A desktop app (.exe)** with a polished, Apple-style UI, so the tool can be used without the command line.

---

## 3. System overview

```
                      ┌──────────────────────────── PixelAgent.exe / python -m pixelagent.app ─────────────────┐
 user ── Edge app ──► │ app/server.py  (stdlib HTTP, 127.0.0.1:8765, JSON API, one background run per asset)   │
 window   (UI: app/   │        │                                                                               │
          static/)    │        ▼                                                                               │
                      │ pipeline.py ── steps/* ── models (OpenAI Responses + Images API, or dry-run stubs)     │
                      │        │                         │                                                     │
                      │        │                         └─ artist agent ──MCP (stdio JSON-RPC)──► engine      │
                      │        ▼                                                    (PixelAgent.exe            │
                      │ store.py: <project>/assets/<id>/steps/NN_step/vNNN/  +  costs.jsonl    --mcp-server)  │
                      └────────────────────────────────────────────────────────────────────────────────────────┘
                                                   │ export (PNG/GIF/sheet/layers)
                                                   ▼
                                    PixelLiner.exe (opened on the exported file; never modified)
```

Two repositories:

| Repo | Path | What |
|---|---|---|
| `pixelliner-mcp` | `~/pixelliner-mcp` | Headless pixel-art engine + MCP server. Standard library only (own PNG/GIF encoders). |
| `pixelagent` | `~/pixelagent` | The agent: pipeline, models, quality mechanisms, artist agent, animation, desktop app, packaging. |

Size: about 9,000 lines of Python plus the web UI (HTML/CSS/JS, about 60 KB).

---

## 4. The engine: `pixelliner-mcp`

An MCP server over stdio (newline-delimited JSON-RPC 2.0, protocol `2025-06-18`), usable by any MCP host
(Claude Code is registered with it) and by pixelagent's own `MCPClient`.

| File | Role |
|---|---|
| `server.py` | MCP server: 17 tools + the `pixel_art_workflow` prompt |
| `canvas.py` | Canvas with layers and frames, undo/redo |
| `art.py` | Pixel-art algorithms: OKLab/OKLCh colour, hue-shifted ramps, lightness levels, smoothing, cluster merge, outline/sel-out, pair-restricted anti-aliasing, lint, frame lint, luminance transfer, fidelity |
| `png.py`, `gif.py` | Encoders (no Pillow dependency) |
| `project.py` | Layered project folders (save/load) |
| `test_smoke.py`, `test_art.py` | Engine checks |

**Tools:** `new_canvas`, `open_image`, `canvas`, `palette`, `import_reference`, `draw` (batched ops including
`pixel_map` rows), `layer`, `frame`, `view` (image or exact `grid` with palette keys), `compare_reference`,
`lint`, `undo`, `redo`, `export` (PNG, GIF, sheet), `save_project`, `load_project`, `open_in_pixelliner`.

---

## 5. The pipeline: `pixelagent`

### 5.1 Character pipeline (14 steps, `pixelagent/steps/__init__.py`)

| # | Step | Kind | What it does |
|---|---|---|---|
| 1 | `intake` | director model → **gate A** | Prompt/reference → `CharacterSpec` (name, summary, view, must-keep features, palette hints…). User may edit it. |
| 2 | `concept` | image model → **gate B** | Concept drafts (4 by default); background removed, cropped. User picks one (or rejects with a note → new drafts). |
| 3 | `matmap` | image model + checks | Flat key-colour **material map** of the concept (segmentation), validated by silhouette IoU ≥ 0.85, unknown ≤ 15 %. |
| 4 | `pixelize` | code + vision pick | Linear-light downscale with sub-pixel grid offsets; a model picks the best grid candidate. |
| 5 | `materials` | code + labeller model | Colour clusters → named materials → colour families → hue-shifted ramps within the 32-colour budget. |
| 6 | `rebuild` | code | Per-material layers; shading transferred from the concept (relative lightness), coherence smoothing. |
| 7 | `lines` | code | Pixel-perfect outline + sel-out, separate layer. |
| 8 | `detail` | (skipped in artist mode) | Older region-rewrite detail pass; used only when `[finish] mode = "repair"`. |
| 9 | `polish` | code | AA restricted to the two meeting ramps; stray-pixel cleanup. Produces the **trace**. |
| 10 | `artist` | **agent via MCP** | The model draws the final sprite over the trace (section 8), then a face pass. |
| 11 | `critique` | lint + critic model | Evaluation only (scores, issues). |
| 12 | `review` | **gate C** | User approves, or rejects with a note → reruns from `artist` with the note in the prompt. |
| 13 | `animate` | placeholder | Always skipped; animations are separate assets (5.2). Its message still says "arrives in M3" (stale). |
| 14 | `export` | code | `<name>.png`, `_x4.png`, per-layer PNGs, engine project, `meta.json` (palette, pivot). |

### 5.2 Animation pipeline (6 steps; an animation is its own asset linked to a finished character)

| # | Step | What it does |
|---|---|---|
| 1 | `anim_plan` | Director turns the user's prompt into frames, poses, durations, loop/in-place, invariants. |
| 2 | `anim_poses` | Image model draws **all frames in one pose sheet** (for consistency) from the concept + approved sprite; sheet cut into figures. **Gate P.** |
| 3 | `anim_traces` | Figures converted at one shared scale on a common ground line, mapped to the character's palette and outline. |
| 4 | `anim_draw` | Artist agent draws frame by frame via MCP (fresh conversation per frame, each starting from the previous frame's drawing, with trace, onion skin and the approved sprite as model sheet); frame-consistency lint. |
| 5 | `anim_review` | **Gate R**; reject with a note redraws the frames (pose sheet kept). |
| 6 | `anim_export` | GIF (1× and 4×), one-row sprite sheet, per-frame PNGs, layered project, `meta.json` (durations, poses, loop, pivot). |

### 5.3 Storage, versions, resume

- `project/assets/<id>/state.json` holds inputs, step records (status, version, outputs, info) and gate decisions.
- Every run of a step writes a new `steps/NN_step/vNNN/`; nothing is overwritten. `run --from STEP` reruns.
- `state.json` is saved atomically: a temp file per writer thread, with a retry while Windows reports the
  file as in use (`store.py: _retry_locked`).
- `costs.jsonl` logs every model call (role, model, tokens, USD estimate). Budgets are enforced from it.
- `pipeline.py` resumes from the first unfinished step; gates stop the run until `decide()` records a choice.

### 5.4 Models (`config.toml [models]`, OpenAI, prices per 1M tokens in `[pricing]`)

| Role | Model | Notes |
|---|---|---|
| director, detail, repair, animator, **artist** | `gpt-6.1-sol` | $2 in / $10 out |
| vision_pick, labeler, critic | `gpt-6.1-terra` | $1 / $5 |
| final_critic | `gpt-6-astra` | $10 / $50 |
| utility | `gpt-6-luna` | $0.10 / $0.50 |
| image_draft / image_final | `gpt-image-2.6-flare` / `gpt-image-2.6-sunburst` | prices are third-party figures, **unverified** |

API usage: Responses API with structured outputs (`responses.parse` + pydantic, strict schemas) and tool loops
(`responses.create` with function tools and `previous_response_id`); Images API generate/edit.
`input_fidelity` is unsupported by `gpt-image-2.6-sunburst` (config option, empty by default).
`agent_session.py: AgentSession` is the tool loop shared by the artist and the animator.

---

## 6. Commands

```powershell
# app (from source) and exe build
python -m pixelagent.app                     # opens the app window; --port N, --no-browser, --quit-when-idle
powershell -ExecutionPolicy Bypass -File packaging\build.ps1     # → dist\PixelAgent.exe (needs PyInstaller)

# CLI
python -m pixelagent --project P new hero --ref ref.png --height 96     # or --prompt "..."
python -m pixelagent --project P run hero [--dry-run | --backend openai] [--from STEP] [--until STEP] [--auto-approve]
python -m pixelagent --project P approve hero [--pick N] [--note TEXT] [--stop]
python -m pixelagent --project P reject hero --note "make the eyes larger"     # redoes the gated step
python -m pixelagent --project P animate hero walk --prompt "6-frame walk cycle in place" --frames 6
python -m pixelagent --project P export hero --open           # opens the PNG in PixelLiner
python -m pixelagent --project P status hero

# tests (~140 tests, ~8 min; all dry-run, no cost)
python -m unittest discover -s tests

# benchmarks (real money in openai mode)
python benchmarks/run.py [--rerun-from STEP --source DIR] [--height 96] [--jobs N] [--only NAME]
python benchmarks/artist_run.py --hybrid [--agent-tag T]     benchmarks/face_pass.py     benchmarks/compare_rebuild.py
python -m pixelagent --project P artist ID --ref concept.png --height 64       # pure agent drawing, no pipeline
```

---

## 7. Quality mechanisms and measurements

Found from the first benchmark of five characters (Forest Archer, Mechanical Engineer, Apprentice Mage, Armored
Knight, Mushroom Traveler; `benchmarks/cases.csv`). Each fixes a root cause:

| Root cause | Mechanism | Where |
|---|---|---|
| One ramp per material: 11–16 materials vs 32 colours → ~2 shades each | **Colour families** (similar hue + relative saturation share a ramp; neutral threshold 0.035; focal materials keep their own) and **area-weighted steps** | `steps/materials.py: build_palette` |
| Count-weighted levels lose contrast | **Range-preserving levels** (ends at 1st/99th lightness percentile) | `art.lightness_levels`, `art.ramp_for_levels` |
| Per-pixel decisions → speckle | **Amplitude-aware smoothing**; merge small same-shade clusters (4-connected areas, 8-connected outlines) only between near shades | `art.smooth_lightness`, `art.merge_small_clusters` |
| Noisy critic as the only judge | **Concept anchor**: OKLab distance to the concept after a 3×3 blur, tolerance 0.05 (calibrated: good edits +0.002–0.039, deleting an emblem +0.069) | `art.fidelity`, `regions.py` |
| Fixed compute regardless of value | One call for all variants; rounds kept only above critic noise (`min_gain`) | `regions.py`, `steps/critique.py` |
| Model judges biased by position | Pairwise judging with swapped order | `pixelagent/judge.py` |

Measured before → after on the five concepts: speck pixels 925 → 496; neighbour shade changes 0.56 → 0.48;
distance to concept 0.050 → 0.041; model calls after pixelize 210 → 99; cost after pixelize $1.96 → $1.09.

---

## 8. Artist mode (the Classic look; the default until 2026-11-10) and faces

1. Steps 3–9 produce a palette-clean **trace** on the final pixel grid.
2. `artist` gives the vision model: the concept (design authority), the trace as a faint tracing layer (never
   exported), and the trace's palette as single-character keys. It draws on its own layers with
   `draw`/`pixel_map`, reads the trace exactly with `view` grid, checks with `view`, `compare_reference`
   and `lint`, then calls `finish`. Limits: `max_turns = 40`, `max_usd = 1.5`.
3. **Face pass** (`face_turns = 12`): the face region is found from material labels (names must start with a face
   word; features grouped with a gap of 3; helmets skipped), and the model gets the face zoomed plus a
   high-resolution crop of the concept's face with size-specific face rules.
4. On a gate-C rejection, the user's note is appended to the artist's first message.

Results on the five characters: the hybrid (agent drawing over the trace) beat both pure conversion and pure
agent drawing. At 64 px a face is only about 8×10 px and the face pass gained little, so **96 px became the
default** (faces 13–17 px wide). At 96 px the drawing costs $0.80–0.95 and takes 4–5 min (upper bound), and a
full character from a prompt costs about **$1.10**. Model judges are unreliable on faces this small, so judge
faces by eye.

**Face lab, round 1** (2026-11-09, `benchmarks/face_lab.py`, results in `benchmarks/face_lab_runs/r1/`). Diagnosis
on six real 96-px sprites: (1) the trace averages eyes, brows and mouth (1-3 px each) into skin, so the face pass
has no exact-size guide; (2) the face rules still said eyes are "usually 1 px wide" (from the 64-px era) and
allowed the outline colour for features; (3) the knight's face box had landed on his shield (already fixed by the
face-word rule). Tested on five characters, all starting from the same sprite with the face reset to the trace,
one sample each, rated blind by the owner (1st/2nd/3rd; $1.71 for 15 passes, ~$0.11 each):

| Variant | Mean place | Firsts | Won on |
|---|---|---|---|
| A: today's face pass (normal trace, rules v1) | **1.60** | 2/5, never last | mage, archer |
| B: face trace (`pixelagent/face.py`), rules v1 | 2.20 | 1/5 | Lantern Keeper |
| C: face trace + rules v2 (feature sizes from the face trace, colour rules) | 2.20 | 2/5 | engineer, mushroom |

Decision then: **keep A as the default**. Variants B and C (the face trace and rules v2) were removed later
(2026-11-09 cleanup); the face kit (rules v3) replaced this line of work.
Reading: C wins on big, high-contrast faces (a grin, chibi eyes) and loses on subtle anime faces, where the face
trace is ragged and misleads; with five characters and one sample each this is a hint, not a finding. A next round
would test C only for faces whose concept has strong features (measurable from the face trace's dark-cell share),
with two samples per variant to separate the method from run-to-run variance.

**First real run from the app/exe** (2026-11-09, "Lantern Keeper", prompt, 96 px): plan $0.02, 4 concepts
$0.15, drawing $0.88 (27 rounds), face pass $0.51 (9 rounds), critique $0.01: **$1.60** (upper bound: cached input
is counted at full price). The face pass was expensive because it continued the drawing conversation, re-sending
every earlier round and image; it now starts a fresh conversation as in the face-pass benchmark ($0.05-0.08), so a
96-px character should cost about $1.15. Result: clear silhouette, lantern and scarf read well; the face (eyes and
mouth merging into dark marks at 1x) is still the weak point, as the critic also said.

**Face kit redesign from Alice in Cradle** (2026-11-09). The owner found the v3 kit's eyes and mouths
structurally odd and pointed to the game. Its sprite atlases (Unity bundles; read locally, nothing copied into the
project) show one construction on every character (sprites about 150 px tall, faces 16-18 px wide):
- **Lash.** A strong dark lash on top, 4-6 px, its outer end softened into a wing that drops a pixel.
- **Iris.** It hangs under the lash as a vertical band (2-3 px wide, 3-4 tall): dark at the top, lighter at the
  bottom, one white highlight. A 1-px white of the eye shows beside it on the outer side. No lower outline, no box,
  no fade: the eye simply ends.
- **Size and layout** (measured over 261 Noel faces): eye blob about 6×5 px on a 17-px face, gap about one eye
  width, and the eyes sit low, about 5 px from the eye bottom to the chin.
- **Expressions** come from the lash: lowered (half), slanting up to the inner end (sad), slanting down to the inner
  end (angry), ^ arcs (happy), a soft line (closed), more white (surprised).
- **Brows, nose, mouth.** No brows (the fringe hides them) and no nose. Mouths are soft: none or a line just darker
  than the skin when calm, a small salmon blob when open, never a dark outline.

The kit (`pixelliner-mcp/face_kit.py`) now builds exactly that:
- eye sizes s 3×3, m 4×4, l 5×5, xl 6×6, with the default gap equal to the eye width;
- styles open, half, happy, closed, wide, sad, angry and winks;
- mouth colours mixed from the skin, which is sampled from the canvas or passed as `skin`;
- mouths none, line, smile, a round open smile, o, frown.

This took three passes. The first version (a lash turning down the outer corner, the iris fading into the skin)
still looked odd to the owner. A catalogue of all of Noel's faces showed what was missing: the eye white and the
vertical iris band.

**Third pass: a same-scale comparison with Noel** (owner: "the eyes still look quite odd"). The round-5 faces were
put next to Noel's at native pixels. The causes were in all three areas:

| Area | Noel | Ours (round 5) | Fix |
|---|---|---|---|
| Structure | Eye taller than wide; the lash continues down the outer side and encloses a soft white; pupil low, toward the nose | 4 wide × 3 visible; the white touched bare skin and read as a hole or a sideways glance | New templates (m 5×4, l 6×5, xl 7×6), exactly mirrored |
| Size and position | Eyes plus gap span almost the face width; eyes low under the fringe | The model chose size m for every 18–19-px face | `pixelagent/face.py face_layout` measures the face (skin width, fringe line, chin, centre) and the face pass gets the kit's size, gap and positions |
| Turned heads | The far eye of a three-quarter face is narrower | Two identical eyes on a turned head | `turn: left/right` narrows the far eye |
| Pixel work | Mid-tone iris, dark pupil, soft greyed lash ends, flat clean skin | Near-black lash beside pure white, bright flat irises (ref10 read as goggles), blotchy traced skin | Tinted white; the iris body is held to a mid tone with the light colour kept for the lower iris; new `skin` op flattens the face |

The model also added hard dark brows on two faces. Brows are now faint by default and the rules say to leave them
out. Free previews on the round-5 sprites showed the faces clearly closer to Noel.

**Full runs with the third-pass kit** (2026-11-10, owner-approved; `retrace_height.py`, ref01 / ref03 / ref05 at
160 px reusing their concepts). They cost $1.27 / $1.11 / $1.16, **$3.54 in total**: drawing 38-40 rounds, against
21 in the first 160-px run. Critique scores were 31.6 / 26.2 / 34.1. All three read as anime faces at game size and
are clearly better than the 96-px versions. The model used the measured layout, the `skin` op and `turn` on the
three-quarter faces. ref03 (a small three-quarter face) is the weakest. The face-only test rounds had started from
raw traces, which is why their faces looked pasted on: judge faces on full runs. The app's 160-px estimate was
raised to $1.50.

**The soft look: the whole character in the game's style** (2026-11-10). The owner still found the faces odd, and
named the real target: characters with the aesthetic of Alice in Cradle, with less effort in the body. Measured at
the same pixel scale:

| | Alice in Cradle (Noel, an NPC) | Ours, 160 px |
|---|---|---|
| Proportions | ~4.7 heads tall (157-px figure, 32-34-px head), large hair volume, small slim body | 5.5-7 heads, realistic adult bodies |
| Face width | 18-19 px, ~12% of the height | 13-16 px, ~8-10% |
| Brightness (mean luminance) | 193-211, few dark pixels | 95-161 |
| Lines | soft, coloured; no dark ring around the figure | a near-black sel-out ring (#1b1325) |
| Colours | hundreds of soft blended shades | 31-34 |

The proportions decide the face size: at 6-7 heads a face gets too few pixels whatever the face kit does.

`[style] look = "soft"` (the default; `"classic"` restores the old behaviour) sets the whole style:
- **Concept prompts** gain `prompts.LOOK_SOFT`, a description of the look that names no game or character, so the
  image model restyles rather than copies. It asks for ~4.6 heads tall, a large head and face with big eyes set
  low, a small slim body, light pastel low-contrast colours, soft coloured edges and a simplified costume.
- **References:** every reference is restyled. The redraw keeps identity (hair, eye colour, costume colours,
  signature accessories) but no longer the reference's proportions or every costume detail. The restyled concept
  comes first and the reference stays a candidate at gate B.
- **Spec:** `heads_tall` is set from `[style] heads_tall = 4.6`, editable at gate A; `keep_proportions` in the
  inputs opts out.
- **Outline:** a new `outline = "soft"` mode draws a gentler sel-out (0.2) from the materials' own shades. The
  near-black outline colour is not offered, and leftover ring pixels get a darker neighbour shade
  (`build._no_black_ring`).
- **Palette and drawing:** `max_colors` went from 32 to 40. The drawing instructions gain `artist.LOOK_RULES["soft"]`:
  light colours, gentle shading, outlines in a darker shade of the colour they enclose, a large face, and small
  details simplified.

**Concept test** (owner-approved, $0.44 for six concepts at $0.074 each; `bench_runs_soft`, ref01 / ref03 / ref05).
- **First prompt:** the style text came after "keep the identity". ref01 restyled strongly but came out pixelated;
  ref03 kept its realistic ~7 heads and ref05 changed only a little. An image edit anchors strongly on the
  reference's proportions.
- **Fix:** `prompts.RESTYLE_REFERENCE` (the soft look's redraw) puts the proportions FIRST, with numbers: "do NOT
  keep the reference's body proportions", about 4.6 heads, head about 22% of the height, legs about 40%. It also
  asks for a smooth illustration, not pixel art.
- **Second round:** all three came out about 4 heads tall with big faces, soft colours and identity kept, ref03
  included.
- **Drawn at 160 px** (owner-approved): drawing $0.74-1.40 each; with concepts $1.54 / $1.47 / $0.89. Critique
  scores were 50.3 / 44.2 / 53.1, against 31.6 / 26.2 / 34.1 for the realistic 160-px versions. Big heads, light
  palette, no black ring, simpler bodies: clearly toward the game's look.
- **New problem:** the eyes were too small. The faces grew to 24-30 px, but the kit's biggest eye (xl 7×6) was
  sized for Noel's 18-px face. Added `xxl` (9×7) for faces 22 px and wider.
- **Measurement fixes in `face_layout`:**
  - skin runs bridge gaps up to 10 px, so a 7-9-px eye no longer splits the face and puts the chin at eye level;
  - the eye-bottom-to-chin distance is proportional (~0.28 × face width, 4-9 px);
  - an implausibly short face (turned, or partly hidden by hair) gets the chin estimated from its width.
- **Free preview:** xxl eyes at the measured place look clearly right on ref01 and ref05. ref03 (three-quarter view,
  hair across the face) still measures poorly; the model's own placement is better there.
- **Face pass rerun** (owner-approved, face lab round r6, $0.36, 9 rounds each): the model used xxl eyes at the
  measured place, and `turn` on the two three-quarter faces. ref03 improved most (clean, well-placed eyes); ref01 and
  ref05 have eyes about a third of the face width.
- **Remaining gaps:** the concept eyes have bright catchlights and ours have none (the separate highlight was
  dropped against the sideways-glance look), so the eyes read a little flat. Expressions are milder than the
  concepts (ref01 laughs in the concept, only smiles in the sprite). Next idea: a 1-2 px catchlight in the upper
  iris on the inner side for xl/xxl eyes, mirrored.

**Root-cause analysis (2026-11-10): see `docs/FACE_ROOT_CAUSE.md`.** The face is good right after the plain
reduction (step 4) and is degraded by the palette step (eye colours starved), the model's redraw (the largest
drift) and the template face pass. Keeping the reduction's face gave the best faces of the project. The proposed
direction was to protect the face. After the owner confirmed it, the whole sprite now comes from the reduction:
`[finish] mode = "reduce"` (default) uses per-material k-means colours (96), no outline ring and no model
drawing; the drawing step passes the result through, so revise and gate-C notes still work. About $0.15-0.20
per character. Section 4 of FACE_ROOT_CAUSE.md has the details.

Rules v3 (`FACE_INSTRUCTIONS_V3`) were rewritten to match. The old v3 kit (box eyes: a full-width lash, a dark iris
row and a light bottom row; dark red "v" mouths) lost its iris colour under the dark top rows and read as an
emoticon.

**Resolution matters more than the kit.** The owner still found the faces uncanny and suggested more pixels. To
test it for free, five reference runs were retraced at 96, 128 and 160 px. The material map is reused (it is made at
concept resolution); only pixelize → polish was redone, in stand-in mode. The kit was then stamped on each.

| Height | Face width | Eye size | How it reads |
|---|---|---|---|
| 96 px | 9–10 px | s (3×3) | Crude; happy eyes turn to dots |
| 128 px | 12–14 px | m | In between |
| 160 px | about 16 px | l, with `xl` (6×6) added for 20 px and wider | Natural (Alice in Cradle's scale: ~150-px sprites, 16–18-px faces) |

The owner's idols are 5.5–7 heads tall, so their faces are smaller at a given height than AIC's chibi-like 4.5 heads.

The app only offers 64/96/128 (`server.py` settings validation, ESTIMATES, three segmented controls in `app.js`).
**Paid check at 160 px (owner-approved, $1.00 total).**
- **Face lab round 4** (`benchmarks/face_lab_runs/r4`): rules v3 on five 160-px traced sprites. It cost $0.38,
  6-8 rounds each. Every face beat today's 96-px model faces; ref10's `o` mouth was a plus sign, now a soft 2×2 blob.
- **Full character** (`benchmarks/retrace_height.py`, ref01 at 160 px reusing its concept and map). It cost $0.62
  in 247 s: drawing 21 rounds $0.50, face pass 9 rounds $0.11. The 96-px run was $0.61 in 400 s. Lint 31.7 against
  28.8. Costume, hands and face all clearly better; the eyes came out slightly heavy-lidded for a happy concept.

**Decision:** the default height is now **160 px** (`config.toml`, app settings default, 160 added to the app's
height choices, ESTIMATES 160 = $1.30 with headroom). The default face rules are now **v3** (face kit). An existing
`~/.pixelagent/settings.json` keeps its saved height until the owner picks 160 in Settings. Animation cost per
frame at 160 px is not measured yet.

**Animation, first real run** (Mushroom Traveler, 96 px, 6-frame walk): plan + pose sheet $0.07, drawing $1.46
(69 rounds), **$1.53 total**. The palette was identical (25 colours) in all frames, and the opaque-area spread
was 3.3 %. The owner approved and exported it.

**Cleanup (2026-11-09, owner request).** Old samples were removed recoverably:
- **Recycle Bin:** the 64-px and 96-px benchmark runs (`bench_runs`, `bench_runs_96`, about $9 of runs), face-lab
  rounds r1-r3 and the build cache.
- **App:** the 96-px "Lantern Keeper" and its walk animation went to Recently Deleted.
- **Kept:** `bench_runs_refs` (the ten owner references at 96 px; their concepts let `retrace_height.py` redo any of
  them at 160 px for ~$0.60), `bench_runs_160` and face-lab round r4.

---

## 9. Milestones completed

| Milestone | Content | Commit |
|---|---|---|
| Engine | Headless canvas + MCP tools; frames/GIF; quality tools (lint, AA, sel-out, ramps); `compare_reference` | `029b934`, `7801b3e` |
| M0 | Pipeline skeleton, store, gates, dry-run stubs, review HTML, export | `794da77` |
| M1 | OpenAI backend (structured outputs, images), material map, real labelling, cost log + budgets | `794da77` |
| M2 | Quality mechanisms (section 7); detail/repair loop (now the non-default `repair` mode) | `794da77` |
| Artist | Model draws via MCP; hybrid trace; face pass | `94ddf3b` |
| 96 px | Default height 96; face detection fixes | `40e38f8` |
| M3 | Prompt-driven animation pipeline | `5c5c96c` |
| App | Desktop app, security hardening, paused/resume, `PixelAgent.exe` packaging, 11 app tests | `ef42e79` |
| Roadmap 1-7 | Real run from the exe; UI pass for animations and references; README; live drawing view; delete/rename/duplicate; versions and redo; cost estimates and limits; face-pass cost fix | after `78fb9cf` |

---

## 10. The desktop app and `PixelAgent.exe`

### 10.1 Architecture
- `pixelagent/app/server.py`: a standard-library `ThreadingHTTPServer` bound to **127.0.0.1** (port 8765 in
  the exe, so a second launch finds the first). It serves the static UI and a JSON API, and runs one background
  pipeline run per asset (`Jobs`), keeping the log lines for live progress.
- `pixelagent/app/static/`: `index.html`, `app.css`, `app.js`. Vanilla single-page app with hash routes
  `#/characters`, `#/animations`, `#/item/<id>`, `#/settings`. It polls while something runs, plus a 30 s
  heartbeat (`/api/ping`).
- Settings in `~/.pixelagent/settings.json` (env `PIXELAGENT_SETTINGS` overrides; used by tests):
  `projects_dir` (default `~/Documents/PixelAgent`), `backend` (`openai` | `dry_run`), `monthly_usd` (90, one
  limit for all projects together), `asset_usd` (3, limit per item including redraws), `sprite_height`
  (64/96/128, default 96), `current_project`.
- Deletes go to **Recently Deleted** (`pixelagent/trash.py`): a character moves together with its animations
  as one entry (`entry.json` + `payload/` + a thumbnail); Restore puts it back under a free name if the old one is
  taken (and recreates a deleted project folder), Delete Now and Delete All remove for good after a confirmation.
- Spending: the app records every paid call in a ledger next to the settings (`~/.pixelagent/ledger.jsonl`),
  independent of project folders, so deleting or moving projects never lowers the month's total (finding F02).
  A missing ledger is seeded once from the projects' `costs.jsonl`.
- Live drawing: after every drawing round the artist exports its drawing (never the tracing layer) to
  `<asset>/live/live.png` + `live.json` (`pixelagent/live.py`); the app shows it while the item runs.

### 10.2 API
GET `/`, `/static/*`, `/files?project=&path=` (confined to the project folder), `/api/ping`, `/api/overview`,
`/api/assets?project=`, `/api/asset?project=&id=`.
POST `/api/projects`, `/api/current`, `/api/characters` (prompt and/or data-URL reference image),
`/api/animations`, `/api/decide` (approve / reject with note / pick / edited spec at gate A), `/api/run`
(start or continue), `/api/open` (PixelLiner or folder), `/api/settings` (validated; optional API key),
`/api/trash` (GET), `/api/trash/restore|delete|empty`, `/trashfile` (thumbnails),
`/api/item/rename|duplicate|delete`, `/api/project/rename|delete|open`, `/api/versions/restore`
(make an older sprite/frames version current; later cheap steps rerun), `/api/redo` (rerun from a milestone).
A missing item answers 404 with `not_found`. Paid actions check the limits first (`spend_check`) and refuse
before changing anything; approving the final sprite or animation (export only, free) is never blocked.

Item details also carry `live` (while drawing), `versions` (sprite or frame versions with thumbnails) and
`redo` (estimated cost per milestone; 0 in test mode). The overview carries `month_spent` (all projects) and
`estimates` (character by height, animation base + per frame).

Each asset summary carries `gate`, `running`, `failed` and `paused`. **Paused** means stopped between steps
(for example, the app was closed mid-run), and the UI offers **Continue**. Milestone grouping for progress:
character = Plan, Concept, Pixel conversion, Drawing, Quality check, Your approval, Export; animation = Plan,
Poses, Frame traces, Drawing frames, Your approval, Export.

### 10.3 Security
- Every request must have a loopback `Host` (`127.0.0.1:port` or `localhost:port`), which blocks DNS rebinding.
- POSTs must come from the app's own `Origin`.
- Project names are a single plain folder name: no separators, no `.`/`..`, no control characters.
- Static and file paths are confined with `normpath` + prefix + separator checks.
- File URLs are built with `urlencode`.
- `AppServer` disables `SO_REUSEADDR`. On Windows that option would let a second process share the port.

### 10.4 UI design (Apple-style)
- **Fonts and colours:** system font stack (SF Pro / Segoe UI Variable). Accent systemIndigo `#5E5CE6`, Apple
  greys (`--window #F5F5F7`, `--text #1D1D1F`, `--text-2 #6E6E73`), with a full dark-mode token set.
- **Window layout:** vibrancy sidebar (Library: Characters, Animations, with badges; Projects; spend meter;
  Settings) and a translucent toolbar with a segmented filter (All / Needs you / Working / Ready).
- **Pixel art display:** sprite tiles on a checkerboard stage, at integer zoom only (or a whole-number divisor
  when too large), with `image-rendering: pixelated`. The dark-mode checkerboard is mid-grey so dark outlines
  stay visible.
- **Item page:**
  - Hero stage with zoom (Fit/1×/2×/4×/8×). Before anything is drawn, it is a compact placeholder.
  - Decision panel per gate: an editable plan; a concept picker (one column per candidate, and the hero
    previews the selected concept); approve or redraw with a note; pose sheet; animation.
  - A quiet "working" panel while running, and a Paused panel. While the model draws, the stage shows the live
    drawing with a "Drawing live, round N" (or "Drawing frame k of n") badge.
  - Inspector: progress (finished milestones have a Redo button, with a cost estimate in a confirmation
    alert), details (with a link to the reference image), versions (click to preview on the stage, "Use this
    version"), exports, animations, activity log.
  - The stage shows the reference image until a concept exists, the pose sheet at gate P, and the 1x GIF at
    gate R with every frame as its own tile below. Paintings (concepts, pose sheets) are smoothly scaled to an
    exact fitted size; pixel art always at whole-number zoom.
- **Menus and alerts:** a More button (toolbar) and right-click on tiles and project rows open a macOS-style menu
  (Rename, Duplicate, Show in folder, Delete). Deletes use a centered alert with the app icon, Cancel focused
  and a red Delete; a character's animations are named and deleted with it.
- **Narrow windows:** below 900 px the sidebar collapses behind a toolbar button and slides in over the page.
- **Spending:** the New character and New animation sheets show an estimate next to Create; a banner appears
  at 80 % and at 100 % of the monthly limit; Settings has the monthly limit and the limit per item.
- **Sheets:** drop from the top, as on macOS. "New character" has two tabs, Describe and Reference image
  (drag and drop). "New animation" offers preset chips and a frame count. A click outside a sheet with typed
  content only nudges it (shakes).
- **Errors:** one plain sentence, with the raw error folded under "Details".

### 10.5 The exe
- `packaging/PixelAgent.spec`, `build.ps1`, `make_icon.py` (icon drawn from the brand mark, 16–256 px) and
  `version_info.txt`. The result is a windowed onefile `dist/PixelAgent.exe` of about 39 MB.
- **Engine:** bundled both as source files under `engine/` and as modules. When frozen, `MCPClient` launches
  `PixelAgent.exe --mcp-server` (`config.resource_path`, `config.FROZEN`). In that mode `__main__.py` rebinds
  stdio for the windowed exe; otherwise output goes to `~/.pixelagent/app.log`.
- **Window and lifetime:**
  - Opens an Edge `--app=` window (Chrome as fallback, then the default browser).
  - A single instance per port: a second launch opens a window to the running one.
  - Quits after 180 s with no window open and no run in progress.
- **Verified in test mode from the built exe:**
  - the full character flow to export;
  - the engine subprocess drawing and exporting through `MCPClient`;
  - the UI served from the bundle;
  - the Edge app window;
  - second-launch detection.
- **Verified with real models (2026-11-09):** a character from a prompt through every gate to export from the
  exe ($1.60, see section 8), and a 6-frame walk for it through poses and frames to review ($1.93), with the
  live view following every frame.

---

## 11. Tests

103 `unittest` tests in `tests/`, all free (dry-run mode or scripted fake models):

| File | Tests | Covers |
|---|---|---|
| `test_pipeline.py` | 11 | Steps, gates, versions, resume; test runs ignore paid limits |
| `test_quality_mechanisms.py` | 15 | Section 7 mechanisms |
| `test_openai_backend.py` | 16 | Request shapes, strict schemas, cost math (mocked); a limit stops the artist honestly and it continues later; revising an area changes only that area; revising continues the drawing; cancel keeps the drawing and Continue resumes |
| `test_artist.py` | 9 | Artist agent tools/session; fresh face pass; two views in one round are two images |
| `test_animation.py` | 6 | Animation steps; a limit stops the frames honestly and resumes; play-once GIFs |
| `test_app.py` | 21 | App API end-to-end, references, animation review, security, item ids, paused runs, managing items and projects, Recently Deleted, revise and area fixes, a cancel applies only to its own run, single instance, save retry |
| `test_live.py` | 5 | Live drawing through the real engine (never the trace), staleness, shown only while running |
| `test_budget.py` | 7 | One monthly limit, the ledger survives deleting projects, seeding, limit per item, refusing before creating, free final export, drawing loop stopping |
| `test_face.py` | 3 | Face trace keeps small dark features, only face cells change |
| `test_names.py` | 3 | Safe file names, a hostile name exports inside its folder, step outputs cannot leave their folder |
| `test_review_fixes.py` | 3 | Restores only from the same inputs, interrupted version folders kept and skipped, the trace cannot be copied |

Note: dry-run stubs exercise the plumbing only. The stub labeller groups clusters by hue and can merge
materials, and the artist step is skipped in dry-run mode. Visual quality can only be judged with real models.

---

## 11a. Steering a character: Cancel and Revise (2026-11-09)

- **Cancel** (`pixelagent/cancel.py`, `POST /api/cancel`): the pipeline checks before each step and the model loop
  before each round, so a cancel takes effect within one model call. The cancelled step goes back to `pending`, the
  item shows "Cancelled" with Continue, and drawing in progress is checkpointed (characters and animation frames).
  A cancel request only applies to the run it was made for (cleared when a run starts and ends).
- **Revise instead of redraw** (`pixelagent/revise.py`, `Pipeline.request_fix`, `POST /api/fix`): a note on the
  sprite approval, or on a finished character, revises the current drawing in a short fresh session
  (`[finish] revise_turns = 12`, `revise_max_usd = 0.30`) instead of drawing it again. With an area (dragged on the
  sprite in the app, snapped to its pixels) only that rectangle may change: afterwards `confine()` restores every
  pixel outside it from the version the revision started from, layer by layer. Each revision is a new version
  (Versions card, restorable). A full redraw is still available as Redo next to Drawing.
- **First real area fix** (Lantern Keeper's face, 15 x 14 px area): 7 rounds, **$0.10 in 82 s** (a full redraw:
  ~$0.90, ~5 min); exactly 15 pixels changed, all inside the area; the eyes and smile became readable at 1x.
- Stop messages: a spending stop reads "Stopped at a spending limit" with a link to Settings.

## 11c. Reference baseline (2026-11-09): 10 owner references, real models

Ten anime-style character references from the owner (`benchmarks/refs/owner/`, git-ignored: third-party art used
for testing only; case list `benchmarks/cases_refs.csv`; results `bench_runs_refs/`). Six are half-body or seated,
all ten have scenery.

**Found before running (fixed):** the reference path converted the reference as it was; it only removes plain
backgrounds, so all ten would have turned their whole scene into the sprite (figure "coverage" 66-100%, touching
the image edges). Now `[concept] clean_reference = "auto"` checks each reference (`concept.reference_usable`:
coverage <= 60%, no edge contact) and otherwise has the image model redraw the character as a clean full-body
concept (identity, colours and costume kept; partial or seated figures completed as standing), guided by the
plan's summary and must-keep list. A redrawn concept always waits at gate B for the owner's approval.

**Results:** 10/10 complete; **$6.44 total, median $0.66 per character** (max $0.74), including the concept
redraw; **median 6.4 min** (max 6.9). The redrawn concepts were faithful in all ten. Sprites: recognisable
characters and costumes; the weak spot was **noise** (critic median 6.2, lint median 33).

**Noise, root cause:** measured trace vs drawing, the artist cleans prompt-made concepts (isolated pixels 209 -> 94)
but not anime concepts (131 -> 123) and adds shade flicker (neighbour shade changes 0.44 -> 0.54): it chases
linework and folds that cannot survive at 96 px. **Fix:** `pixelagent/cleanup.py` merges tiny near-shade specks
within a material after the drawing, as its own `cleanup` layer; the face, transparent pixels and real contrast
(highlights, buttons, creases) are never touched (`[finish] cleanup = true`). On the ten drawings: lint 33.4 -> 40.8,
isolated pixels 123 -> 81, shade changes 0.538 -> 0.471 (offline, same drawings).

**Face box (fixed):** the waving idol's face pass had worked on her raised hand (a hand is skin too, and was the
bigger group). `face_region` now picks the group holding the eye/mouth labels and sizes the box around them; with
no such labels it keeps a face-sized box (18% of the figure's height) from the top of the group.

Still open from this baseline: owner ratings of the ten sprites (not done), a second sample per reference to
measure run-to-run variation, and a rerun with the cleanup and face-box fixes in place (about $6.50).

## 11b. Review of `de5e3a2` (Codex, 2026-11-09): all 11 findings fixed

| ID | Finding | Fix | Regression test |
|---|---|---|---|
| F01 | Recycle Bin delete could be permanent | Recently Deleted folder; permanent only by explicit, confirmed action | `test_app` Recently Deleted tests |
| F02 | Deleting a project removed its spending | App ledger outside project folders, seeded once | `test_budget.test_deleting_a_project_keeps_its_spending` |
| F03 | Asset ids could reach another project | `Project.asset` accepts only a plain id; runs check the item before starting | `test_app.test_item_ids_cannot_leave_their_project` |
| F04 | Display names became unsafe paths | `names.safe_filename` for exports, layers, regions; `Ctx.path` refuses paths leaving the step folder | `test_names` |
| F05 | Restores mixed versions with newer inputs | Each version records the versions it was made from; only matching ones restore; UI dims the others | `test_review_fixes.RestoreTests` |
| F06 | A spending stop reached review as complete | Stops are explicit errors; finished frames / the drawing are kept as checkpoints and resumed | `test_animation`, `test_openai_backend` stop tests |
| F07 | Two `view`s in one round gave the model the same image | Per-session image sequence in file names | `test_artist.test_two_views_in_one_round_reach_the_model_as_two_images` (fails on the old code) |
| F08 | The trace could be copied into the drawing | Guard resolves the reference layer by name and index for `from_layer` and `layer` | `test_review_fixes.ReferenceGuardTests` |
| F09 | An interrupted run's folder could be reused | Next free folder, reserved in `state.json` before writing | `test_review_fixes.VersionFolderTests` |
| F10 | Test runs failed when paid limits were used up | Stand-in models never check paid limits | `test_pipeline.test_test_runs_ignore_paid_limits` |
| F11 | Play-once animations looped in preview/4x GIFs | One rule for all GIFs; play once = no NETSCAPE block (engine `gif.py`) | `test_animation` (stop test checks all GIFs) |

Also from the review: the unused `openai-agents` dependency was removed. Not done yet (release items, owner's call):
license, dependency lock and pinned engine revision in the build, CI, clean-machine test.

## 12. Known issues and limits

1. **Cost estimates are upper bounds:** cached input is priced in full in `agent_session._price`; actual billing
   is lower. Real runs from the exe on 2026-11-09: a character $1.60 (before the face-pass fix), a 6-frame walk
   $1.93 (plan + poses $0.07, ~$0.31 per frame, 11 minutes; no frame-consistency issues).
2. **Versions** are listed for the sprite drawing and the animation frames only (concept versions stay on disk
   but are not shown). Animations drawn before 2026-11-09 have no 1x GIF or per-frame PNGs, so their oldest
   version thumbnail is the 4x GIF and their review shows the frame strip instead of tiles.
3. **Budget scope:** the app's monthly limit uses its ledger (all app runs, also from deleted projects);
   command-line and benchmark runs (`python -m pixelagent`, `benchmarks/`) count their own project only and are
   not in the app's ledger.
4. **Faces:** with the native finish they rate 4.5 (owner, ten references). Model judges cannot rate them
   reliably; use the owner's ratings. Expressions are redrawn simpler and more frontal than a three-quarter face.
5. **Native-finish limits:** the proportion check misreads pixel art and is information only. The image model
   picks its own pixel count, so sprite heights vary by about 10%. Material labels are coarse (no eye material on
   most characters).
6. **Character-tuned pipeline.** `CharacterSpec.asset_type` accepts `prop`, `tile` and `scene`, but the steps
   (face pass, material labels, pose logic) assume a character. Objects and scenes are not yet supported in
   practice.
7. **Image-model prices are unverified.** Costs are estimates; `costs.jsonl` keeps raw token counts for
   recomputation.
8. **Windows-only parts:** `winreg` for the key and the Edge lookup, and `os.startfile`.
9. **Distribution:** the exe is unsigned, so Windows SmartScreen will warn on other machines. There is no
   installer and no auto-update.
10. **Single user, local only,** by design. The server must never bind to anything other than 127.0.0.1.

---

## 13. Remaining work, in priority order

Each item has a goal, the main files, and what "done" means. Run everything in dry-run mode first. Real runs
cost money: estimate and ask first.

### Done on 2026-11-09
Real run from the exe (character), the UI pass for animations and reference images, README refresh, live
drawing view, delete/rename/duplicate, versions and "redo from here", cost estimates and spending limits, plus
the fixes they turned up: the face-pass cost, a race when the page read an item twice during a save, Windows
file locks on `state.json`, the user's name being replaced by the planner, cropped concepts on the stage, and
an animation-listing bug.

### Done on 2026-11-10/11 (details and numbers: `docs/FACE_ROOT_CAUSE.md`)
- **Native pixel-art finish** (now the default, the app's Soft look). The image model draws the character as pixel
  art, which is read back block by block. Owner ratings on the ten references: 4.8 overall and 4.5 for faces
  (artist and reduce finishes: 3.7 and 4.0). About $0.15 a character.
- **Palette grade** (`[finish] grade`, the app's Colours setting; medium is the owner's pick): muted colours and
  lifted shadows, toward Alice in Cradle's dusty palette.
- **Animations:** a pixel-art pose sheet drawn over a layout of the finished sprite, with the frames read back.
  Frames match the character's size; ~$0.07 an animation.
- **Move set:** idle, walk, run, jump, attack and hurt in one go (app button, `benchmarks/move_set.py`).
- **Expressions:** happy, sad, angry, surprised, hurt and blink. Only the face changes, located by the vision
  model; ~$0.055 a face (`pixelagent/expressions.py`).
- **Game pack:** an atlas with uniform cells and one pivot, Aseprite/TexturePacker JSON, Godot 4 SpriteFrames and
  a README (`pixelagent/gamepack.py`, `pixelagent pack ID`, the Exports card).
- **Objects** (asset_type `prop`, the app's Objects library): the native finish for one item on a square image,
  with blocks sized for the item (`concept.object_block`). Its own prompt and critic note; fitted to its size
  when drawn more than 20% too big. First three (`bench_runs_objects`: sword, chest, potion; $0.87): clean pixel art;
  the sword was drawn 82 px for 48 and fitted, losing some blade shading.

### Next: characters
1. **A validation round for move sets and expressions** on several references, rated by the owner (one character
   so far: ref05). Watch for drift in the frames' faces and for poses the layout holds back.
2. **Steering single pieces:** redo one animation frame or one expression with a note (the frames are separate
   PNGs; expressions already redo by name).
3. **Faces inside animations:** let an animation use an expression (a hurt move with the hurt face) by pasting the
   face patch onto upright frames, or by giving the face to the pose sheet's layout.
4. **Directions:** left/right is a mirror for side-scrolling games. Back and front views, and 4/8 directions for
   top-down games, only if the owner wants them: one sheet for all views on the layout approach.
5. **Accurate costs:** price cached input at its discounted rate (`config.toml [pricing]`).
6. **Concept versions** in the Versions card (the concept step's outputs are on disk under `vNNN/`).

### Later: objects and backgrounds, as their own workflows
The owner's direction (2026-11-09): **characters are the primary focus.** Objects and backgrounds must be supported
too, mainly generated from prompts, and their logic differs from characters, so each gets its own workflow instead
of options on the character pipeline. Each is a separate asset kind (like animations), with its own steps, gates,
app sheet ("New object", "New background") and benchmark.

| | Character (exists) | Object / prop (planned) | Background / scene (planned) |
|---|---|---|---|
| Subject | One figure, identity, face | One isolated item, silhouette at small size | The whole canvas: composition, depth, mood |
| Size | 64-128 px tall | 16-48 px (icons, items, props) | 320x180, 480x270 or wider; tileable strips |
| Concept | Image model, flat background, gate B | Same, item centred, 2-4 drafts | Layout plan first (layers: sky, far, mid, near, ground), then one image per layer, wide aspect, seamless horizontally |
| Conversion | Material map, pixelize, ramps, trace | Same steps, object material names, no face pass | Per layer: palette per scene (atmospheric perspective: far layers lower contrast and cooler), optional dithering |
| Model drawing | Artist redraws every pixel over the trace, then a face pass | Artist redraws over the trace (small, cheap) | Too large to redraw pixel by pixel: converted pixels may stay, plus targeted model passes on regions (focal points, edges, seams); the owner approved this (2026-11-09) |
| Checks | Lint, critic, face, frame consistency | Lint, silhouette readability at 1x | Seam lint (left/right edges match), parallax layer alpha, colour count per layer |
| Export | PNG, layers, animation | PNG, icon sizes, item sheet | Layered parallax PNGs, tileable strips, a PixelLiner project |

7. **Objects/props:** a first version is done (see "Done" above). Next: an item sheet export (several items in one
   atlas, icon sizes) and the owner's ratings; it reuses most character steps; the work is object-aware material labels, the size rules,
   skipping the face pass and animation, and an "item sheet" export. Done when three benchmark props (for
   example a weapon, a chest, a potion) export cleanly from a prompt.
8. **Backgrounds/scenes:** a new pipeline (layout plan → per-layer concepts → per-layer conversion → targeted
   artist passes → seam and layer checks → layered export). Start with a short design doc and 3 benchmark
   prompts (forest, town street, cave) before code.
9. **Tiles and tilesets:** a subset of backgrounds: seamless edges (wrap-around lint), autotile variants (corners
    and edges), and a palette shared with the characters. Needs a tiling preview and an edge-continuity lint in
    the engine.

### Later: distribution
10. **Signing and installer:**
    - code-sign the exe (to avoid the SmartScreen warning);
    - an installer (MSIX or Inno Setup) with a Start menu entry;
    - optional auto-update from GitHub releases.
11. **First-run onboarding:** a welcome sheet that sets the API key, explains test-run mode and creates a
    first project.
12. **Cross-platform:** abstract the Windows-only calls (`winreg`, `os.startfile`, the Edge lookup) if macOS or
    Linux are ever wanted.

---

## 14. Decision log (made by the owner)

| Decision | Choice |
|---|---|
| Model provider | OpenAI API (Pro $100 tier credit) |
| Framework | No agent framework. A thin custom layer over the OpenAI Responses API plus a custom MCP client; the project is a pipeline with gates, not an open-ended agent. |
| How pixels are made | Native finish by default (2026-11-10): the image model draws pixel art, read back; the MCP artist stays as the Classic look |
| Default sprite height | 160 px, about 4.5 heads (Alice in Cradle scale; earlier 96 px) |
| Animation | Driven by the user's prompt; separate asset per animation |
| Optimisation policy | Root causes across all benchmark characters, never per-example tweaks |
| UI | Local web UI in an app window, Apple-style, packaged as an .exe |
| Focus | Characters first. Objects and backgrounds later, from prompts, each as its own workflow (2026-11-09) |
| Faces | Judged by the owner's blind ratings (benchmarks/face_lab.py), not by model judges |
| Deleting | Recently Deleted inside the app with Restore, Delete Now and Delete All, instead of the Windows Recycle Bin (2026-11-09) |
| Final pixels | Native: the image model's drawn pixels, read back, rebuilt in 3-6 tones a material and graded (palette grade medium, 2026-11-11). Backgrounds: converted pixels may remain, with targeted model passes (2026-11-09) |
| Style target | Alice in Cradle's characters: vivid pixel-art faces, not detailed illustrations; eyes "suitable", not bigger; muted, light palette (2026-11-10/11) |
| Repos | Private: `crackthedream/pixelagent`, `crackthedream/pixelliner-mcp` |

---

## 15. Glossary

- **Trace:** the pipeline's palette-clean conversion of the concept on the final grid. The artist draws over it.
- **Gate:** a stop where the user decides:
  - A: plan;
  - B: concept;
  - C: sprite;
  - P: pose sheet;
  - R: animation.
- **Material map:** a flat key-colour segmentation of the concept, used to label materials.
- **Sel-out:** an outline coloured per neighbouring material instead of one dark line.
- **Dry run / Test run:** stub models with no API calls and no cost. It tests plumbing, not quality.
- **Paused:** a run stopped between steps. Continue resumes where it stopped.
