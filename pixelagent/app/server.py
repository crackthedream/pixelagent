"""PixelAgent local app: a small web server (Python standard library only) with a JSON API over the pipeline and
a static single-page UI. Runs on 127.0.0.1 only; nothing is hosted online.

    python -m pixelagent.app            (or PixelAgent.exe)  -> opens http://127.0.0.1:<port>/
"""
import base64
import http.client
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

from PIL import Image

from ..config import resource_path
from ..pipeline import Pipeline
from ..schemas import CharacterSpec
from ..store import Project
from ..trash import RecentlyDeleted
from ..live import read_live

STATIC = resource_path("pixelagent", "app", "static")
SETTINGS_FILE = os.environ.get("PIXELAGENT_SETTINGS") or os.path.join(os.path.expanduser("~"), ".pixelagent", "settings.json")

STEP_LABELS = {
    "intake": "Plan", "concept": "Concept", "matmap": "Material map", "pixelize": "Pixel grid",
    "materials": "Colours", "rebuild": "Shading", "lines": "Outline", "detail": "Detail", "polish": "Polish",
    "artist": "Drawing", "critique": "Quality check", "review": "Your approval", "animate": "Animation",
    "export": "Export", "anim_plan": "Plan", "anim_poses": "Poses", "anim_traces": "Frame traces",
    "anim_draw": "Drawing frames", "anim_review": "Your approval", "anim_export": "Export",
}
GATE_KIND = {"A": "spec", "B": "concept", "C": "sprite", "P": "poses", "R": "animation", "S": "scene", "K": "kit"}
# what the user sees as progress: milestones, not internal steps
STAGES = {
    "character": [("Plan", ["intake"]), ("Concept", ["concept"]),
                  ("Pixel conversion", ["matmap", "pixelize", "materials", "rebuild", "lines", "detail", "polish"]),
                  ("Drawing", ["artist"]), ("Quality check", ["critique"]), ("Your approval", ["review"]),
                  ("Export", ["animate", "export"])],
    "animation": [("Plan", ["anim_plan"]), ("Poses", ["anim_poses"]), ("Frame traces", ["anim_traces"]),
                  ("Drawing frames", ["anim_draw"]), ("Your approval", ["anim_review"]), ("Export", ["anim_export"])],
}


# which step's versions the app lists, and the image that represents each version
STAGES["object"] = STAGES["character"]      # objects run the character pipeline (asset_type "prop")
STAGES["scene"] = [("Plan", ["scene_plan"]), ("Layers", ["scene_layers"]), ("Seams", ["scene_seams"]),
                   ("Pixel art", ["scene_pixel"]), ("Your approval", ["scene_review"]), ("Export", ["scene_export"])]
VERSIONED = {"character": ("artist", ("preview",)), "animation": ("anim_draw", ("gif", "gif_x4"))}
VERSIONED["object"] = VERSIONED["character"]
VERSIONED["scene"] = ("scene_pixel", ("composite",))
STAGES["kit"] = [("Plan", ["kit_plan"]), ("Sheet", ["kit_sheet"]), ("Tiles", ["kit_read"]),
                 ("Your approval", ["kit_review"]), ("Export", ["kit_export"])]
VERSIONED["kit"] = ("kit_review", ("level",))
# rough upper-bound cost (USD, OpenAI mode, 96 px) of running again from each milestone; measured on real runs
REDO_USD = {
    "character": {"Plan": 0.02, "Concept": 0.20, "Pixel conversion": 1.10, "Drawing": 0.95, "Quality check": 0.02,
                  "Your approval": 0.0, "Export": 0.0},
    "animation": {"Plan": 0.01, "Poses": 0.07, "Frame traces": 2.00, "Drawing frames": 2.00, "Your approval": 0.0,
                  "Export": 0.0},
    "scene": {"Plan": 0.01, "Layers": 0.20, "Seams": 0.0, "Pixel art": 0.0, "Your approval": 0.0, "Export": 0.0},
    "kit": {"Plan": 0.01, "Sheet": 0.06, "Tiles": 0.0, "Your approval": 0.0, "Export": 0.0},
}


def stage_status(statuses):
    if "failed" in statuses:
        return "failed"
    if "awaiting_gate" in statuses:
        return "awaiting_gate"
    if all(s in ("done", "skipped") for s in statuses):
        return "done"
    if any(s in ("done", "skipped") for s in statuses):
        return "partial"
    return "pending"


# ------------------------------------------------------------------ settings

def default_settings():
    return {"projects_dir": os.path.join(os.path.expanduser("~"), "Documents", "PixelAgent"),
            "backend": "dry_run", "monthly_usd": 90.0, "asset_usd": 3.0, "sprite_height": 160, "look": "soft",
            "current_project": None}


def load_settings():
    s = default_settings()
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    os.makedirs(s["projects_dir"], exist_ok=True)
    return s


def save_settings(s):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS_FILE)


def ledger_path():
    """The spending ledger lives with the settings, not in any project folder (review finding F02)."""
    return os.path.join(os.path.dirname(os.path.abspath(SETTINGS_FILE)), "ledger.jsonl")


def overrides(s):
    # one monthly limit for everything in the projects folder, and a limit per item
    return {"backend": {"mode": s["backend"]},
            "budget": {"monthly_usd": float(s["monthly_usd"]), "max_usd_per_asset": float(s.get("asset_usd", 3.0)),
                       "monthly_scope_dir": s["projects_dir"], "ledger": ledger_path()},
            "style": {"sprite_height": int(s["sprite_height"]), **LOOKS[s.get("look", "soft")]["style"]},
            "finish": {**LOOKS[s.get("look", "soft")]["finish"], "grade": s.get("grade", "medium"),
                       "prop_grade": s.get("prop_grade", "soft")}}


# the app's two looks: soft = the game-sprite look (reduce finish, docs/FACE_ROOT_CAUSE.md), classic = strict palette
# with the model-drawn finish and the face kit
LOOKS = {"soft": {"style": {"look": "soft", "outline": "soft"}, "finish": {"mode": "native"}},
         "classic": {"style": {"look": "classic", "outline": "selout"}, "finish": {"mode": "artist"}}}


# upper-bound estimates (USD, OpenAI mode) shown before starting; from real runs: a 96-px character ~$1.15 with the
# fresh face pass, 64 px ~$0.70; 128 px extrapolated; 160 px: four real runs reusing the concept cost $0.62-1.27
# (drawing 21-40 rounds + face pass), so ~$1.35-1.45 with concept and map, here with headroom. Animations: plan + pose sheet ~$0.07, then ~$0.33 per drawn
# frame (two real 96-px walks: $1.46 for 6 frames, ~$1.95 for 6 frames from the exe on 2026-11-09).
# the reduce finish (soft look) has no drawing step: plan, concept, material map, grid pick and critique, and one
# proportion retry when the concept misses (~$0.14 without, ~$0.25 with one)
REDUCE_CHARACTER = {"64": 0.30, "96": 0.30, "128": 0.30, "160": 0.30}
# the native finish (pixel art) never retries the concept: plan, concept, material map, critique (~$0.15)
NATIVE_CHARACTER = {"64": 0.20, "96": 0.20, "128": 0.20, "160": 0.20}
ESTIMATES = {"character": {"64": 0.70, "96": 1.15, "128": 1.70, "160": 1.50}, "animation_base": 0.08, "animation_per_frame": 0.33,
             # revising the current drawing (a short session) instead of drawing it again (~$0.90)
             "revise_area": 0.20, "revise_whole": 0.35}


