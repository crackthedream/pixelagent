"""Live preview: while the artist draws, the current drawing is exported after every round of tool calls to
<asset>/live/live.png (+ live.json), so the app can show the sprite taking shape.

The export never includes the tracing layer ('reference' layers are not exported), so what the user watches is
only the model's own drawing. Publishing must never break a run: every error is swallowed."""
import json
import os
import time

from .store import _retry_locked


class LivePreview:
    def __init__(self, asset_dir, step, frames=1):
        self.dir = os.path.join(asset_dir, "live")
        self.step, self.frames = step, frames
        os.makedirs(self.dir, exist_ok=True)
        self.clear()

    @property
    def png(self):
        return os.path.join(self.dir, "live.png")

    def publish(self, mcp, phase, turn, frame=None):
        try:
            tmp = os.path.join(self.dir, f"live.{os.getpid()}.tmp.png")
            args = {"path": tmp}
            if frame is not None:
                args["frame"] = frame
            _, _, err = mcp.call("export", args)
            if err or not os.path.exists(tmp):
                return
            _retry_locked(lambda: os.replace(tmp, self.png))
            meta = {"step": self.step, "phase": phase, "turn": turn, "frame": frame, "frames": self.frames,
                    "updated": time.time()}
            mtmp = os.path.join(self.dir, f"live.{os.getpid()}.tmp.json")
            with open(mtmp, "w", encoding="utf-8") as f:
                json.dump(meta, f)
            _retry_locked(lambda: os.replace(mtmp, os.path.join(self.dir, "live.json")))
        except Exception:  # a preview is a nicety; the drawing itself must go on
            pass

    def clear(self):
        for n in ("live.png", "live.json"):
            try:
                os.remove(os.path.join(self.dir, n))
            except OSError:
                pass


def read_live(asset_dir, max_age=None):
    """The current live preview of an asset as (meta, png path), or None."""
    d = os.path.join(asset_dir, "live")
    try:
        with open(os.path.join(d, "live.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    png = os.path.join(d, "live.png")
    if not os.path.exists(png) or (max_age and time.time() - meta.get("updated", 0) > max_age):
        return None
    return meta, png
