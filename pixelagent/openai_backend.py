"""OpenAI backend: structured (pydantic) model calls with image inputs via the Responses API, and concept
images via the Images API. Every call logs tokens and estimated USD to costs.jsonl and respects budgets.

The API key is read only from the OPENAI_API_KEY environment variable (on Windows also from the user
environment in the registry, so a freshly set variable works without restarting). It is never logged.
"""
import base64
import io
import os
import time

from PIL import Image

from .models import _Base


def find_api_key():
    """On Windows the user environment in the registry wins: it is the current setting, while a running
    process keeps whatever value it inherited at launch (stale after a key rotation)."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key, _ = winreg.QueryValueEx(k, "OPENAI_API_KEY")
                if key:
                    return key
        except OSError:
            pass
    return os.environ.get("OPENAI_API_KEY") or None


def make_client(cfg):
    from openai import OpenAI
    key = find_api_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set. Set it as a user environment variable (see README); "
                           "pixelagent never stores keys in files.")
    b = cfg["backend"]
    return OpenAI(api_key=key, timeout=float(b.get("timeout_s", 240)), max_retries=int(b.get("max_retries", 4)))


def image_data_url(path, max_side=1024):
    """PNG data URL, downscaled so the longest side is <= max_side. Small pixel images are upscaled with
    nearest-neighbour to at least 256 px so models can see individual pixels."""
    im = Image.open(path)
    im.load()
    im = im.convert("RGBA")
    longest = max(im.size)
    if longest > max_side:
        r = max_side / longest
        im = im.resize((max(1, round(im.width * r)), max(1, round(im.height * r))), Image.LANCZOS)
    elif longest < 256:
        f = max(1, 256 // longest)
        im = im.resize((im.width * f, im.height * f), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class OpenAILLM(_Base):
    def __init__(self, project, asset_id=None, client=None):
        super().__init__(project, asset_id)
        self.client = client or make_client(self.cfg)

    def _price(self, model, usage):
        p = self.cfg.get("pricing", {}).get(model)
        if not p or usage is None:
            return 0.0
        return (usage.input_tokens * p.get("input", 0) + usage.output_tokens * p.get("output", 0)) / 1e6

    def structured(self, role, instructions, text, images, schema, stub):
        self.check_budget()
        model = self.model_for(role)
        content = [{"type": "input_text", "text": text or "(no text input)"}]
        max_side = int(self.cfg["backend"].get("max_image_side", 1024))
        for p in images or []:
            content.append({"type": "input_image", "image_url": image_data_url(p, max_side), "detail": "high"})
        kwargs = dict(model=model, instructions=instructions, input=[{"role": "user", "content": content}],
                      text_format=schema)
        effort = self.cfg.get("reasoning", {}).get(role)
        if effort:
            kwargs["reasoning"] = {"effort": effort}
        t = time.perf_counter()
        try:
            resp = self.client.responses.parse(**kwargs)
        except Exception as e:  # some models reject reasoning settings: retry once without them
            if "reasoning" in kwargs and "reasoning" in str(e).lower():
                kwargs.pop("reasoning")
                resp = self.client.responses.parse(**kwargs)
            else:
                self._log("llm", role, model=model, error=type(e).__name__, ms=int((time.perf_counter() - t) * 1000))
                raise
        usage = getattr(resp, "usage", None)
        self._log("llm", role, usd=round(self._price(model, usage), 5), model=model, images=len(images or []),
                  input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None),
                  ms=int((time.perf_counter() - t) * 1000))
        result = resp.output_parsed
        if result is None:
            raise RuntimeError(f"{model} returned no parsable {schema.__name__} (refusal or empty output)")
        return result


class OpenAIImages(_Base):
    def __init__(self, project, asset_id=None, client=None):
        super().__init__(project, asset_id)
        self.client = client or make_client(self.cfg)

    def _price(self, model, usage, n):
        p = self.cfg.get("pricing", {}).get(model)
        if not p or usage is None:
            return 0.0
        det = getattr(usage, "input_tokens_details", None)
        img_in = (getattr(det, "image_tokens", 0) or 0) if det is not None else 0
        txt_in = getattr(det, "text_tokens", None) if det is not None else None
        if txt_in is None:
            txt_in = usage.input_tokens - img_in
        return (txt_in * p.get("text_input", 0) + img_in * p.get("image_input", 0)
                + usage.output_tokens * p.get("output", 0)) / 1e6

    def generate(self, role, prompt, n, out_dir, refs=None, stub=None, quality=None, mask=None, size=None):
        """Generate n images (refs given -> images.edit with the references; mask: a PNG whose transparent area is
        the only part the model may change). Returns PNG paths. size ("WxH"): this call's image size; callers that
        need their own size pass it here instead of changing [images] draft_size, which every run of the project
        shares: faces drawn while pose sheets were drawn came back at the sheets' size (2026-11-12)."""
        self.check_budget()
        os.makedirs(out_dir, exist_ok=True)
        model = self.model_for(role)
        icfg = self.cfg.get("images", {})
        q = quality or (icfg.get("final_quality") if role == "image_final" else icfg.get("draft_quality"))
        common = dict(model=model, prompt=prompt, n=n, size=size or icfg.get("draft_size", "1024x1536"), quality=q,
                      background=icfg.get("background", "opaque"), output_format="png")
        t = time.perf_counter()
        files = []
        try:
            if refs:
                files = [open(p, "rb") for p in refs]
                extra = {}
                if icfg.get("input_fidelity"):  # not every image model accepts it (2.6-sunburst does not)
                    extra["input_fidelity"] = icfg["input_fidelity"]
                if mask:
                    files.append(open(mask, "rb"))
                    extra["mask"] = files[-1]
                    files_for_image = files[:-1]
                else:
                    files_for_image = files
                resp = self.client.images.edit(image=files_for_image, **common, **extra)
            else:
                resp = self.client.images.generate(**common)
        except Exception as e:
            self._log("image", role, model=model, n=n, error=type(e).__name__, ms=int((time.perf_counter() - t) * 1000))
            raise
        finally:
            for f in files:
                f.close()
        paths = []
        for i, d in enumerate(resp.data or []):
            if not d.b64_json:
                raise RuntimeError(f"{model} returned an image without data")
            p = os.path.join(out_dir, f"gen{i:02d}.png")
            with open(p, "wb") as f:
                f.write(base64.b64decode(d.b64_json))
            paths.append(p)
        usage = getattr(resp, "usage", None)
        self._log("image", role, usd=round(self._price(model, usage, n), 5), model=model, n=len(paths), quality=q,
                  input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None),
                  ms=int((time.perf_counter() - t) * 1000))
        if not paths:
            raise RuntimeError(f"{model} returned no images")
        return paths