def estimates(settings=None):
    """The estimates the app shows: a character costs far less when the finish is the reduction (no drawing)."""
    if settings is not None:
        mode = LOOKS[settings.get("look", "soft")]["finish"]["mode"]
    else:
        from ..config import load_config
        mode = load_config().get("finish", {}).get("mode", "artist")
    # animations: plan + pose sheet only (~$0.10), no per-frame drawing
    # revisions: one concept edit + material map + critique (~$0.12)
    character = NATIVE_CHARACTER if mode == "native" else REDUCE_CHARACTER
    return ({**ESTIMATES, "character": character, "animation_base": 0.15, "animation_per_frame": 0.0,
             "revise_area": 0.15, "revise_whole": 0.15, "expression": 0.06, "object": 0.30, "scene": 0.25, "kit": 0.10}
            if mode in ("reduce", "native") else ESTIMATES)


def spend_check(settings, pid=None, aid=None):
    """Refuse to start paid work that a limit would stop anyway (test runs are free)."""
    if settings.get("backend") == "dry_run":
        return
    from ..models import budget_problem
    proj = Project(project_path(settings, pid), overrides(settings)) if pid else None
    if proj is None:
        return
    problem = budget_problem(proj, aid)
    if problem:
        raise ValueError(problem[0].upper() + problem[1:] + ". Nothing was started.")


def key_found():
    from ..openai_backend import find_api_key
    return bool(find_api_key())


def set_api_key(key):
    """Store the key in the user's environment (HKCU\\Environment), where pixelagent reads it. Never logged."""
    if os.name != "nt":
        raise ValueError("saving the key from the app is only supported on Windows; set OPENAI_API_KEY yourself")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "OPENAI_API_KEY", 0, winreg.REG_SZ, key)


# ------------------------------------------------------------------ jobs

