"""Pipeline runner: ordered steps, versioned outputs, approval gates, resume, rerun-from."""
import importlib
import json
import os
import time
import re
import traceback
from dataclasses import dataclass
from typing import Callable, Optional

from .config import load_engine
from .models import make_backend
from .schemas import CharacterSpec, GateDecision


@dataclass
class Step:
    index: int
    name: str
    run: Callable
    gate: Optional[str] = None            # gate id shown to the user, e.g. "A"
    gate_title: str = ""
    after_gate: Optional[Callable] = None  # applies the user's decision (e.g. the picked candidate)
    redo_from: Optional[str] = None        # on reject: rerun from this (earlier) step, with the user's note


class Ctx:
    """Everything a step needs. Steps put file outputs in self.outputs and small facts in self.info."""

    def __init__(self, project, asset, backend, step, out_dir):
        self.project, self.asset, self.backend, self.step = project, asset, backend, step
        self.cfg = project.config
        self.style = self.cfg["style"]
        self.out = out_dir
        self.outputs = {}
        self.info = {}
        self.needs_gate = bool(step.gate)
        self.skipped = False
        self.log = print  # replaced by the run's log (the app shows it as Activity)
        load_engine(self.cfg)
        self.canvas = importlib.import_module("canvas")
        self.art = importlib.import_module("art")
        self.projectio = importlib.import_module("project")

    # --- inputs from earlier steps
    def spec(self):
        with open(self.asset.output("intake", "spec"), encoding="utf-8") as f:
            return CharacterSpec.model_validate_json(f.read())

    def input(self, step, key):
        return self.asset.output(step, key)

    def notes(self):
        return self.asset.state.inputs.get("notes", {}).get(self.step.name, [])

    # --- outputs
    def path(self, name):
        # every output stays inside this step's version folder, whatever a name contains (review finding F04)
        root = os.path.normpath(self.out)
        p = os.path.normpath(os.path.join(root, name))
        if not p.startswith(root + os.sep):
            raise ValueError(f"output name {name!r} would leave the step's folder")
        return p

    def save_json(self, key, name, obj):
        p = self.path(name)
        with open(p, "w", encoding="utf-8") as f:
            if hasattr(obj, "model_dump_json"):
                f.write(obj.model_dump_json(indent=2))
            else:
                json.dump(obj, f, indent=2, ensure_ascii=False)
        self.outputs[key] = p
        return p

    def save_image(self, key, name, im):
        p = self.path(name)
        im.save(p)
        if key:
            self.outputs[key] = p
        return p

    def save_project(self, key, doc, name="project"):
        p = os.path.join(self.out, name)
        self.projectio.save_project(doc, p)
        self.outputs[key] = p
        return p

    def load_project(self, step, key="project"):
        return self.projectio.load_project(self.input(step, key))


def build_steps(kind="character"):
    from . import steps
    return {"animation": steps.ANIM_STEPS, "scene": steps.SCENE_STEPS}.get(kind, steps.STEPS)


