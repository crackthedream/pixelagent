# PixelAgent: development status and remaining work

A handoff document for an AI developer (GPT) continuing this project. It describes what exists, how it works,
what has been measured, which decisions the owner has made, and what is left to build, in priority order.
Status as of 2026-11-09 (updated after the first seven roadmap items; see `git log` of `crackthedream/pixelagent`
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
3. **The model draws the pixel art through MCP tool calls.** The owner was explicit: the goal is not an image
   model producing pixel art directly, but a model that uses a reference to *draw* with the pixelliner tools.
   Image models are used only for concepts and pose sheets; the final sprite and every animation frame are
   drawn by the artist agent through MCP.
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

# tests (61 tests, ~110 s; all dry-run, no cost)
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

## 8. Artist mode (the default finish) and faces

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

Decision: **keep A as the default** (`[finish] face_trace = false`, `face_rules = "v1"`; both switches exist).
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

**Animation, first real run** (Mushroom Traveler, 96 px, 6-frame walk): plan + pose sheet $0.07, drawing $1.46
(69 rounds), **$1.53 total**. The palette was identical (25 colours) in all frames, and the opaque-area spread
was 3.3 %. The owner approved and exported it.

**Spend recorded in the current benchmark logs:** about $9 (`bench_runs` $2.91, `bench_runs_96` $6.08). Earlier
runs were cleaned up.

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

99 `unittest` tests in `tests/`, all free (dry-run mode or scripted fake models):

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
4. **Faces at 96 px** are much better than at 64 px but remain the weakest part. Model judges cannot rate them
   reliably; the owner's blind ratings (face lab round 1) kept today's face pass over two alternatives.
5. **Character-tuned pipeline.** `CharacterSpec.asset_type` accepts `prop`, `tile` and `scene`, but the steps
   (face pass, material labels, pose logic) assume a character. Objects and scenes are not yet supported in
   practice.
6. **Image-model prices are unverified.** Costs are estimates; `costs.jsonl` keeps raw token counts for
   recomputation.
7. **Windows-only parts:** `winreg` for the key and the Edge lookup, and `os.startfile`.
8. **Distribution:** the exe is unsigned, so Windows SmartScreen will warn on other machines. There is no
   installer and no auto-update.
9. **Single user, local only,** by design. The server must never bind to anything other than 127.0.0.1.

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

### Next: app
1. ~~One real animation from the exe~~ done 2026-11-09 ($1.93; the live view followed all six frames).
   ~~Cancel; revise instead of redraw; fix an area~~ done 2026-11-09 (section 11a). Next for steering: revising a
   single animation frame the same way (the frames are already separate PNGs and cels).
2. **More animation presets and export targets.**
   - Presets: idle, run, attack, jump, hurt, death, cast, with frame defaults.
   - Exports: sheet metadata for game engines (Aseprite JSON, Godot SpriteFrames, Unity), and multi-row sheets.
3. **Concept versions** in the Versions card (concept step outputs are on disk under `vNNN/`).
4. **Accurate costs:** price cached input at its discounted rate (needs the cached-input prices per model in
   `config.toml [pricing]`).

### Next: quality
5. **Reference-image benchmark.**
   - About 10 reference images in `benchmarks/refs`. Evaluate `[concept] clean_reference` on and off,
     comparing distance to the reference, specks, and owner ratings in `benchmarks/ratings.csv`.
   - The prompt path is well measured; the reference path is not.
6. **Faces.** Round 1 is done (section 8): today's face pass stays the default. Next round, if faces stay the
    priority: apply the face trace + rules v2 only to concepts with strong facial features, two samples per
    variant, rated blind with `benchmarks/face_lab.py` (prepare / run / serve / report). Do not rely on model
    judges.
7. **Directions and consistency.** Front/side/back (4- or 8-direction) sprites of the same character with an
    identical palette and proportions, reusing the animation approach: one sheet for all views, then shared
    scale and palette.

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

8. **Objects/props:** reuses most character steps; the work is object-aware material labels, the size rules,
   skipping the face pass and animation, and an "item sheet" export. Done when three benchmark props (for
   example a weapon, a chest, a potion) export cleanly from a prompt.
9. **Backgrounds/scenes:** a new pipeline (layout plan → per-layer concepts → per-layer conversion → targeted
   artist passes → seam and layer checks → layered export). Start with a short design doc and 3 benchmark
   prompts (forest, town street, cave) before code.
10. **Tiles and tilesets:** a subset of backgrounds: seamless edges (wrap-around lint), autotile variants (corners
    and edges), and a palette shared with the characters. Needs a tiling preview and an edge-continuity lint in
    the engine.

### Later: distribution
11. **Signing and installer:**
    - code-sign the exe (to avoid the SmartScreen warning);
    - an installer (MSIX or Inno Setup) with a Start menu entry;
    - optional auto-update from GitHub releases.
12. **First-run onboarding:** a welcome sheet that sets the API key, explains test-run mode and creates a
    first project.
13. **Cross-platform:** abstract the Windows-only calls (`winreg`, `os.startfile`, the Edge lookup) if macOS or
    Linux are ever wanted.

---

## 14. Decision log (made by the owner)

| Decision | Choice |
|---|---|
| Model provider | OpenAI API (Pro $100 tier credit) |
| Framework | No agent framework. A thin custom layer over the OpenAI Responses API plus a custom MCP client; the project is a pipeline with gates, not an open-ended agent. |
| How pixels are made | The model draws through MCP; hybrid over the pipeline's trace |
| Default sprite height | 96 px |
| Animation | Driven by the user's prompt; separate asset per animation |
| Optimisation policy | Root causes across all benchmark characters, never per-example tweaks |
| UI | Local web UI in an app window, Apple-style, packaged as an .exe |
| Focus | Characters first. Objects and backgrounds later, from prompts, each as its own workflow (2026-11-09) |
| Faces | Judged by the owner's blind ratings (benchmarks/face_lab.py), not by model judges |
| Deleting | Recently Deleted inside the app with Restore, Delete Now and Delete All, instead of the Windows Recycle Bin (2026-11-09) |
| Final pixels | Characters and animations: every final pixel drawn by the model. Backgrounds: converted pixels may remain, with targeted model passes (2026-11-09) |
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