class Jobs:
    """One background pipeline run per asset; keeps the last log lines for live progress."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = {}   # (project, asset) -> thread
        self.logs = {}      # (project, asset) -> [lines]
        self.errors = {}

    def is_running(self, key):
        with self.lock:
            t = self.running.get(key)
            return bool(t and t.is_alive())

    def start(self, key, fn, *, cancel_root=None):
        with self.lock:
            t = self.running.get(key)
            if t and t.is_alive():
                raise ValueError("this item is already working")
            from .. import cancel
            if cancel_root is not None:
                cancel.clear(cancel_root, key[1])
            self.logs[key] = []
            self.errors.pop(key, None)

            def run():
                from ..cancel import Cancelled
                try:
                    fn(lambda m: self._log(key, m))
                except Cancelled:  # not an error: the item is paused and Continue picks up where it stopped
                    self._log(key, "cancelled: everything finished so far is kept")
                except Exception as e:  # shown to the user in the item's progress
                    self.errors[key] = f"{type(e).__name__}: {e}"
                    self._log(key, "error: " + self.errors[key])
                    traceback.print_exc()
                finally:
                    if cancel_root is not None:
                        cancel.clear(cancel_root, key[1])
            t = threading.Thread(target=run, daemon=True)
            self.running[key] = t
            t.start()

    def _log(self, key, msg):
        with self.lock:
            lines = self.logs.setdefault(key, [])
            lines.append(f"{time.strftime('%H:%M:%S')}  {msg}")
            del lines[:-200]


JOBS = Jobs()


# ------------------------------------------------------------------ views over the store

def project_path(settings, pid):
    # one plain folder name: no separators, no "." / ".." (or names Windows would trim to them), no control chars
    if (not isinstance(pid, str) or not re.fullmatch(r"[^\\/:*?\"<>|\x00-\x1f]{1,80}", pid)
            or pid != pid.strip(" .") or pid.strip(".") == ""):
        raise ValueError("invalid project name")
    return os.path.join(settings["projects_dir"], pid)


def list_projects(settings):
    d = settings["projects_dir"]
    return sorted(n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n, "assets")))


def file_url(pid, asset, rel):
    return "/files?" + urlencode({"project": pid, "path": f"assets/{asset.id}/{rel}".replace("\\", "/")}, quote_via=quote)


def first_output(asset, pairs):
    for step, key in pairs:
        rec = asset.state.steps.get(step)
        if rec and key in rec.outputs and os.path.exists(asset.path(rec.outputs[key])):
            return rec.outputs[key]
    return None


def thumb_of(asset):
    if asset.state.inputs.get("kind") == "kit":
        return first_output(asset, [("kit_export", "level"), ("kit_review", "level"), ("kit_read", "tileset")])
    if asset.state.inputs.get("kind") == "scene":
        return first_output(asset, [("scene_export", "png"), ("scene_pixel", "composite"), ("scene_layers", "composite")])
    if asset.state.inputs.get("kind") == "animation":
        return first_output(asset, [("anim_export", "gif"), ("anim_draw", "gif"), ("anim_draw", "gif_x4"),
                                    ("anim_traces", "base")])
    out = first_output(asset, [("export", "png"), ("artist", "preview"), ("polish", "preview"), ("concept", "concept"),
                               ("concept", "candidate0")])
    if out:
        return out
    # nothing made yet: show the user's reference image, if they gave one
    refs = [r for r in asset.state.inputs.get("references") or [] if os.path.exists(asset.path(r))]
    return refs[0] if refs else None


def asset_summary(settings, pid, proj, aid, a=None):
    a = a or proj.asset(aid)
    st = a.state
    kind = st.inputs.get("kind") or ("object" if st.inputs.get("asset_type") == "prop" else "character")
    gate = None
    for name, rec in st.steps.items():
        if rec.status == "awaiting_gate":
            gate = name
    failed = [n for n, r in st.steps.items() if r.status == "failed"]
    t = thumb_of(a)
    spec_name = st.inputs.get("name")
    if kind == "animation":
        spec_name = (st.inputs.get("anim") or "animation").replace("_", " ").replace("-", " ").strip().capitalize()
    try:
        if kind in ("scene", "kit"):
            with open(a.output(f"{kind}_plan", "plan"), encoding="utf-8") as f:
                spec_name = json.load(f).get("name") or spec_name
        elif kind != "animation":
            with open(a.output("intake", "spec"), encoding="utf-8") as f:
                spec_name = json.load(f).get("name") or spec_name
    except (KeyError, OSError, ValueError):
        pass
    char_name = None
    if kind == "animation" and st.inputs.get("character"):
        try:
            ca = proj.asset(st.inputs["character"])
            with open(ca.output("intake", "spec"), encoding="utf-8") as f:
                char_name = json.load(f).get("name")
        except (KeyError, OSError, ValueError, FileNotFoundError):
            char_name = st.inputs["character"]
    running = JOBS.is_running((pid, aid))
    return {"id": aid, "kind": kind, "name": spec_name or aid, "character_name": char_name, "status": st.status, "gate": gate,
            "failed": failed[0] if failed else None, "character": st.inputs.get("character"),
            "thumb": file_url(pid, a, t) if t else None, "running": running,
            # stopped between steps (the app was closed mid-run): running it again resumes where it left off
            "paused": not running and not gate and not failed and st.status != "complete",
            "updated": os.path.getmtime(a.state_path), "cost": round(proj.cost_total(aid), 3)}


def asset_detail(settings, pid, proj, aid):
    pipe = Pipeline(proj, aid, mode="dry_run")  # only to read the step list; no model calls happen here
    # ONE read of state.json for the whole view: a run in progress may save between two reads
    a = pipe.asset
    d = asset_summary(settings, pid, proj, aid, a)
    recs = {s.name: a.state.steps.get(s.name) for s in pipe.steps}
    steps = []
    for label, names in STAGES[d["kind"]]:
        st_ = [recs[n].status if recs.get(n) else "pending" for n in names if n in recs]
        errs = [recs[n].error for n in names if recs.get(n) and recs[n].error]
        steps.append({"name": names[0], "label": label, "status": stage_status(st_),
                      "version": sum(recs[n].version for n in names if recs.get(n)), "error": errs[0] if errs else ""})
    d["steps"] = steps
    mat = recs.get("materials")
    if d["kind"] == "character" and mat and (mat.info or {}).get("heads_tall_measured"):
        # the soft look's proportion check (materials.proportion_check): what was measured, and any redo
        d["proportions"] = {"heads_tall": mat.info["heads_tall_measured"],
                            "retries": int(a.state.inputs.get("proportion_retries", 0)),
                            "note": mat.info.get("proportions") or ""}
    crit = a.state.steps.get("critique")
    if d["kind"] in ("character", "object") and crit and crit.status == "done" and "critique" in crit.outputs:
        # the quality check's verdict stays on the page after approval (it was only shown at gate C)
        try:
            with open(a.path(crit.outputs["critique"]), encoding="utf-8") as f:
                c = json.load(f)
            d["quality"] = {"overall": c.get("overall"), "issues": [i["problem"] for i in c.get("issues", [])][:5]}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    d["inputs"] = {k: v for k, v in a.state.inputs.items() if k in ("prompt", "height", "view", "character", "frames", "anim")}
    d["log"] = JOBS.logs.get((pid, aid), [])[-40:]
    from .. import cancel
    d["cancelling"] = d["running"] and cancel.requested(proj.root, aid)
    d["can_revise"] = (d["kind"] in ("character", "object") and not d["running"]
                       and bool((a.state.steps.get("artist") and a.state.steps["artist"].outputs or {}).get("project"))
                       and (d["status"] == "complete" or d.get("gate") == "review"))
    d["job_error"] = JOBS.errors.get((pid, aid))
    gs = pipe.pending_gate()
    if gs:
        rec = a.state.steps[gs.name]
        g = {"id": gs.gate, "kind": GATE_KIND.get(gs.gate, "approve"), "title": gs.gate_title, "step": gs.name}
        o = rec.outputs
        if gs.gate == "A":
            with open(a.output("intake", "spec"), encoding="utf-8") as f:
                g["spec"] = json.load(f)
        elif gs.gate == "B":
            n = rec.info.get("candidates", 1)
            g["candidates"] = [file_url(pid, a, o[f"candidate{i}"]) for i in range(n) if f"candidate{i}" in o]
        elif gs.gate == "C":
            g["image"] = file_url(pid, a, a.state.steps["critique"].outputs["final_1x"])
            try:
                with open(a.output("critique", "critique"), encoding="utf-8") as f:
                    c = json.load(f)
                g["critique"] = {"overall": c.get("overall"), "issues": [i["problem"] for i in c.get("issues", [])][:5]}
            except (KeyError, OSError, ValueError):
                pass
        elif gs.gate == "K":
            rd = a.state.steps["kit_read"]
            g["image"] = file_url(pid, a, a.state.steps["kit_review"].outputs["level"])
            g["tileset"] = file_url(pid, a, rd.outputs["tileset"])
            with open(rd.outputs["tiles"] if os.path.isabs(rd.outputs["tiles"]) else a.path(rd.outputs["tiles"]),
                      encoding="utf-8") as f:
                tm = json.load(f)
            g["decorations"] = [{"name": d["name"], "url": file_url(pid, a, rd.outputs[f"deco{n}"])}
                                for n, d in enumerate(tm.get("decorations", [])) if f"deco{n}" in rd.outputs]
            g["named"] = bool(rd.info.get("named"))
        elif gs.gate == "S":
            px = a.state.steps["scene_pixel"]
            g["image"] = file_url(pid, a, px.outputs["gif"])
            with open(a.output("scene_plan", "plan"), encoding="utf-8") as f:
                plan = json.load(f)
            g["layers"] = [{"name": l["name"], "url": file_url(pid, a, px.outputs[f"layer{i}"]),
                            "scroll": (px.info.get("scroll") or {}).get(l["name"])}
                           for i, l in enumerate(plan["layers"]) if f"layer{i}" in px.outputs]
            g["still"] = file_url(pid, a, px.outputs["composite"])
            g["seams"] = (a.state.steps.get("scene_seams").info or {}).get("seams", [])
        elif gs.gate == "P":
            g["image"] = file_url(pid, a, o["sheet"])
            with open(a.output("anim_plan", "plan"), encoding="utf-8") as f:
                plan = json.load(f)
            g["frames"] = [{"pose": fr["pose"], "ms": fr["duration_ms"]} for fr in plan["frames"]]
        elif gs.gate == "R":
            dr = a.state.steps["anim_draw"].outputs
            g["image"] = file_url(pid, a, dr.get("gif") or dr["gif_x4"])  # 1x when available: the UI zooms by whole numbers
            g["strip"] = file_url(pid, a, dr["strip"])
            g["issues"] = a.state.steps["anim_draw"].info.get("frame_issues", [])
            try:
                with open(a.output("anim_plan", "plan"), encoding="utf-8") as f:
                    ms = [fr["duration_ms"] for fr in json.load(f)["frames"]]
            except (KeyError, OSError, ValueError):
                ms = []
            n = sum(1 for k in dr if re.fullmatch(r"frame\d+", k))
            g["frames"] = [{"url": file_url(pid, a, dr[f"frame{i}"]), "ms": ms[i] if i < len(ms) else None}
                           for i in range(n)]  # empty for animations drawn before per-frame PNGs: the UI shows the strip
        d["gate_detail"] = g
    d["hero"] = d["thumb"]
    step, keys = VERSIONED[d["kind"]]
    vers = []
    for v in pipe.versions(step):
        img = next((v["outputs"][k] for k in keys if k in v["outputs"]), None)
        if img and os.path.exists(a.path(img)):
            vers.append({"version": v["version"], "url": file_url(pid, a, img), "time": v["time"], "current": v["current"],
                         "compatible": v["compatible"]})
    d["versions"] = {"step": step, "items": vers}
    test_mode = settings.get("backend") == "dry_run"
    d["redo"] = {label: (0.0 if test_mode else usd)
                 for label, usd in REDO_USD["character" if d["kind"] == "object" else d["kind"]].items()}
    lv = read_live(a.dir, max_age=900) if d["running"] else None
    if lv:  # the model is drawing right now: its current drawing (never the tracing layer)
        meta, png = lv
        d["live"] = {"url": file_url(pid, a, a.rel(png)) + f"&t={int(meta['updated'] * 1000)}",
                     "step": meta.get("step"), "turn": meta.get("turn"), "frame": meta.get("frame"),
                     "frames": meta.get("frames")}
    refs = [r for r in a.state.inputs.get("references") or [] if os.path.exists(a.path(r))]
    d["reference"] = file_url(pid, a, refs[0]) if refs else None
    exp = a.state.steps.get({"animation": "anim_export", "scene": "scene_export", "kit": "kit_export"}
                            .get(d["kind"], "export"))
    if exp and exp.status == "done":
        d["exports"] = [{"key": k, "label": _export_label(k, d["kind"]), "url": file_url(pid, a, v) if not os.path.isdir(a.path(v)) else None,
                         "path": a.path(v)} for k, v in exp.outputs.items()]
        d["export_dir"] = a.path(a.state.steps[exp.name].info.get("dir", ""))
    if d["kind"] not in ("animation", "scene", "kit"):
        # by the recorded parent, not the id prefix ("knight-2-walk" is not an animation of "knight")
        d["animations"] = [x for x in proj.asset_ids() if x != aid and proj.asset(x).state.inputs.get("character") == aid]
        ej = os.path.join(a.dir, "expressions", "expressions.json")
        if os.path.exists(ej):
            from ..expressions import load_current
            ex = load_current(a)
            d["expressions_stale"] = not bool(ex)
            def expression_url(e, key):
                rel = "expressions/" + e[key]
                revision = e.get("revision") or str(os.stat(a.path(rel)).st_mtime_ns)
                return file_url(pid, a, rel) + "&v=" + quote(str(revision), safe="")
            d["expressions"] = [{"name": n, "url": expression_url(e, "png"),
                                 "x4": expression_url(e, "png_x4"),
                                 "face": expression_url(e, "face"),
                                 "note": e.get("note", ""), "custom": bool(e.get("custom")),
                                 "passed": e.get("passed", True), "issue": (e.get("check") or {}).get("issue", "")}
                                for n, e in ex.get("expressions", {}).items()]
            d["face_box"] = ex.get("face_box")
    return d


def _export_label(k, kind=None):
    if kind == "kit":
        return {"tileset": "Tileset (PNG)", "decorations": "Decorations folder", "decorations_atlas": "Decorations (PNG)",
                "tiled_tileset": "Tiled tileset (.tsx)", "tiled_level": "Tiled test level (.tmx)",
                "godot": "Godot TileSet (.tres)", "level": "Test level (PNG)", "meta": "Metadata (JSON)",
                "readme": "Import notes (README)"}.get(k, k)
    if kind == "scene":
        return {"png": "Backdrop (PNG)", "png_x2": "Backdrop 2× (PNG)", "parallax": "Parallax preview (GIF)",
                "layers": "Layers folder", "meta": "Metadata (JSON)", "godot": "Godot scene (.tscn)",
                "tiled": "Tiled map (.tmx)", "unity": "Unity parallax script (.cs)",
                "readme": "Import notes (README)"}.get(k, k)
    return {"png": "Sprite (PNG)", "png_x4": "Sprite 4× (PNG)", "gif": "Animation (GIF)", "gif_x4": "Animation 4× (GIF)",
            "sheet": "Sprite sheet (PNG)", "frames": "Frames folder", "layers": "Layers folder", "project": "Layered project",
            "meta": "Metadata (JSON)"}.get(k, k)


def _short(info):
    out = {}
    for k, v in info.items():
        s = json.dumps(v, ensure_ascii=False)
        if len(s) <= 160:
            out[k] = v
    return out


# ------------------------------------------------------------------ actions

def start_run(settings, pid, aid, check=True, **kw):
    if check:
        spend_check(settings, pid, aid)
    proj = Project(project_path(settings, pid), overrides(settings))
    proj.asset(aid)  # a valid id of an existing item, checked before anything starts
    JOBS.start((pid, aid), lambda log: Pipeline(proj, aid).run(log=log, **kw), cancel_root=proj.root)


def safe_id(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip().lower()).strip("-")[:40]
    return s or "item"


def unique_id(proj, base):
    ids = set(proj.asset_ids())
    aid, i = base, 2
    while aid in ids:
        aid = f"{base}-{i}"
        i += 1
    return aid


def create_character(settings, body):
    pid = body["project"]
    spend_check(settings, pid)
    proj = Project(project_path(settings, pid), overrides(settings))
    name = (body.get("name") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    refs = []
    if body.get("reference"):
        m = re.match(r"data:image/(png|jpeg|jpg|webp);base64,(.+)", body["reference"], re.S)
        if not m:
            raise ValueError("the reference must be a PNG, JPG or WEBP image")
        up = os.path.join(proj.root, "uploads")
        os.makedirs(up, exist_ok=True)
        p = os.path.join(up, f"ref-{int(time.time())}.{m.group(1).replace('jpeg', 'jpg')}")
        with open(p, "wb") as f:
            f.write(base64.b64decode(m.group(2)))
        refs.append(p)
    obj = body.get("type") == "object"
    if not prompt and not refs:
        raise ValueError(f"describe the {'object' if obj else 'character'} or add a reference image")
    aid = unique_id(proj, safe_id(name or prompt.split(",")[0] or ("object" if obj else "character")))
    inputs = {"prompt": prompt or None, "references": refs, "height": int(body.get("height") or 0) or None,
              "name": name or None}
    if obj:
        inputs.update(asset_type="prop", view="three_quarter")
    proj.new_asset(aid, inputs)
    start_run(settings, pid, aid)
    return {"id": aid}


# a side-scrolling character's usual moves (Alice in Cradle-style action games): name -> (motion, frames)
MOVES = {
    "idle": ("4-frame idle, loops: gentle breathing, the body rises and falls by one pixel, the hair sways slightly", 4),
    "walk": ("6-frame walk cycle in place, loops, side view facing right", 6),
    "run": ("6-frame run cycle in place, loops, side view facing right, leaning forward, longer strides", 6),
    "jump": ("6-frame jump, plays once, side view facing right: crouch, take off, rise, peak, fall, land", 6),
    "attack": ("5-frame attack, plays once, side view facing right: wind up, strike, follow through, recover", 5),
    "hurt": ("3-frame hurt reaction, plays once: flinch and recoil backwards, then recover", 3),
}


def item_sheet(settings, body):
    """Build the project's item sheet (every finished object in one atlas) and return its zip's URL."""
    from .. import itemsheet
    pid = body["project"]
    proj = Project(project_path(settings, pid), overrides(settings))
    _, zpath, summary = itemsheet.build(proj)
    rel = os.path.relpath(zpath, proj.root).replace("\\", "/")
    return {"url": "/files?" + urlencode({"project": pid, "path": rel}, quote_via=quote), **summary}


def game_pack(settings, body):
    """Build a character's game pack (atlas, Aseprite JSON, Godot SpriteFrames, README) and return its zip's URL."""
    from .. import gamepack
    pid, aid = body["project"], body["id"]
    proj = Project(project_path(settings, pid), overrides(settings))
    a = proj.asset(aid)
    _not_running(pid, [aid])
    if a.state.inputs.get("kind") in ("animation", "scene", "kit") or a.state.inputs.get("asset_type") == "prop":
        raise ValueError("a game pack is made for a character (it includes its animations)")
    _, zpath, summary = gamepack.build(proj, aid)
    return {"url": file_url(pid, a, os.path.relpath(zpath, a.dir)), **summary}


def make_expressions(settings, body):
    """Expressions for a finished character (pixel-art look): one masked face edit each, in the background."""
    from .. import expressions
    pid, aid = body["project"], body["id"]
    # the user's own expression: {"prompt": what the face does, "name": optional}; presets by name
    own = body.get("custom") or None
    custom = {}
    if own:
        text = str(own.get("prompt") or "").strip()
        if not text:
            raise ValueError("describe the expression, for example: a smug half-smile with one brow raised")
        custom[expressions.safe_name(own.get("name") or " ".join(text.split()[:3]))] = text
    names = body.get("names")
    if names is None:
        names = [] if custom else list(expressions.EXPRESSIONS)
    if not names and not custom:
        raise ValueError("pick a face or describe one")
    unknown = [n for n in names if n not in expressions.EXPRESSIONS]
    if unknown:
        raise ValueError(f"unknown expression: {', '.join(map(str, unknown))}")
    spend_check(settings, pid, aid)
    proj = Project(project_path(settings, pid), overrides(settings))
    a = proj.asset(aid)
    if a.state.inputs.get("kind") in ("animation", "scene", "kit") or a.state.status != "complete" \
            or a.state.inputs.get("asset_type") == "prop":
        raise ValueError("expressions are made for a finished character")
    if proj.config.get("finish", {}).get("mode") != "native":
        raise ValueError("expressions need the Soft look (pixel-art characters)")
    JOBS.start((pid, aid), lambda log: expressions.make(proj, aid, names, log=log, custom=custom),
               cancel_root=proj.root)
    return {"ok": True, "names": names + list(custom)}


def create_move_set(settings, body):
    """Several animations of one character at once (each still stops for its poses to be approved)."""
    need_animations()
    pid, char = body["project"], body["character"]
    moves = body.get("moves") or list(MOVES)
    unknown = [m for m in moves if m not in MOVES]
    if unknown:
        raise ValueError(f"unknown move: {', '.join(map(str, unknown))}")
    spend_check(settings, pid)
    proj = Project(project_path(settings, pid), overrides(settings))
    a = proj.asset(char)
    if a.state.inputs.get("kind") in ("animation", "scene", "kit") or a.state.inputs.get("asset_type") == "prop":
        raise ValueError("a move set is made for a character")
    ids = []
    for m in dict.fromkeys(moves):
        prompt, frames = MOVES[m]
        aid = unique_id(proj, f"{char}-{m}")
        proj.new_asset(aid, {"kind": "animation", "character": char, "anim": m, "prompt": prompt, "frames": frames})
        start_run(settings, pid, aid)
        ids.append(aid)
    return {"ids": ids}


# Experimental parts of the app, off unless config.toml [app] turns them on (None: read the config; tests set it).
FEATURES = {"backgrounds": None, "animations": None}


def feature_on(name):
    """A part of the app outside the agent's focus (characters and their expressions), kept behind config.toml
    [app] <name>: "backgrounds" (Scenes and Kits) or "animations" (Animations and Move set)."""
    if FEATURES[name] is not None:
        return bool(FEATURES[name])
    from ..config import load_config
    return bool(load_config().get("app", {}).get(name, False))


def backgrounds_on():
    return feature_on("backgrounds")


def need_backgrounds():
    if not backgrounds_on():
        raise ValueError("scenes and kits are switched off (config.toml [app] backgrounds = true turns them on)")


def need_animations():
    if not feature_on("animations"):
        raise ValueError("animations are switched off (config.toml [app] animations = true turns them on)")


def create_kit(settings, body):
    """A walkable kit (tiles and decorations), optionally matched to a finished scene of the project."""
    need_backgrounds()
    pid = body["project"]
    spend_check(settings, pid)
    proj = Project(project_path(settings, pid), overrides(settings))
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("describe the ground, for example: mossy forest ground with roots and stones")
    scene = body.get("scene") or None
    if scene and (scene not in proj.asset_ids() or proj.asset(scene).state.inputs.get("kind") != "scene"):
        raise ValueError("that scene is not in this project")
    name = (body.get("name") or "").strip()
    aid = unique_id(proj, safe_id(name or prompt.split(",")[0] or "kit"))
    proj.new_asset(aid, {"kind": "kit", "prompt": prompt, "name": name or None, "scene": scene})
    start_run(settings, pid, aid)
    return {"id": aid}


def create_scene(settings, body):
    """A scenery backdrop (parallax layers) from the user's prompt."""
    need_backgrounds()
    pid = body["project"]
    spend_check(settings, pid)
    proj = Project(project_path(settings, pid), overrides(settings))
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("describe the place, for example: a forest at dusk with tall old trees")
    name = (body.get("name") or "").strip()
    aid = unique_id(proj, safe_id(name or prompt.split(",")[0] or "scene"))
    proj.new_asset(aid, {"kind": "scene", "prompt": prompt, "name": name or None})
    start_run(settings, pid, aid)
    return {"id": aid}