class Pipeline:
    def __init__(self, project, asset_id, mode=None, client=None):
        self.project = project
        self.asset = project.asset(asset_id)
        self.backend = make_backend(project, asset_id, mode, client)
        self.steps = build_steps(self.asset.state.inputs.get("kind", "character"))

    def step(self, name):
        for s in self.steps:
            if s.name == name:
                return s
        raise KeyError(f"no step '{name}' (steps: {[s.name for s in self.steps]})")

    def reset_from(self, name):
        """Mark a step and every later step pending (old versions stay on disk)."""
        start = self.step(name).index
        for s in self.steps:
            if s.index >= start:
                rec = self.asset.record(s.name)
                rec.status, rec.error = "pending", ""
                if s.gate:
                    self.asset.state.gates.pop(s.gate, None)
        self.asset.save()

    def upstream(self, name):
        """The exact versions (folders) of the earlier steps that a run of `name` is made from."""
        out = {}
        for s in self.steps:
            if s.name == name:
                break
            rec = self.asset.state.steps.get(s.name)
            if rec and rec.status in ("done", "skipped", "awaiting_gate"):
                out[s.name] = rec.info.get("dir", "")
        return out

    def versions(self, name):
        """Finished versions of a step, oldest first: [{version, dir, outputs, time, current}]."""
        s = self.step(name)
        rec = self.asset.state.steps.get(name)
        root = os.path.join(self.asset.dir, "steps", f"{s.index:02d}_{name}")
        if not rec or not os.path.isdir(root):
            return []
        cur_dir = rec.info.get("dir", "").replace("\\", "/").rstrip("/")
        out = []
        for d in sorted(os.listdir(root)):
            m = re.fullmatch(r"v(\d{3})", d)
            if not m:
                continue
            vdir = os.path.join(root, d)
            rel_dir = self.asset.rel(vdir)
            outputs, upstream = None, None
            try:
                with open(os.path.join(vdir, "outputs.json"), encoding="utf-8") as f:
                    saved = json.load(f)
                outputs, upstream = saved["outputs"], saved.get("upstream")
            except (OSError, ValueError, KeyError):
                if cur_dir and rec.outputs:  # versions made before outputs.json: same file names, other folder
                    outputs = {k: v.replace(cur_dir + "/", rel_dir + "/", 1) for k, v in rec.outputs.items()
                               if v.startswith(cur_dir + "/")}
                    outputs = {k: v for k, v in outputs.items() if os.path.exists(self.asset.path(v))}
            if not outputs:
                continue  # a run that failed or stopped before finishing
            out.append({"version": int(m.group(1)), "dir": rel_dir, "outputs": outputs, "upstream": upstream,
                        "time": os.path.getmtime(vdir), "current": rel_dir == cur_dir})
        now = self.upstream(name)
        for v in out:
            # usable now only if made from the same plan, concept, traces... as the current one; versions made
            # before this was recorded (no 'upstream') are accepted only when they are the current one
            v["compatible"] = v["current"] or (v["upstream"] is not None and v["upstream"] == now)
        return out

    def restore_version(self, name, version):
        """Make an older finished version of a step current again; every later step runs again (they are cheap
        after the artist: critique, the review gate, export). Newer versions stay on disk."""
        v = next((x for x in self.versions(name) if x["version"] == int(version)), None)
        if v is None:
            raise ValueError(f"no finished version {version} of {name}")
        if not v["compatible"]:
            raise ValueError(f"version {version} was made from an earlier plan, concept or pose sheet, so it does not "
                             "fit the current one; redo from that step instead")
        rec = self.asset.record(name)
        rec.outputs = dict(v["outputs"])
        rec.info["dir"] = v["dir"]
        rec.status, rec.error = "done", ""
        later = [s for s in self.steps if s.index > self.step(name).index]
        if later:
            self.reset_from(later[0].name)
        else:
            self.asset.save()

    def pending_gate(self):
        for s in self.steps:
            rec = self.asset.state.steps.get(s.name)
            if rec and rec.status == "awaiting_gate":
                return s
        return None

    def request_fix(self, note, box=None):
        """Revise the current sprite with a note (optionally only inside box) instead of starting over. Returns the
        step to redo from ('artist': the model edits the drawing; 'concept': in the reduce finish the concept is
        edited and reduced again), or False when there is nothing to revise."""
        st = self.asset.state.steps
        if self.project.config.get("finish", {}).get("mode") in ("reduce", "native") and "concept" in st:
            conc = (st["concept"].outputs or {}).get("concept")
            if not conc or not os.path.exists(self.asset.path(conc)):
                return False
            pix = st.get("pixelize")
            geom = None
            if pix and (pix.outputs or {}).get("geom") and os.path.exists(self.asset.path(pix.outputs["geom"])):
                with open(self.asset.path(pix.outputs["geom"]), encoding="utf-8") as f:
                    geom = json.load(f)
            self.asset.state.inputs["concept_fix"] = {
                "note": note, "box": list(box) if box else None, "from_concept": conc, "geom": geom,
                "offsets": list((pix.info or {}).get("offsets") or []) if pix else None,
                "grid": (pix.info or {}).get("grid") if pix else None,
                "from_project": (st["artist"].outputs or {}).get("project") if "artist" in st else None}
            self.asset.save()
            return "concept"
        rec = self.asset.state.steps.get("artist")
        proj = (rec.outputs or {}).get("project") if rec else None
        if not proj or not os.path.exists(os.path.join(self.asset.path(proj), "project.json")):
            return False
        self.asset.state.inputs["fix"] = {"note": note, "box": list(box) if box else None, "from_project": proj}
        self.asset.save()
        return "artist"

    def decide(self, approved, pick=None, note="", box=None):
        s = self.pending_gate()
        if s is None:
            raise ValueError("no gate is waiting for a decision")
        self.asset.state.gates[s.gate] = GateDecision(gate=s.gate, approved=approved, pick=pick, note=note)
        if not approved:
            notes = self.asset.state.inputs.setdefault("notes", {})
            notes.setdefault(s.name, []).append(note)
            if s.redo_from:
                notes.setdefault(s.redo_from, []).append(note)
                target = s.redo_from
                if s.redo_from == "artist":  # revise the current sprite rather than starting it again
                    target = self.request_fix(note, box) or s.redo_from
                self.asset.save()
                self.reset_from(target)
                return s
            rec = self.asset.record(s.name)
            rec.status = "pending"
            self.asset.state.gates.pop(s.gate, None)
        self.asset.save()
        return s

    def run(self, auto_approve=False, until=None, log=None):
        log = log or (lambda msg: print(msg, flush=True))  # flush: progress shows even when output is redirected
        from .review import write_review
        st = self.asset.state
        from . import cancel
        for s in self.steps:
            rec = self.asset.record(s.name)
            if rec.status in ("done", "skipped"):
                continue
            if cancel.requested(self.project.root, self.asset.id):  # the user cancelled: stop before the next step
                cancel.clear(self.project.root, self.asset.id)
                st.status = "cancelled"
                self.asset.save()
                raise cancel.Cancelled("cancelled")
            if rec.status in ("pending", "failed"):
                out = self.asset.new_version_dir(s.index, s.name)
                ctx = Ctx(self.project, self.asset, self.backend, s, out)
                ctx.log = log
                log(f"[{s.index:02d} {s.name}] running (v{rec.version:03d})")
                try:
                    s.run(ctx)
                except cancel.Cancelled:
                    # not an error: the step runs again on Continue (drawing in progress was checkpointed)
                    cancel.clear(self.project.root, self.asset.id)
                    rec.status, rec.error = "pending", ""
                    st.status = "cancelled"
                    self.asset.save()
                    raise
                except Exception as e:
                    rec.status, rec.error = "failed", f"{type(e).__name__}: {e}"
                    rec.info = {"traceback": traceback.format_exc(limit=6)}
                    st.status = f"failed at {s.name}"
                    self.asset.save()
                    write_review(self)
                    raise
                rec.outputs = {k: self.asset.rel(v) for k, v in ctx.outputs.items()}
                rec.info, rec.error = ctx.info, ""
                rec.info["dir"] = self.asset.rel(out)
                try:  # each version folder remembers its outputs and what it was made from (restores, finding F05)
                    with open(os.path.join(out, "outputs.json"), "w", encoding="utf-8") as f:
                        json.dump({"version": rec.version, "outputs": rec.outputs, "time": time.time(),
                                   "upstream": self.upstream(s.name)}, f, indent=1)
                except OSError:
                    pass
                if ctx.skipped:
                    rec.status = "skipped"
                    log(f"[{s.index:02d} {s.name}] skipped: {ctx.info.get('reason', '')}")
                elif s.gate and ctx.needs_gate:
                    rec.status = "awaiting_gate"
                else:
                    rec.status = "done"
                self.asset.save()
                redo = rec.info.pop("redo_from", None) if rec.status == "done" else None
                if redo:
                    # a step found its input unusable (e.g. the concept's proportions): redo from that step, once per
                    # request (the step keeps count); at a gate the new result waits for approval as usual
                    self.asset.save()
                    log(f"[{s.index:02d} {s.name}] {rec.info.get('proportions', 'redo requested')} -> redoing from {redo}")
                    self.reset_from(redo)
                    return self.run(auto_approve=auto_approve, until=until, log=log)
            if rec.status == "awaiting_gate":
                dec = st.gates.get(s.gate)
                if dec is None and auto_approve:
                    dec = GateDecision(gate=s.gate, approved=True, pick=rec.info.get("suggested"), note="auto-approved")
                    st.gates[s.gate] = dec
                if dec is None or not dec.approved:
                    st.status = f"awaiting gate {s.gate}: {s.gate_title}"
                    self.asset.save()
                    page = write_review(self)
                    log(f"[{s.index:02d} {s.name}] waiting for gate {s.gate} ({s.gate_title}). Review: {page}")
                    return "awaiting_gate"
                if s.after_gate:
                    ctx = Ctx(self.project, self.asset, self.backend, s, self.asset.path(rec.info["dir"]))
                    s.after_gate(ctx, dec)
                    rec.outputs.update({k: self.asset.rel(v) for k, v in ctx.outputs.items()})
                    rec.info.update(ctx.info)
                rec.status = "done"
                log(f"[{s.index:02d} {s.name}] gate {s.gate} approved" + (f" (pick {dec.pick})" if dec.pick is not None else ""))
                self.asset.save()
            if until and s.name == until:
                break
        st.status = "complete" if all(r.status in ("done", "skipped") for r in st.steps.values()) else st.status
        self.asset.save()
        page = write_review(self)
        log(f"status: {st.status}. Review: {page}")
        return st.status
