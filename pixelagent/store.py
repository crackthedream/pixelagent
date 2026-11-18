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
import uuid

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


ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,120}")
_LEDGER_LOCK = threading.Lock()
_COST_LOCK = threading.RLock()


def _atomic_json(path, value):
    """Publish a complete JSON file; failed writes leave the previous file readable."""
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        _retry_locked(lambda: os.replace(tmp, path))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _cost_entries(path):
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if isinstance(entry, dict):
                    yield entry
            except ValueError:  # an interrupted append must not hide the rest of the accounting
                continue


def ensure_asset_identity(state_path, costs_path, asset_id):
    """Migrate an existing asset once, before moving/renaming it. Historical logs stay untouched.

    The legacy totals travel with the asset, including if its former project is later deleted.
    New assets set their UUID explicitly and therefore never inherit a reused folder ID's costs.
    """
    with _COST_LOCK:
        state = AssetState.model_validate_json(_retry_locked(lambda: _read_text(state_path)))
        if not state.asset_uuid:
            state.asset_uuid = uuid.uuid4().hex
            totals = {}
            for entry in _cost_entries(costs_path):
                if entry.get("asset") == asset_id and not entry.get("asset_uuid"):
                    month = str(entry.get("time", ""))[:7]
                    totals[month] = totals.get(month, 0.0) + float(entry.get("usd", 0) or 0)
            state.legacy_costs = totals
            _atomic_json(state_path, state.model_dump())
        return state