def create_animation(settings, body):
    need_animations()
    pid = body["project"]
    spend_check(settings, pid)
    proj = Project(project_path(settings, pid), overrides(settings))
    char = body["character"]
    proj.asset(char)
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("describe the animation, for example: 6-frame walk cycle in place")
    aid = unique_id(proj, f"{char}-{safe_id(body.get('name') or prompt.split(',')[0])}")
    frames = int(body["frames"]) if body.get("frames") else None
    proj.new_asset(aid, {"kind": "animation", "character": char, "anim": body.get("name") or None, "prompt": prompt,
                         "frames": frames})
    start_run(settings, pid, aid)
    return {"id": aid}


def decide(settings, body):
    pid, aid = body["project"], body["id"]
    proj = Project(project_path(settings, pid), overrides(settings))
    pipe = Pipeline(proj, aid, mode="dry_run")
    g = pipe.pending_gate()
    if g is None:
        raise ValueError("nothing is waiting for your decision")
    # approving the final sprite or animation only exports (free): never blocked, even at a limit
    paid = not (body.get("approve") and g.gate in ("C", "R"))
    if paid:
        spend_check(settings, pid, aid)  # before changing anything
    if body.get("spec") and g.gate == "A":
        spec = CharacterSpec.model_validate(body["spec"])  # validates before writing
        with open(pipe.asset.output("intake", "spec"), "w", encoding="utf-8") as f:
            f.write(spec.model_dump_json(indent=2))
    approve = bool(body.get("approve"))
    note = (body.get("note") or "").strip()
    if not approve and not note:
        raise ValueError("say what should change")
    pick = body.get("pick")
    from ..revise import clamp_box
    box = None
    if body.get("box") and g.gate == "C":
        pr = pipe.asset.state.steps.get("artist")
        size = image_size(pipe.asset.path(pr.outputs["preview"])) if pr and "preview" in (pr.outputs or {}) else None
        box = clamp_box(body["box"], *size) if size else None
    pipe.decide(approve, int(pick) if pick is not None else None, note, box=box)
    start_run(settings, pid, aid, check=paid)
    return {"ok": True}


