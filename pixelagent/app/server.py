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

from ..config import resource_path
from ..pipeline import Pipeline
from ..schemas import CharacterSpec
from ..store import Project

STATIC = resource_path("pixelagent", "app", "static")
SETTINGS_FILE = os.environ.get("PIXELAGENT_SETTINGS") or os.path.join(os.path.expanduser("~"), ".pixelagent", "settings.json")

STEP_LABELS = {
    "intake": "Plan", "concept": "Concept", "matmap": "Material map", "pixelize": "Pixel grid",
    "materials": "Colours", "rebuild": "Shading", "lines": "Outline", "detail": "Detail", "polish": "Polish",
    "artist": "Drawing", "critique": "Quality check", "review": "Your approval", "animate": "Animation",
    "export": "Export", "anim_plan": "Plan", "anim_poses": "Poses", "anim_traces": "Frame traces",
    "anim_draw": "Drawing frames", "anim_review": "Your approval", "anim_export": "Export",
}
GATE_KIND = {"A": "spec", "B": "concept", "C": "sprite", "P": "poses", "R": "animation"}
# what the user sees as progress: milestones, not internal steps
STAGES = {
    "character": [("Plan", ["intake"]), ("Concept", ["concept"]),
                  ("Pixel conversion", ["matmap", "pixelize", "materials", "rebuild", "lines", "detail", "polish"]),
                  ("Drawing", ["artist"]), ("Quality check", ["critique"]), ("Your approval", ["review"]),
                  ("Export", ["animate", "export"])],
    "animation": [("Plan", ["anim_plan"]), ("Poses", ["anim_poses"]), ("Frame traces", ["anim_traces"]),
                  ("Drawing frames", ["anim_draw"]), ("Your approval", ["anim_review"]), ("Export", ["anim_export"])],
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
            "backend": "openai", "monthly_usd": 90.0, "sprite_height": 96, "current_project": None}


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


def overrides(s):
    return {"backend": {"mode": s["backend"]}, "budget": {"monthly_usd": float(s["monthly_usd"])},
            "style": {"sprite_height": int(s["sprite_height"])}}


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

    def start(self, key, fn):
        with self.lock:
            t = self.running.get(key)
            if t and t.is_alive():
                raise ValueError("this item is already working")
            self.logs[key] = []
            self.errors.pop(key, None)

            def run():
                try:
                    fn(lambda m: self._log(key, m))
                except Exception as e:  # shown to the user in the item's progress
                    self.errors[key] = f"{type(e).__name__}: {e}"
                    self._log(key, "error: " + self.errors[key])
                    traceback.print_exc()
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
    if asset.state.inputs.get("kind") == "animation":
        return first_output(asset, [("anim_export", "gif"), ("anim_draw", "gif_x4"), ("anim_traces", "base")])
    return first_output(asset, [("export", "png"), ("artist", "preview"), ("polish", "preview"), ("concept", "concept"),
                                ("concept", "candidate0")])


def asset_summary(settings, pid, proj, aid):
    a = proj.asset(aid)
    st = a.state
    kind = st.inputs.get("kind", "character")
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
        if kind != "animation":
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
    a = proj.asset(aid)
    pipe = Pipeline(proj, aid, mode="dry_run")  # only to read the step list; no model calls happen here
    d = asset_summary(settings, pid, proj, aid)
    recs = {s.name: a.state.steps.get(s.name) for s in pipe.steps}
    steps = []
    for label, names in STAGES[d["kind"]]:
        st_ = [recs[n].status if recs.get(n) else "pending" for n in names if n in recs]
        errs = [recs[n].error for n in names if recs.get(n) and recs[n].error]
        steps.append({"name": names[0], "label": label, "status": stage_status(st_),
                      "version": sum(recs[n].version for n in names if recs.get(n)), "error": errs[0] if errs else ""})
    d["steps"] = steps
    d["inputs"] = {k: v for k, v in a.state.inputs.items() if k in ("prompt", "height", "view", "character", "frames", "anim")}
    d["log"] = JOBS.logs.get((pid, aid), [])[-40:]
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
        elif gs.gate == "P":
            g["image"] = file_url(pid, a, o["sheet"])
            with open(a.output("anim_plan", "plan"), encoding="utf-8") as f:
                plan = json.load(f)
            g["frames"] = [{"pose": fr["pose"], "ms": fr["duration_ms"]} for fr in plan["frames"]]
        elif gs.gate == "R":
            dr = a.state.steps["anim_draw"].outputs
            g["image"] = file_url(pid, a, dr["gif_x4"])
            g["strip"] = file_url(pid, a, dr["strip"])
            g["issues"] = a.state.steps["anim_draw"].info.get("frame_issues", [])
        d["gate_detail"] = g
    d["hero"] = d["thumb"]
    exp = a.state.steps.get("anim_export" if d["kind"] == "animation" else "export")
    if exp and exp.status == "done":
        d["exports"] = [{"key": k, "label": _export_label(k), "url": file_url(pid, a, v) if not os.path.isdir(a.path(v)) else None,
                         "path": a.path(v)} for k, v in exp.outputs.items()]
        d["export_dir"] = a.path(a.state.steps[exp.name].info.get("dir", ""))
    if d["kind"] != "animation":
        # by the recorded parent, not the id prefix ("knight-2-walk" is not an animation of "knight")
        d["animations"] = [x for x in proj.asset_ids() if x != aid and proj.asset(x).state.inputs.get("character") == aid]
    return d


