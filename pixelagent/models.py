"""Model backends. Every model call in the pipeline goes through these two interfaces, so providers can be
swapped in config and dry runs need no API.

    llm.structured(role, instructions, text, images, schema, stub) -> schema instance
    images.generate(role, prompt, n, out_dir, refs, stub)          -> [png paths]

Each call site passes a `stub` callable that produces a deterministic answer; the dry-run backend returns it,
real backends ignore it. Every call is logged to costs.jsonl.
"""
import os
import shutil
import time


class BudgetExceeded(RuntimeError):
    pass


def month_spend(project):
    """This month's spend that counts against the monthly limit: all projects in the app's projects folder when
    [budget] monthly_scope_dir is set (the app sets it), else this project only (command line)."""
    b = project.config["budget"]
    if b.get("ledger"):  # the app: one ledger for all projects, independent of their folders
        from .store import ledger_month_spend
        return ledger_month_spend(b["ledger"], seed_dir=b.get("monthly_scope_dir"))
    scope = b.get("monthly_scope_dir")
    if scope:
        from .store import month_spend_all
        return month_spend_all(scope)
    return project.cost_total(month=True)


def budget_problem(project, asset_id=None):
    """A sentence saying why spending must stop now, or None."""
    b = project.config["budget"]
    cap = float(b.get("max_usd_per_asset", 0) or 0)
    if cap and asset_id and project.cost_total(asset_id) >= cap:
        return f"this item has used its ${cap:.2f} limit (Settings: limit per item)"
    month_cap = float(b.get("monthly_usd", 0) or 0)
    if month_cap and month_spend(project) >= month_cap:
        return f"this month's ${month_cap:.2f} limit is used up (Settings: monthly limit)"
    return None


class _Base:
    def __init__(self, project, asset_id=None):
        self.project = project
        self.asset_id = asset_id
        self.cfg = project.config
        self._metering_state = {}

    def model_for(self, role):
        return self.cfg["models"].get(role, role)

    def _log(self, kind, role, usd=0.0, **extra):
        self.project.log_cost({"asset": self.asset_id, "kind": kind, "role": role,
                               "model": extra.pop("model", self.model_for(role)), "usd": usd, **extra})

    def check_budget(self):
        from .metering import check_usage_guard
        check_usage_guard(self._metering_state)
        problem = budget_problem(self.project, self.asset_id)
        if problem:
            raise BudgetExceeded(problem)


class StubLLM(_Base):
    # stand-in models are free: paid spending limits do not apply to them (review finding F10)
    def structured(self, role, instructions, text, images, schema, stub):
        t = time.perf_counter()
        result = stub()
        if not isinstance(result, schema):
            result = schema.model_validate(result)
        self._log("llm", role, model="stub", images=len(images or []), ms=int((time.perf_counter() - t) * 1000))
        return result


class StubImages(_Base):
    def generate(self, role, prompt, n, out_dir, refs=None, stub=None, quality=None, mask=None, size=None):
        os.makedirs(out_dir, exist_ok=True)
        paths = stub(n, out_dir) if stub else []
        if not paths and refs:
            for i in range(n):
                p = os.path.join(out_dir, f"gen{i:02d}.png")
                shutil.copyfile(refs[min(i, len(refs) - 1)], p)
                paths.append(p)
        self._log("image", role, model="stub", n=n)
        return paths


class Backend:
    def __init__(self, llm, images):
        self.llm = llm
        self.images = images
        images._metering_state = llm._metering_state


def make_backend(project, asset_id, mode=None, client=None):
    """client: inject an OpenAI-compatible client (tests use a fake one)."""
    mode = mode or project.config["backend"]["mode"]
    if mode == "dry_run":
        return Backend(StubLLM(project, asset_id), StubImages(project, asset_id))
    if mode == "openai":
        from .openai_backend import OpenAIImages, OpenAILLM, make_client
        client = client or make_client(project.config)
        return Backend(OpenAILLM(project, asset_id, client), OpenAIImages(project, asset_id, client))
    raise ValueError(f"unknown backend mode '{mode}'")