# ------------------------------------------------------------------ managing items and projects

def _not_running(pid, aids):
    if any(JOBS.is_running((pid, a)) for a in aids):
        raise ValueError("wait until it has finished working" if len(aids) == 1
                         else "wait until it and its animations have finished working")


def animations_of(proj, aid):
    return [x for x in proj.asset_ids() if x != aid and proj.asset(x).state.inputs.get("character") == aid]


def _set_name(a, name):
    """The name the library shows: the plan's name for characters (once there is a plan), the anim name for animations."""
    if a.state.inputs.get("kind") == "animation":
        a.state.inputs["anim"] = name
        return
    a.state.inputs["name"] = name
    try:
        p = a.output("intake", "spec")
        with open(p, encoding="utf-8") as f:
            spec = json.load(f)
        spec["name"] = name
        with open(p, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
    except (KeyError, OSError, ValueError):
        pass


def rename_item(settings, body):
    pid, aid = body["project"], body["id"]
    name = (body.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("give it a name (up to 80 characters)")
    proj = Project(project_path(settings, pid), overrides(settings))
    a = proj.asset(aid)
    _not_running(pid, [aid])
    _set_name(a, name)
    a.save()
    return {"ok": True}


def duplicate_item(settings, body):
    import shutil
    pid, aid = body["project"], body["id"]
    proj = Project(project_path(settings, pid), overrides(settings))
    a = proj.asset(aid)
    _not_running(pid, [aid])
    label = asset_summary(settings, pid, proj, aid, a)["name"]
    new = unique_id(proj, f"{aid}-copy")
    shutil.copytree(a.dir, os.path.join(proj.root, "assets", new), ignore=shutil.ignore_patterns("live", "*.tmp"))
    b = proj.asset(new)
    b.state.id = new
    b.reset_cost_identity()
    _set_name(b, f"{label} copy")
    b.save()
    return {"id": new}


def trash(settings):
    return RecentlyDeleted(settings["projects_dir"])


def delete_item(settings, body):
    pid, aid = body["project"], body["id"]
    proj = Project(project_path(settings, pid), overrides(settings))
    a = proj.asset(aid)
    kind = a.state.inputs.get("kind", "character")
    anims = animations_of(proj, aid) if kind != "animation" else []
    if anims and not body.get("with_animations"):
        raise ValueError(f"this character has {len(anims)} animation(s); confirm deleting them too")
    _not_running(pid, [aid] + anims)
    summary = asset_summary(settings, pid, proj, aid, a)
    t = thumb_of(a)
    # the character and its animations move together, as one entry that restores as one
    trash(settings).delete_items(proj.root, [aid] + anims,
                                 {"name": summary["name"], "item_kind": kind, "animations": len(anims),
                                  "character_name": summary.get("character_name")},
                                 thumb=a.path(t) if t else None)
    return {"ok": True, "deleted": [aid] + anims}


def _project_busy(pid):
    with JOBS.lock:
        return any(k[0] == pid and t.is_alive() for k, t in JOBS.running.items())


def rename_project(settings, body):
    pid, new = body["project"], (body.get("name") or "").strip()
    src, dst = project_path(settings, pid), project_path(settings, new)
    if not os.path.isdir(os.path.join(src, "assets")):
        raise ValueError("no such project")
    if new == pid:
        return {"id": new}
    if os.path.exists(dst):
        raise ValueError("a project with that name already exists")
    if _project_busy(pid):
        raise ValueError("wait until everything in this project has finished working")
    os.rename(src, dst)
    if settings.get("current_project") == pid:
        settings["current_project"] = new
        save_settings(settings)
    return {"id": new}


def delete_project(settings, body):
    pid = body["project"]
    path = project_path(settings, pid)
    if not os.path.isdir(os.path.join(path, "assets")):
        raise ValueError("no such project")
    if _project_busy(pid):
        raise ValueError("wait until everything in this project has finished working")
    # Upgrade old per-project accounting before its logs leave the searchable project root.
    from ..store import ledger_month_spend
    ledger_month_spend(ledger_path(), settings["projects_dir"])
    proj = Project(path)
    ids = proj.asset_ids()
    thumb = None
    for x in ids:  # the project's first picture, for the Recently Deleted list
        t = thumb_of(proj.asset(x))
        if t:
            thumb = proj.asset(x).path(t)
            break
    chars = sum(1 for x in ids if proj.asset(x).state.inputs.get("kind", "character") != "animation")
    trash(settings).delete_project(path, {"name": pid, "items": len(ids), "characters": chars}, thumb=thumb)
    if settings.get("current_project") == pid:
        settings["current_project"] = None
        save_settings(settings)
    return {"ok": True}


def restore_version(settings, body):
    pid, aid = body["project"], body["id"]
    spend_check(settings, pid, aid)  # before changing anything
    proj = Project(project_path(settings, pid), overrides(settings))
    _not_running(pid, [aid])
    pipe = Pipeline(proj, aid, mode="dry_run")
    step = body.get("step")
    if step not in (s for s, _ in VERSIONED.values()):
        raise ValueError("versions can be restored for the sprite drawing and the animation frames only")
    pipe.restore_version(step, int(body["version"]))
    start_run(settings, pid, aid)  # the cheap later steps run again and stop at the approval
    return {"ok": True}


def redo_from(settings, body):
    pid, aid, label = body["project"], body["id"], body.get("stage")
    spend_check(settings, pid, aid)  # before changing anything
    proj = Project(project_path(settings, pid), overrides(settings))
    _not_running(pid, [aid])
    pipe = Pipeline(proj, aid, mode="dry_run")
    kind = pipe.asset.state.inputs.get("kind", "character")
    names = dict(STAGES[kind]).get(label)
    if not names:
        raise ValueError("unknown stage")
    first = next((n for n in names if n in {s.name for s in pipe.steps}), None)
    pipe.reset_from(first)
    start_run(settings, pid, aid)
    return {"ok": True}


def trash_list(settings):
    out = []
    for e in trash(settings).entries():
        out.append({"entry": e["entry"], "kind": e["kind"], "name": e.get("name"), "project": e.get("project"),
                    "item_kind": e.get("item_kind"), "animations": e.get("animations", 0), "items": e.get("items"),
                    "character_name": e.get("character_name"), "deleted_at": e.get("deleted_at"),
                    "thumb": f"/trashfile?entry={e['entry']}&name={e['thumb']}" if e.get("thumb") else None})
    return {"items": out}


def trash_restore(settings, body):
    r = trash(settings).restore(body["entry"])
    if r["kind"] == "project" and not settings.get("current_project"):
        settings["current_project"] = r["project"]
        save_settings(settings)
    return r


def image_size(path):
    with Image.open(path) as im:  # closed at once: an open file would block moving the item on Windows
        return im.size


def cancel_item(settings, body):
    pid, aid = body["project"], body["id"]
    proj = Project(project_path(settings, pid), overrides(settings))
    proj.asset(aid)
    if not JOBS.is_running((pid, aid)):
        raise ValueError("nothing is running for this item")
    from .. import cancel
    cancel.request(proj.root, aid)
    return {"ok": True}


def fix_item(settings, body):
    """Revise a finished character (or one waiting for approval) with a note, optionally only inside an area."""
    pid, aid = body["project"], body["id"]
    note = (body.get("note") or "").strip()
    if not note:
        raise ValueError("say what should change")
    spend_check(settings, pid, aid)
    proj = Project(project_path(settings, pid), overrides(settings))
    _not_running(pid, [aid])
    pipe = Pipeline(proj, aid, mode="dry_run")
    g = pipe.pending_gate()
    if g is not None and g.gate == "C":
        return decide(settings, {"project": pid, "id": aid, "approve": False, "note": note, "box": body.get("box")})
    rec = pipe.asset.state.steps.get("artist")
    if g is not None or not rec or rec.status != "done":
        raise ValueError("only a finished sprite can be revised")
    from ..revise import clamp_box
    box = clamp_box(body.get("box"), *image_size(pipe.asset.path(rec.outputs["preview"]))) if body.get("box") else None
    target = pipe.request_fix(note, box)
    if not target:
        raise ValueError("this sprite has no drawing to revise; use Redo from Drawing instead")
    pipe.asset.state.inputs.setdefault("notes", {}).setdefault("artist", []).append(note)
    pipe.asset.save()
    pipe.reset_from(target)   # 'concept' in the reduce finish: the concept is edited and reduced again
    start_run(settings, pid, aid)
    return {"ok": True}


def open_item(settings, body):
    pid, aid = body["project"], body["id"]
    proj = Project(project_path(settings, pid), overrides(settings))
    d = asset_detail(settings, pid, proj, aid)
    if body.get("what") == "folder":
        target = d.get("export_dir") or proj.asset(aid).dir
        os.startfile(target) if os.name == "nt" else subprocess.Popen(["xdg-open", target])
        return {"ok": True}
    exe = proj.config["engine"]["pixelliner_exe"]
    if not os.path.exists(exe):
        raise ValueError(f"PixelLiner was not found at {exe}")
    files = {e["key"]: e["path"] for e in d.get("exports", [])}
    target = files.get("gif") or files.get("png")
    if not target:
        raise ValueError("export this item first")
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen([exe, target], cwd=os.path.dirname(exe), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "PixelAgent"

    def log_message(self, fmt, *args):  # quiet console
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, cache=False):
        if not os.path.isfile(path):
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        # DNS rebinding: a web page whose own domain re-resolves to 127.0.0.1 arrives with its domain in Host;
        # only literal loopback names are this app
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _guard_origin(self):
        # only the app's own page may call the API (no cross-site requests from other local pages)
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{self.headers.get('Host', '')}"

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "forbidden"}, 403)
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        s = load_settings()
        try:
            if u.path == "/" or u.path == "/index.html":
                return self._file(os.path.join(STATIC, "index.html"))
            if u.path.startswith("/static/"):
                p = os.path.normpath(os.path.join(STATIC, u.path[len("/static/"):]))
                if not p.startswith(os.path.normpath(STATIC) + os.sep):
                    return self.send_error(403)
                return self._file(p)
            if u.path == "/files":
                root = os.path.normpath(project_path(s, q["project"]))
                p = os.path.normpath(os.path.join(root, q["path"]))
                if not p.startswith(root + os.sep):
                    return self.send_error(403)
                return self._file(p, cache=True)
            if u.path == "/trashfile":  # thumbnails of deleted items
                return self._file(trash(s).path(q["entry"], q["name"]), cache=True)
            if u.path == "/api/trash":
                return self._json(trash_list(s))
            if u.path == "/api/ping":  # heartbeat from an open window (keeps the packaged app alive)
                return self._json({"ok": True})
            if u.path == "/api/overview":
                projects = list_projects(s)
                cur = s.get("current_project") if s.get("current_project") in projects else (projects[0] if projects else None)
                from ..store import ledger_month_spend
                spent = ledger_month_spend(ledger_path(), seed_dir=s["projects_dir"])
                return self._json({"projects": projects, "current": cur, "settings": {
                    "backend": s["backend"], "monthly_usd": s["monthly_usd"], "asset_usd": s.get("asset_usd", 3.0),
                    "sprite_height": s["sprite_height"], "projects_dir": s["projects_dir"], "key_found": key_found(),
                    "look": s.get("look", "soft"), "grade": s.get("grade", "medium"),
                    "prop_grade": s.get("prop_grade", "soft")},
                    "month_spent": round(spent, 2), "estimates": estimates(s),
                    "features": {"backgrounds": backgrounds_on(), "animations": feature_on("animations")},
                    "trash_count": len(trash(s).entries())})
            if u.path == "/api/assets":
                proj = Project(project_path(s, q["project"]))
                items = [asset_summary(s, q["project"], proj, aid) for aid in proj.asset_ids()]
                items.sort(key=lambda x: -x["updated"])
                return self._json({"items": items})
            if u.path == "/api/asset":
                proj = Project(project_path(s, q["project"]))
                if q["id"] not in proj.asset_ids():  # deleted, or it belongs to another project
                    return self._json({"error": "not found", "not_found": True}, 404)
                return self._json(asset_detail(s, q["project"], proj, q["id"]))
            return self.send_error(404)
        except (KeyError, ValueError, FileNotFoundError) as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        if not self._host_ok() or not self._guard_origin():
            return self._json({"error": "forbidden"}, 403)
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except ValueError:
            return self._json({"error": "invalid JSON"}, 400)
        s = load_settings()
        try:
            if u.path == "/api/projects":
                name = (body.get("name") or "").strip()
                if not name:
                    raise ValueError("give the project a name")
                Project(project_path(s, name))
                s["current_project"] = name
                save_settings(s)
                return self._json({"id": name})
            if u.path == "/api/current":
                s["current_project"] = body["project"]
                save_settings(s)
                return self._json({"ok": True})
            if u.path == "/api/characters":
                return self._json(create_character(s, body))
            if u.path == "/api/animations":
                return self._json(create_animation(s, body))
            if u.path == "/api/movesets":
                return self._json(create_move_set(s, body))
            if u.path == "/api/kits":
                return self._json(create_kit(s, body))
            if u.path == "/api/scenes":
                return self._json(create_scene(s, body))
            if u.path == "/api/itemsheet":
                return self._json(item_sheet(s, body))
            if u.path == "/api/gamepack":
                return self._json(game_pack(s, body))
            if u.path == "/api/expressions":
                return self._json(make_expressions(s, body))
            if u.path == "/api/decide":
                return self._json(decide(s, body))
            if u.path == "/api/run":
                start_run(s, body["project"], body["id"])
                return self._json({"ok": True})
            if u.path == "/api/open":
                return self._json(open_item(s, body))
            if u.path == "/api/versions/restore":
                return self._json(restore_version(s, body))
            if u.path == "/api/redo":
                return self._json(redo_from(s, body))
            if u.path == "/api/cancel":
                return self._json(cancel_item(s, body))
            if u.path == "/api/fix":
                return self._json(fix_item(s, body))
            if u.path == "/api/trash/restore":
                return self._json(trash_restore(s, body))
            if u.path == "/api/trash/delete":  # permanent: only from the user's explicit, confirmed action
                trash(s).delete_now(body["entry"])
                return self._json({"ok": True})
            if u.path == "/api/trash/empty":  # permanent: only from the user's explicit, confirmed action
                return self._json({"removed": trash(s).empty()})
            if u.path == "/api/item/rename":
                return self._json(rename_item(s, body))
            if u.path == "/api/item/duplicate":
                return self._json(duplicate_item(s, body))
            if u.path == "/api/item/delete":
                return self._json(delete_item(s, body))
            if u.path == "/api/project/rename":
                return self._json(rename_project(s, body))
            if u.path == "/api/project/delete":
                return self._json(delete_project(s, body))
            if u.path == "/api/project/open":
                path = project_path(s, body["project"])
                if not os.path.isdir(path):
                    raise ValueError("no such project")
                os.startfile(path) if os.name == "nt" else subprocess.Popen(["xdg-open", path])
                return self._json({"ok": True})
            if u.path == "/api/settings":
                for k in ("backend", "monthly_usd", "asset_usd", "sprite_height", "projects_dir", "look", "grade", "prop_grade"):
                    if k in body and body[k] not in (None, ""):
                        s[k] = body[k]
                if s["backend"] not in ("openai", "dry_run"):
                    raise ValueError("unknown backend")
                if s.get("look", "soft") not in LOOKS:
                    raise ValueError("choose the soft or the classic look")
                if s.get("grade", "medium") not in ("none", "soft", "medium", "strong") or \
                        s.get("prop_grade", "soft") not in ("none", "soft", "medium", "strong"):
                    raise ValueError("choose a colour grade: none, soft, medium or strong")
                try:
                    s["monthly_usd"] = float(s["monthly_usd"])
                    s["asset_usd"] = float(s.get("asset_usd", 3.0))
                    s["sprite_height"] = int(s["sprite_height"])
                except (TypeError, ValueError):
                    raise ValueError("the monthly limit and height must be numbers")
                if not (0 < s["monthly_usd"] <= 10000) or s["sprite_height"] not in (64, 96, 128, 160):
                    raise ValueError("choose a monthly limit above 0 and a height of 64, 96, 128 or 160 px")
                if not (0.5 <= s["asset_usd"] <= 100):
                    raise ValueError("choose a limit per item between $0.50 and $100")
                if body.get("api_key"):
                    set_api_key(body["api_key"].strip())
                save_settings(s)
                return self._json({"ok": True, "key_found": key_found()})
            return self.send_error(404)
        except (KeyError, ValueError, FileNotFoundError) as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)


