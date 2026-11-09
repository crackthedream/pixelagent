"""Configuration: repo-level config.toml, overridden by <project>/config.toml."""
import copy
import os
import sys
import tomllib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN = bool(getattr(sys, "frozen", False))  # running from the PyInstaller-built PixelAgent.exe


def resource_path(*parts):
    """Bundled files: next to the sources normally, inside the unpacked bundle when frozen."""
    return os.path.join(getattr(sys, "_MEIPASS", REPO), *parts)


DEFAULT_CONFIG = resource_path("config.toml")


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(project_dir=None, overrides=None):
    with open(DEFAULT_CONFIG, "rb") as f:
        cfg = tomllib.load(f)
    if project_dir:
        p = os.path.join(project_dir, "config.toml")
        if os.path.exists(p):
            with open(p, "rb") as f:
                cfg = _merge(cfg, tomllib.load(f))
    if overrides:
        cfg = _merge(cfg, overrides)
    return cfg


_ENGINE_LOADED = None


def load_engine(cfg):
    """Make the PixelLiner MCP engine importable (canvas, art, png, gif, project)."""
    global _ENGINE_LOADED
    path = resource_path("engine") if FROZEN else os.path.abspath(os.path.expanduser(cfg["engine"]["path"]))
    if not os.path.exists(os.path.join(path, "canvas.py")):
        raise RuntimeError(f"PixelLiner engine not found at {path} (config [engine] path)")
    if path not in sys.path:
        sys.path.insert(0, path)
    _ENGINE_LOADED = path
    return path
