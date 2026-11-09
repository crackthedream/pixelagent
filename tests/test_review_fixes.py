"""Regression tests for review findings F05 (restores), F08 (the trace never becomes drawn pixels) and F09 (version
folders are never reused), each reproducing the original failure condition."""
import json
import os
import shutil
import tempfile
import unittest

from PIL import Image

from pixelagent import synth
from pixelagent.agent_session import AgentSession
from pixelagent.config import load_config, load_engine
from pixelagent.mcp_client import MCPClient
from pixelagent.pipeline import Pipeline
from pixelagent.store import Project


def quiet(*_a, **_k):
    pass


class RestoreTests(unittest.TestCase):
    """F05: an older drawing could be restored on top of a newer plan or concept (e.g. 96 px over 64 px)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pxa_fix_")
        self.proj = Project(os.path.join(self.tmp, "P"), {"backend": {"mode": "dry_run"}})
        im, _ = synth.draw_character(seed=3, size=384, background=(200, 200, 204))
        self.ref = os.path.join(self.tmp, "ref.png")
        im.save(self.ref)
        self.proj.new_asset("hero", {"references": [self.ref], "height": 48})
        Pipeline(self.proj, "hero").run(auto_approve=True, log=quiet)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_inputs_restore_and_new_inputs_refuse(self):
        pipe = Pipeline(self.proj, "hero")
        pipe.reset_from("artist")                       # redraw from the same plan and concept: versions fit
        pipe.run(auto_approve=True, log=quiet)
        pipe = Pipeline(self.proj, "hero")
        vs = pipe.versions("artist")
        self.assertEqual([(v["version"], v["current"], v["compatible"]) for v in vs], [(1, False, True), (2, True, True)])
        pipe.restore_version("artist", 1)
        pipe.run(auto_approve=True, log=quiet)
        a = self.proj.asset("hero")
        self.assertTrue(a.state.steps["artist"].info["dir"].endswith("v001"))
        self.assertEqual(a.state.status, "complete")

        # a new concept: the earlier drawings were made from another concept and must not be restored onto it
        pipe = Pipeline(self.proj, "hero")
        pipe.reset_from("concept")
        pipe.run(auto_approve=True, log=quiet)
        pipe = Pipeline(self.proj, "hero")
        vs = {v["version"]: v for v in pipe.versions("artist")}
        self.assertTrue(vs[3]["current"] and vs[3]["compatible"])
        self.assertFalse(vs[1]["compatible"])
        self.assertFalse(vs[2]["compatible"])
        with self.assertRaises(ValueError) as cm:
            pipe.restore_version("artist", 1)
        self.assertIn("earlier plan, concept", str(cm.exception))
        self.assertTrue(self.proj.asset("hero").state.steps["artist"].info["dir"].endswith("v003"))  # unchanged


class VersionFolderTests(unittest.TestCase):
    """F09: a run killed after writing outputs but before saving state left a folder that the next run reused."""

    def test_an_interrupted_folder_is_kept_and_skipped(self):
        tmp = tempfile.mkdtemp(prefix="pxa_fix_")
        try:
            proj = Project(os.path.join(tmp, "P"))
            a = proj.new_asset("hero", {"prompt": "x"})
            d1 = a.new_version_dir(9, "polish")
            self.assertTrue(d1.endswith("v001"))
            # simulate a crash: the next run created v002 and wrote into it, but state.json still says version 1
            orphan = os.path.join(os.path.dirname(d1), "v002")
            os.makedirs(orphan)
            with open(os.path.join(orphan, "half.png"), "wb") as f:
                f.write(b"unfinished")
            a2 = proj.asset("hero")
            self.assertEqual(a2.state.steps["polish"].version, 1)
            d3 = a2.new_version_dir(9, "polish")
            self.assertTrue(d3.endswith("v003"))
            with open(os.path.join(orphan, "half.png"), "rb") as f:
                self.assertEqual(f.read(), b"unfinished")       # the interrupted attempt is kept for inspection
            self.assertEqual(proj.asset("hero").state.steps["polish"].version, 3)  # reserved in state.json at once
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ReferenceGuardTests(unittest.TestCase):
    """F08: draw {op: transform, from_layer: 'reference', copy: true} copied the trace into the exported drawing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pxa_fix_")
        engine = load_engine(load_config())
        self.mcp = MCPClient(os.path.join(engine, "server.py"), env={"PIXELLINER_MCP_OUT": self.tmp})
        self.mcp.call("new_canvas", {"width": 8, "height": 8, "name": "g"})
        tr = os.path.join(self.tmp, "trace.png")
        Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(tr)
        self.mcp.call("import_reference", {"path": tr, "width": 8, "height": 8, "into": "layer", "x": 0, "y": 0,
                                           "palette": "none", "opacity": 0.35, "layer_name": "reference"})
        self.mcp.call("layer", {"action": "move", "layer": "reference", "index": 0})
        self.mcp.call("layer", {"action": "add", "name": "lines"})
        self.sess = AgentSession(Project(os.path.join(self.tmp, "P")), "g", self.mcp, None, "m", self.tmp,
                                 allowed=("draw", "view"), log=quiet)

    def tearDown(self):
        self.sess.close()
        self.mcp.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_copying_or_drawing_on_the_trace_is_refused(self):
        info, _, _ = self.mcp.call("canvas", {"action": "info"})
        n = sum(1 for line in info.splitlines() if line.strip().lstrip("*").startswith("#"))
        copy = lambda src: {"ops": [{"op": "transform", "from_layer": src, "copy": True}]}  # noqa: E731
        for src in ("reference", 0, "0", -n, str(-n)):
            self.assertIsNotNone(self.sess.guard("draw", copy(src)), src)
        self.assertIsNotNone(self.sess.guard("draw", {"layer": "reference", "ops": [{"op": "pixel", "x": 0, "y": 0, "color": "#f00"}]}))
        self.assertIsNotNone(self.sess.guard("draw", {"layer": 0, "ops": []}))
        # drawing between the model's own layers, and frame reuse, still work
        self.assertIsNone(self.sess.guard("draw", copy("lines")))
        self.assertIsNone(self.sess.guard("draw", {"ops": [{"op": "transform", "from_frame": 0, "copy": True}]}))
        self.assertIsNone(self.sess.guard("draw", {"ops": [{"op": "pixel", "x": 1, "y": 1, "color": "#ff0000"}]}))
        self.assertIsNone(self.sess.guard("draw", {"layer": "lines", "ops": []}))


if __name__ == "__main__":
    unittest.main()
