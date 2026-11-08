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


class _Base:
    def __init__(self, project, asset_id=None):
        self.project = project
        self.asset_id = asset_id
        self.cfg = project.config

    def model_for(self, role):
        return self.cfg["models"].get(role, role)

    def _log(self, kind, role, usd=0.0, **extra):
        self.project.log_cost({"asset": self.asset_id, "kind": kind, "role": role,
                               "model": extra.pop("model", self.model_for(role)), "usd": usd, **extra})

    def check_budget(self):
        cap = float(self.cfg["budget"].get("max_usd_per_asset", 0) or 0)
        if cap and self.asset_id and self.project.cost_total(self.asset_id) >= cap:
            raise BudgetExceeded(f"asset '{self.asset_id}' reached its ${cap:.2f} budget")
        month_cap = float(self.cfg["budget"].get("monthly_usd", 0) or 0)
        if month_cap and self.project.cost_total(month=True) >= month_cap:
            raise BudgetExceeded(f"this month's spend in this project reached the ${month_cap:.2f} cap ([budget] monthly_usd)")


class StubLLM(_Base):
    def structured(self, role, instructions, text, images, schema, stub):
        self.check_budget()
        t = time.perf_counter()
        result = stub()
        if not isinstance(result, schema):
            result = schema.model_validate(result)
        self._log("llm", role, model="stub", images=len(images or []), ms=int((time.perf_counter() - t) * 1000))
        return result


class StubImages(_Base):
    def generate(self, role, prompt, n, out_dir, refs=None, stub=None, quality=None):
        self.check_budget()
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
