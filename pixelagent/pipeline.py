"""Pipeline runner: ordered steps, versioned outputs, approval gates, resume, rerun-from."""
import importlib
import json
import os
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
        return os.path.join(self.out, name)

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


def build_steps():
    from . import steps
    return steps.STEPS


class Pipeline:
    def __init__(self, project, asset_id, mode=None, client=None):
        self.project = project
        self.asset = project.asset(asset_id)
        self.backend = make_backend(project, asset_id, mode, client)
        self.steps = build_steps()

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

    def pending_gate(self):
        for s in self.steps:
            rec = self.asset.state.steps.get(s.name)
            if rec and rec.status == "awaiting_gate":
                return s
        return None

    def decide(self, approved, pick=None, note=""):
        s = self.pending_gate()
        if s is None:
            raise ValueError("no gate is waiting for a decision")
        self.asset.state.gates[s.gate] = GateDecision(gate=s.gate, approved=approved, pick=pick, note=note)
        if not approved:
            notes = self.asset.state.inputs.setdefault("notes", {})
            notes.setdefault(s.name, []).append(note)
            rec = self.asset.record(s.name)
            rec.status = "pending"
            self.asset.state.gates.pop(s.gate, None)
        self.asset.save()
        return s

    def run(self, auto_approve=False, until=None, log=None):
        log = log or (lambda msg: print(msg, flush=True))  # flush: progress shows even when output is redirected
        from .review import write_review
        st = self.asset.state
        for s in self.steps:
            rec = self.asset.record(s.name)
            if rec.status in ("done", "skipped"):
                continue
            if rec.status in ("pending", "failed"):
                out = self.asset.new_version_dir(s.index, s.name)
                ctx = Ctx(self.project, self.asset, self.backend, s, out)
                log(f"[{s.index:02d} {s.name}] running (v{rec.version:03d})")
                try:
                    s.run(ctx)
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
                if ctx.skipped:
                    rec.status = "skipped"
                    log(f"[{s.index:02d} {s.name}] skipped: {ctx.info.get('reason', '')}")
                elif s.gate and ctx.needs_gate:
                    rec.status = "awaiting_gate"
                else:
                    rec.status = "done"
                self.asset.save()
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
