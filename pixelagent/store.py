"""Project store: one folder per project, one folder per asset, one versioned folder per step run.

project/
  config.toml                 optional overrides (style guide, models, budgets)
  costs.jsonl                 one line per model call (role, model, tokens, usd)
  assets/<id>/
    state.json                AssetState (inputs, step records, gate decisions)
    inputs/                   copies of the reference images
    steps/<NN>_<step>/vNNN/   outputs of each run of each step
    review/                   HTML review pages
"""
import datetime
import json
import os
import re
import shutil
import threading
import time

from .config import load_config
from .schemas import AssetState, StepRecord


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _retry_locked(fn, tries=40, wait=0.025):
    """Run fn, retrying for up to ~1 s while Windows reports the file as in use by someone else."""
    for i in range(tries):
        try:
            return fn()
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(wait)


class Project:
    def __init__(self, root, overrides=None):
        self.root = os.path.abspath(os.path.expanduser(root))
        os.makedirs(os.path.join(self.root, "assets"), exist_ok=True)
        self.config = load_config(self.root, overrides)

    def asset_ids(self):
        d = os.path.join(self.root, "assets")
        return sorted(n for n in os.listdir(d) if os.path.exists(os.path.join(d, n, "state.json")))

    def asset(self, asset_id):
        return Asset(self, asset_id)

    def new_asset(self, asset_id, inputs):
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", asset_id):
            raise ValueError("asset id may only contain letters, digits, '-' and '_'")
        a = Asset(self, asset_id, create=True)
        if os.path.exists(a.state_path):
            raise ValueError(f"asset '{asset_id}' already exists")
        stored = dict(inputs)
        refs = []
        for i, ref in enumerate(inputs.get("references") or []):
            src = os.path.abspath(os.path.expanduser(ref))
            if not os.path.exists(src):
                raise FileNotFoundError(src)
            dst = os.path.join(a.dir, "inputs", f"ref{i:02d}{os.path.splitext(src)[1].lower()}")
            shutil.copyfile(src, dst)
            refs.append(os.path.relpath(dst, a.dir))
        stored["references"] = refs
        a.state = AssetState(id=asset_id, inputs=stored, status="new")
        a.save()
        return a

    _cost_lock = threading.Lock()  # assets may run in parallel threads (benchmarks)

    def log_cost(self, entry):
        entry = {"time": datetime.datetime.now().isoformat(timespec="seconds"), **entry}
        with self._cost_lock, open(os.path.join(self.root, "costs.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def cost_total(self, asset_id=None, month=False):
        """USD spent (estimated) in this project; filter by asset and/or the current calendar month."""
        p = os.path.join(self.root, "costs.jsonl")
        if not os.path.exists(p):
            return 0.0
        prefix = datetime.date.today().strftime("%Y-%m") if month else None
        total = 0.0
        with open(p, encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                if asset_id is not None and e.get("asset") != asset_id:
                    continue
                if prefix and not str(e.get("time", "")).startswith(prefix):
                    continue
                total += float(e.get("usd", 0) or 0)
        return total


class Asset:
    def __init__(self, project, asset_id, create=False):
        self.project = project
        self.id = asset_id
        self.dir = os.path.join(project.root, "assets", asset_id)
        self.state_path = os.path.join(self.dir, "state.json")
        if create:
            for sub in ("inputs", "steps", "review"):
                os.makedirs(os.path.join(self.dir, sub), exist_ok=True)
            self.state = None
        else:
            if not os.path.exists(self.state_path):
                raise FileNotFoundError(f"no asset '{asset_id}' in {project.root}")
            self.state = AssetState.model_validate_json(_retry_locked(lambda: _read_text(self.state_path)))

    def save(self):
        # a temp file per writer thread; the swap is retried because on Windows os.replace fails
        # (WinError 5/32) while another thread or process (the UI polling, a virus scanner) has state.json open
        tmp = f"{self.state_path}.{os.getpid()}-{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self.state.model_dump_json(indent=2))
        try:
            _retry_locked(lambda: os.replace(tmp, self.state_path))
        except OSError:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def path(self, rel):
        return os.path.join(self.dir, rel)

    def rel(self, path):
        return os.path.relpath(path, self.dir).replace("\\", "/")

    def record(self, step):
        if step not in self.state.steps:
            self.state.steps[step] = StepRecord(name=step)
        return self.state.steps[step]

    def new_version_dir(self, index, step):
        rec = self.record(step)
        rec.version += 1
        d = os.path.join(self.dir, "steps", f"{index:02d}_{step}", f"v{rec.version:03d}")
        os.makedirs(d, exist_ok=True)
        return d

    def output(self, step, key):
        """Absolute path of a named output of a finished step."""
        rec = self.state.steps.get(step)
        if not rec or key not in rec.outputs:
            raise KeyError(f"step '{step}' has no output '{key}' (has it run?)")
        return self.path(rec.outputs[key])

    def references(self):
        return [self.path(r) for r in self.state.inputs.get("references", [])]