def _export_label(k):
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

def start_run(settings, pid, aid, **kw):
    proj = Project(project_path(settings, pid), overrides(settings))

    def job(log):
        Pipeline(proj, aid).run(log=log, **kw)
    JOBS.start((pid, aid), job)


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
    if not prompt and not refs:
        raise ValueError("describe the character or add a reference image")
    aid = unique_id(proj, safe_id(name or prompt.split(",")[0] or "character"))
    proj.new_asset(aid, {"prompt": prompt or None, "references": refs, "height": int(body.get("height") or 0) or None,
                         "name": name or None})
    start_run(settings, pid, aid)
    return {"id": aid}


def create_animation(settings, body):
    pid = body["project"]
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
    if body.get("spec") and g.gate == "A":
        spec = CharacterSpec.model_validate(body["spec"])  # validates before writing
        with open(pipe.asset.output("intake", "spec"), "w", encoding="utf-8") as f:
            f.write(spec.model_dump_json(indent=2))
    approve = bool(body.get("approve"))
    note = (body.get("note") or "").strip()
    if not approve and not note:
        raise ValueError("say what should change")
    pick = body.get("pick")
    pipe.decide(approve, int(pick) if pick is not None else None, note)
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
            if u.path == "/api/ping":  # heartbeat from an open window (keeps the packaged app alive)
                return self._json({"ok": True})
            if u.path == "/api/overview":
                projects = list_projects(s)
                cur = s.get("current_project") if s.get("current_project") in projects else (projects[0] if projects else None)
                spent = 0.0
                for p in projects:
                    spent += Project(project_path(s, p)).cost_total(month=True)
                return self._json({"projects": projects, "current": cur, "settings": {
                    "backend": s["backend"], "monthly_usd": s["monthly_usd"], "sprite_height": s["sprite_height"],
                    "projects_dir": s["projects_dir"], "key_found": key_found()}, "month_spent": round(spent, 2)})
            if u.path == "/api/assets":
                proj = Project(project_path(s, q["project"]))
                items = [asset_summary(s, q["project"], proj, aid) for aid in proj.asset_ids()]
                items.sort(key=lambda x: -x["updated"])
                return self._json({"items": items})
            if u.path == "/api/asset":
                proj = Project(project_path(s, q["project"]))
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
            if u.path == "/api/decide":
                return self._json(decide(s, body))
            if u.path == "/api/run":
                start_run(s, body["project"], body["id"])
                return self._json({"ok": True})
            if u.path == "/api/open":
                return self._json(open_item(s, body))
            if u.path == "/api/settings":
                for k in ("backend", "monthly_usd", "sprite_height", "projects_dir"):
                    if k in body and body[k] not in (None, ""):
                        s[k] = body[k]
                if s["backend"] not in ("openai", "dry_run"):
                    raise ValueError("unknown backend")
                try:
                    s["monthly_usd"] = float(s["monthly_usd"])
                    s["sprite_height"] = int(s["sprite_height"])
                except (TypeError, ValueError):
                    raise ValueError("the monthly limit and height must be numbers")
                if not (0 < s["monthly_usd"] <= 10000) or s["sprite_height"] not in (64, 96, 128):
                    raise ValueError("choose a monthly limit above 0 and a height of 64, 96 or 128 px")
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