APP_PORT = 8765           # the app's usual address, so a second launch finds the first one
IDLE_QUIT_SECONDS = 180  # the open page polls every few seconds; this long without it (and no run working) = closed
LAST_SEEN = [time.time()]


def find_app_browser():
    """Edge (always on Windows 11) or Chrome, for a chromeless app window via --app."""
    if os.name != "nt":
        return None
    import winreg
    for exe in ("msedge.exe", "chrome.exe"):
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                    path = winreg.QueryValue(k, None)
                if path and os.path.exists(path):
                    return path
            except OSError:
                pass
    for path in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"):
        path = os.path.expandvars(path)
        if os.path.exists(path):
            return path
    return None


def open_window(url):
    browser = find_app_browser()
    if browser:
        try:
            subprocess.Popen([browser, f"--app={url}", "--window-size=1320,860"], close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    webbrowser.open(url)


def running_instance(port):
    """True if PixelAgent already answers on this port (then we only open another window to it)."""
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=1.5)
        c.request("GET", "/api/overview", headers={"Host": f"127.0.0.1:{port}"})
        r = c.getresponse()
        return r.status == 200 and r.getheader("Server", "").startswith("PixelAgent")
    except OSError:
        return False


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    # on Windows SO_REUSEADDR lets a second process bind a port that is in use; the app must own its port
    allow_reuse_address = False
    allow_reuse_port = False

    def process_request(self, request, client_address):
        LAST_SEEN[0] = time.time()
        super().process_request(request, client_address)


