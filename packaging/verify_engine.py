"""Verify the engine source against the reviewed release manifest before bundling it."""
import hashlib
import json
from pathlib import Path


def verify_engine(engine, manifest):
    engine = Path(engine)
    lock = json.loads(Path(manifest).read_text(encoding="utf-8"))
    for name, expected in lock["files"].items():
        source = engine / name
        # Git may check Python sources out with CRLF on Windows. Compare the
        # canonical LF content recorded by git archive, independent of checkout.
        if not source.is_file() or hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != expected:
            raise RuntimeError(f"Engine file {name} does not match pinned commit {lock['commit']}. "
                               "Use that engine revision, or review and update packaging/engine-lock.json.")
    return lock["commit"]