def ledger_append(ledger, entry):
    """The app's spending ledger: one line per paid call, kept outside every project folder, so deleting or moving
    projects never removes spending from the monthly limit (review finding F02)."""
    os.makedirs(os.path.dirname(os.path.abspath(ledger)), exist_ok=True)
    with _LEDGER_LOCK, open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def ledger_month_spend(ledger, seed_dir=None):
    """USD recorded in the ledger this calendar month. A missing ledger is first seeded from the cost logs of the
    projects in seed_dir (spending made before the ledger existed)."""
    with _LEDGER_LOCK:
        if not os.path.exists(ledger):
            os.makedirs(os.path.dirname(os.path.abspath(ledger)), exist_ok=True)
            with open(ledger, "w", encoding="utf-8") as out:
                out.write(json.dumps({"seeded_from": seed_dir, "time": datetime.datetime.now().isoformat(timespec="seconds"),
                                      "usd": 0}) + "\n")
                for name in (sorted(os.listdir(seed_dir)) if seed_dir and os.path.isdir(seed_dir) else []):
                    p = os.path.join(seed_dir, name, "costs.jsonl")
                    if not os.path.isfile(p):
                        continue
                    with open(p, encoding="utf-8") as f:
                        for line in f:
                            try:
                                e = json.loads(line)
                            except ValueError:
                                continue
                            out.write(json.dumps({**e, "project": name}, ensure_ascii=False) + "\n")
        prefix = datetime.date.today().strftime("%Y-%m")
        total = 0.0
        with open(ledger, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if str(e.get("time", "")).startswith(prefix):
                    total += float(e.get("usd", 0) or 0)
        return total


def month_spend_all(projects_dir):
    """USD logged this calendar month by every project folder in projects_dir."""
    total = 0.0
    try:
        names = os.listdir(projects_dir)
    except OSError:
        return 0.0
    prefix = datetime.date.today().strftime("%Y-%m")
    for n in names:
        p = os.path.join(projects_dir, n, "costs.jsonl")
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if str(e.get("time", "")).startswith(prefix):
                    total += float(e.get("usd", 0) or 0)
    return total


class Project:
    def __init__(self, root, overrides=None):
        self.root = os.path.abspath(os.path.expanduser(root))
        os.makedirs(os.path.join(self.root, "assets"), exist_ok=True)
        self.config = load_config(self.root, overrides)

    def asset_ids(self):
        d = os.path.join(self.root, "assets")
        return sorted(n for n in os.listdir(d) if os.path.exists(os.path.join(d, n, "state.json")))

    def asset(self, asset_id):
        # every lookup starts here: an id is one plain folder name, so '../../B/assets/hero' or an absolute path
        # can never reach another project's files (review finding F03)
        if not isinstance(asset_id, str) or not ID_RE.fullmatch(asset_id):
            raise ValueError("invalid item id")
        return Asset(self, asset_id)

    def new_asset(self, asset_id, inputs):
        if not ID_RE.fullmatch(asset_id):
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
        a.state = AssetState(id=asset_id, asset_uuid=uuid.uuid4().hex, inputs=stored, status="new")
        a.save()
        return a

    _cost_lock = _COST_LOCK  # also serializes the one-time identity migration with cost appends

    def log_cost(self, entry):
        entry = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "event_id": uuid.uuid4().hex, **entry}
        with self._cost_lock:
            aid = entry.get("asset")
            if isinstance(aid, str) and ID_RE.fullmatch(aid) and os.path.isfile(
                    os.path.join(self.root, "assets", aid, "state.json")):
                entry["asset_uuid"] = self.asset(aid).state.asset_uuid
            ledger = self.config.get("budget", {}).get("ledger")
            if ledger:  # record in the surviving ledger before writing the project-local mirror
                ledger_month_spend(ledger, self.config.get("budget", {}).get("monthly_scope_dir"))
                ledger_append(ledger, {**entry, "project": os.path.basename(self.root)})
            with open(os.path.join(self.root, "costs.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

    def cost_total(self, asset_id=None, month=False):
        """USD spent (estimated) in this project; filter by asset and/or the current calendar month."""
        prefix = datetime.date.today().strftime("%Y-%m") if month else None
        with self._cost_lock:
            state = None
            if asset_id is not None and isinstance(asset_id, str) and ID_RE.fullmatch(asset_id) and os.path.isfile(
                    os.path.join(self.root, "assets", asset_id, "state.json")):
                state = self.asset(asset_id).state
            total = (state.legacy_costs.get(prefix, 0.0) if prefix else sum(state.legacy_costs.values())) if state else 0.0
            paths = [os.path.join(self.root, "costs.jsonl")]
            ledger = self.config.get("budget", {}).get("ledger")
            if state and ledger:
                paths.append(ledger)
            seen = set()
            for path in paths:
                for e in _cost_entries(path):
                    if state:
                        if e.get("asset_uuid") != state.asset_uuid:
                            continue
                    elif asset_id is not None and e.get("asset") != asset_id:
                        continue
                    if prefix and not str(e.get("time", "")).startswith(prefix):
                        continue
                    event = e.get("event_id")
                    if event and event in seen:
                        continue
                    if event:
                        seen.add(event)
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
            self.state = ensure_asset_identity(self.state_path, os.path.join(project.root, "costs.jsonl"), asset_id)

    def reset_cost_identity(self):
        """A duplicate is new work; call this before saving a copied asset under its new ID."""
        self.state.asset_uuid = uuid.uuid4().hex
        self.state.legacy_costs = {}

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
        """A new, never-used folder for a run of a step, reserved in state.json before anything is written into it.
        A folder left by an interrupted run (on disk, not recorded) is skipped, never overwritten (finding F09)."""
        rec = self.record(step)
        base = os.path.join(self.dir, "steps", f"{index:02d}_{step}")
        v = rec.version + 1
        while os.path.exists(os.path.join(base, f"v{v:03d}")):
            v += 1
        rec.version = v
        d = os.path.join(base, f"v{v:03d}")
        os.makedirs(d)
        self.save()
        return d

    def output(self, step, key):
        """Absolute path of a named output of a finished step."""
        rec = self.state.steps.get(step)
        if not rec or key not in rec.outputs:
            raise KeyError(f"step '{step}' has no output '{key}' (has it run?)")
        return self.path(rec.outputs[key])

    def references(self):
        return [self.path(r) for r in self.state.inputs.get("references", [])]
