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
9. **Deleting files:** move them to the Recycle Bin (recoverable), never hard-delete the owner's work.
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
- Deletes go to the Windows Recycle Bin (`pixelagent/trash.py`, shell API, restorable); `PIXELAGENT_TRASH_DIR`
  redirects them to a folder (tests).
- Live drawing: after every drawing round the artist exports its drawing (never the tracing layer) to
  `<asset>/live/live.png` + `live.json` (`pixelagent/live.py`); the app shows it while the item runs.

### 10.2 API
GET `/`, `/static/*`, `/files?project=&path=` (confined to the project folder), `/api/ping`, `/api/overview`,
`/api/assets?project=`, `/api/asset?project=&id=`.
POST `/api/projects`, `/api/current`, `/api/characters` (prompt and/or data-URL reference image),
`/api/animations`, `/api/decide` (approve / reject with note / pick / edited spec at gate A), `/api/run`
(start or continue), `/api/open` (PixelLiner or folder), `/api/settings` (validated; optional API key),
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
  exe ($1.60, see section 8). A real animation from the exe has not been run (the same code path; about $1.60).

---

## 11. Tests

77 `unittest` tests in `tests/`, all in dry-run mode or with scripted fake models:

| File | Tests | Covers |
|---|---|---|
| `test_pipeline.py` | 11 | Steps, gates, versions, resume |
| `test_quality_mechanisms.py` | 15 | Section 7 mechanisms |
| `test_openai_backend.py` | 12 | Request shapes, strict schemas, cost math (mocked) |
| `test_artist.py` | 8 | Artist agent tools/session; the face pass starts a fresh conversation |
| `test_animation.py` | 5 | Animation steps |
| `test_app.py` | 16 | App API end-to-end, references, animation review, security, paused runs, managing items and projects, single instance, save retry |
| `test_live.py` | 5 | Live drawing through the real engine (never the trace), staleness, shown only while running |
| `test_budget.py` | 5 | One monthly limit for all projects, limit per item, refusing before creating, free final export, drawing loop stopping |

Note: dry-run stubs exercise the plumbing only. The stub labeller groups clusters by hue and can merge
materials, and the artist step is skipped in dry-run mode. Visual quality can only be judged with real models.

---

## 12. Known issues and limits

1. **No real animation from the exe yet** (characters: done). Cost estimates are upper bounds: cached input is
   priced in full in `agent_session._price`; actual billing is lower.
2. **Versions** are listed for the sprite drawing and the animation frames only (concept versions stay on disk
   but are not shown). Animations drawn before 2026-11-09 have no 1x GIF or per-frame PNGs, so their oldest
   version thumbnail is the 4x GIF and their review shows the frame strip instead of tiles.
3. **Budget scope:** the app's monthly limit covers every project in its projects folder; command-line runs
   (`python -m pixelagent`) still count their own project only.
4. **Faces at 96 px** are much better than at 64 px but remain the weakest part. Model judges cannot rate them
   reliably.
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
1. **One real animation from the exe** (about $1.60) to confirm the frame drawing and the live frame view with
   real models.
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
6. **Faces.** Build a small human-rated face set (owner ratings) and use it to compare face-pass prompts and
    variants. Do not rely on model judges.
7. **Directions and consistency.** Front/side/back (4- or 8-direction) sprites of the same character with an
    identical palette and proportions, reusing the animation approach: one sheet for all views, then shared
    scale and palette.

### Later: objects and scenes (the owner requires them)
8. **Props/objects** (`asset_type = "prop"`):
    - Skip the face pass, use object-appropriate material labels, set the size from the spec.
    - Done when three benchmark props (for example a weapon, a chest, a potion) export cleanly.
9. **Tiles and tilesets** (`"tile"`):
    - Seamless edges (a wrap-around lint), autotile variants (corners and edges), and a shared palette with
      the characters.
    - Needs new engine checks: tiling preview and edge continuity lint.
10. **Scenes/backgrounds** (`"scene"`):
    - Larger canvases, parallax layers, perspective. Likely a different pipeline (layout → layers → artist per
      layer).
    - Start with a design doc and a benchmark before code.

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