def _quit_when_idle(httpd):
    while True:
        time.sleep(15)
        idle = time.time() - LAST_SEEN[0]
        busy = any(t.is_alive() for t in list(JOBS.running.values()))
        if idle > IDLE_QUIT_SECONDS and not busy:
            print("No window has been open for a while and nothing is running: quitting.", flush=True)
            httpd.shutdown()
            return


def serve(port=0, open_browser=True, quit_when_idle=False):
    httpd = None
    if port == APP_PORT and running_instance(port):
        print("PixelAgent is already running; opening a window to it.", flush=True)
        if open_browser:
            open_window(f"http://127.0.0.1:{port}/")
        return None
    try:
        httpd = AppServer(("127.0.0.1", port), Handler)
    except OSError:
        if port != APP_PORT:
            raise
        httpd = AppServer(("127.0.0.1", 0), Handler)  # the usual port is taken by another program
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"PixelAgent is running at {url}", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: open_window(url)).start()
    if quit_when_idle:
        threading.Thread(target=_quit_when_idle, args=(httpd,), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return httpd


def main(argv=None):
    from ..config import FROZEN
    argv = list(sys.argv[1:] if argv is None else argv)
    port = APP_PORT if FROZEN else 0
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    serve(port, open_browser="--no-browser" not in argv, quit_when_idle=FROZEN or "--quit-when-idle" in argv)
