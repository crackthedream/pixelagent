"""Live preview: the drawing is published through the real engine after drawing rounds, and the app shows it only
while the item is running."""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from PIL import Image

from pixelagent.agent_session import AgentSession
from pixelagent.config import load_config, load_engine
from pixelagent.live import LivePreview, read_live
from pixelagent.mcp_client import MCPClient
from pixelagent.store import Project


class LivePreviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pixelagent-live-")
        self.engine = load_engine(load_config())
        self.mcp = MCPClient(os.path.join(self.engine, "server.py"), env={"PIXELLINER_MCP_OUT": self.tmp})

    def tearDown(self):
        self.mcp.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_publish_exports_the_drawing_without_the_trace(self):
        self.mcp.call("new_canvas", {"width": 12, "height": 16, "name": "t"})
        trace = os.path.join(self.tmp, "trace.png")
        Image.new("RGBA", (12, 16), (0, 0, 255, 255)).save(trace)
        self.mcp.call("import_reference", {"path": trace, "width": 12, "height": 16, "into": "layer", "x": 0, "y": 0,
                                           "palette": "none", "opacity": 0.35, "layer_name": "reference"})
        self.mcp.call("layer", {"action": "select", "layer": "Layer 1"})
        self.mcp.call("draw", {"ops": [{"op": "pixel", "x": 3, "y": 4, "color": "#FF0000"}]})
        live = LivePreview(os.path.join(self.tmp, "asset"), "artist")
        live.publish(self.mcp, "draw", 7)
        meta, png = read_live(os.path.join(self.tmp, "asset"))
        self.assertEqual((meta["step"], meta["phase"], meta["turn"]), ("artist", "draw", 7))
        im = Image.open(png).convert("RGBA")
        self.assertEqual(im.size, (12, 16))
        self.assertEqual(im.getpixel((3, 4))[:3], (255, 0, 0))
        self.assertEqual(im.getpixel((0, 0))[3], 0, "the tracing layer must not be in the live image")
        live.clear()
        self.assertIsNone(read_live(os.path.join(self.tmp, "asset")))

    def test_frames_and_staleness(self):
        self.mcp.call("new_canvas", {"width": 8, "height": 8, "name": "a", "frames": 3})
        self.mcp.call("draw", {"frame": 2, "ops": [{"op": "pixel", "x": 1, "y": 1, "color": "#00FF00"}]})
        live = LivePreview(os.path.join(self.tmp, "anim"), "anim_draw", frames=3)
        live.publish(self.mcp, "frame2", 3, frame=2)
        meta, png = read_live(os.path.join(self.tmp, "anim"))
        self.assertEqual((meta["frame"], meta["frames"]), (2, 3))
        self.assertEqual(Image.open(png).convert("RGBA").getpixel((1, 1))[:3], (0, 255, 0))
        meta["updated"] = time.time() - 3600
        with open(os.path.join(self.tmp, "anim", "live", "live.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        self.assertIsNone(read_live(os.path.join(self.tmp, "anim"), max_age=900))

    def test_publish_never_raises(self):
        live = LivePreview(os.path.join(self.tmp, "x"), "artist")
        live.publish(self.mcp, "draw", 1)  # no canvas yet: the export fails, the run must not
        self.assertIsNone(read_live(os.path.join(self.tmp, "x")))

    def test_session_calls_on_round_after_drawing_rounds_only(self):
        class Call:
            def __init__(self, name, args, cid):
                self.type, self.name, self.arguments, self.call_id = "function_call", name, json.dumps(args), cid

        class Resp:
            def __init__(self, calls):
                self.id, self.output = "r", calls
                self.usage = {"input_tokens": 10, "output_tokens": 3}

        script = [[Call("view", {"mode": "image"}, "a")],
                  [Call("draw", {"ops": [{"op": "pixel", "x": 0, "y": 0, "color": "#FFFFFF"}]}, "b")],
                  [Call("finish", {"summary": "done"}, "c")]]

        class Client:
            class responses:
                @staticmethod
                def create(**kw):
                    return Resp(script.pop(0))

        self.mcp.call("new_canvas", {"width": 4, "height": 4, "name": "s"})
        project = Project(os.path.join(self.tmp, "proj"))
        rounds = []
        sess = AgentSession(project, "s", self.mcp, Client, project.config["models"]["artist"], self.tmp, allowed=("view", "draw"),
                            log=lambda m: None, on_round=lambda se, ph: rounds.append((ph, se.turns)))
        stop, summary = sess.converse("draw", "test", [{"type": "input_text", "text": "go"}], 5)
        sess.close()
        self.assertEqual((stop, summary), ("finished", "done"))
        self.assertEqual(rounds, [("draw", 2)])  # the view-only round publishes nothing


class LiveInAppTest(unittest.TestCase):
    def test_detail_shows_live_only_while_running(self):
        from unittest import mock
        from pixelagent.app import server
        tmp = tempfile.mkdtemp(prefix="pixelagent-liveapp-")
        try:
            settings = {**server.default_settings(), "projects_dir": tmp, "backend": "dry_run"}
            proj = Project(os.path.join(tmp, "P"))
            a = proj.new_asset("hero", {"prompt": "x"})
            os.makedirs(os.path.join(a.dir, "live"))
            Image.new("RGBA", (4, 4)).save(os.path.join(a.dir, "live", "live.png"))
            with open(os.path.join(a.dir, "live", "live.json"), "w", encoding="utf-8") as f:
                json.dump({"step": "artist", "phase": "draw", "turn": 5, "frame": None, "frames": 1,
                           "updated": time.time()}, f)
            self.assertNotIn("live", server.asset_detail(settings, "P", proj, "hero"))
            stop = threading.Event()
            t = threading.Thread(target=stop.wait, daemon=True)
            t.start()
            with mock.patch.dict(server.JOBS.running, {("P", "hero"): t}):
                d = server.asset_detail(settings, "P", proj, "hero")
            stop.set()
            self.assertEqual(d["live"]["turn"], 5)
            self.assertIn("live%2Flive.png", d["live"]["url"])
            self.assertIn("&t=", d["live"]["url"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
