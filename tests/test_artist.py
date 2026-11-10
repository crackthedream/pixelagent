"""Artist agent loop, driven by a scripted fake model against the REAL pixelliner MCP server over stdio."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from pixelagent import imaging, synth  # noqa: E402
from pixelagent.artist import face_region, run_artist  # noqa: E402
from pixelagent.store import Project  # noqa: E402


def call(name, args, cid):
    return NS(type="function_call", name=name, arguments=json.dumps(args), call_id=cid)


class FakeResponses:
    """Plays a fixed drawing session: palette + silhouette, then compare/view, then finish."""

    def __init__(self, script):
        self.script, self.requests = script, []

    def create(self, **kw):
        self.requests.append(kw)
        i = len(self.requests) - 1
        out = self.script[min(i, len(self.script) - 1)]
        usage = NS(input_tokens=2000 * (i + 1), output_tokens=300, input_tokens_details=NS(cached_tokens=0))
        return NS(id=f"resp_{i}", output=out, usage=usage)


class ArtistLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pxa_art_")
        self.proj = Project(os.path.join(self.tmp, "项目"), {"backend": {"mode": "openai"}})
        im, _ = synth.draw_character(seed=1, size=384, background=(200, 200, 204))
        self.ref = os.path.join(self.tmp, "ref.png")
        im.save(self.ref)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scripted_session_draws_through_mcp(self):
        script = [
            [call("palette", {"ramp": {"base": "#c03030", "steps": 3, "keys": "abc"}, "set": {"o": "#1b1325"}}, "c1"),
             call("draw", {"ops": [{"op": "ellipse", "x": 6, "y": 4, "w": 22, "h": 40, "color": "b", "fill": True},
                                   {"op": "outline", "color": "o"}]}, "c2")],
            [call("compare_reference", {}, "c3"), call("view", {"mode": "image"}, "c4"),
             call("new_canvas", {"width": 8, "height": 8}, "c5")],          # not allowed: must be refused
            [call("finish", {"summary": "red blob"}, "c6")],
        ]
        fake = NS(responses=FakeResponses(script))
        s = run_artist(self.proj, "hero", self.ref, height=48, client=fake, max_usd=3, max_turns=10, log=lambda *_: None)
        self.assertEqual(s["stop"], "draw: finished")
        self.assertEqual(s["turns"], 3)
        self.assertEqual(s["finish_summary"], "red blob")
        reqs = fake.responses.requests
        tool_names = {t["name"] for t in reqs[0]["tools"]}
        self.assertTrue({"draw", "view", "compare_reference", "palette", "finish"} <= tool_names)
        self.assertNotIn("new_canvas", tool_names)
        self.assertEqual(reqs[1]["previous_response_id"], "resp_0")
        outs = [i for i in reqs[2]["input"] if i.get("type") == "function_call_output"]
        self.assertEqual([o["call_id"] for o in outs], ["c3", "c4", "c5"])
        self.assertIn("not available", outs[2]["output"])
        imgs = [c for i in reqs[2]["input"] if i.get("role") == "user" for c in i["content"] if c["type"] == "input_image"]
        self.assertEqual(len(imgs), 2)                                  # compare + view images forwarded to the model
        png = imaging.load_rgba(s["png"])
        self.assertEqual(png.height, 48 + 4)
        bbox = imaging.content_bbox(png, 1)
        self.assertGreater(bbox[3] - bbox[1], 30)                        # the drawing is exported...
        data = imaging.to_bytes(png)
        cols = {tuple(data[i:i + 3]) for i in range(0, len(data), 4) if data[i + 3]}
        self.assertLessEqual(len(cols), 4)                               # ...without the (many-colour) tracing layer
        with open(os.path.join(self.proj.root, "artist", "hero", "transcript.jsonl"), encoding="utf-8") as f:
            tr = [json.loads(l) for l in f]
        self.assertEqual([t["tool"] for t in tr if "tool" in t], ["palette", "draw", "compare_reference", "view", "new_canvas", "finish"])
        with open(os.path.join(self.proj.root, "costs.jsonl"), encoding="utf-8") as f:
            costs = [json.loads(l) for l in f]
        self.assertEqual([c["role"] for c in costs], ["artist"] * 3)

    def test_hybrid_trace_palette_and_grid_readout(self):
        from PIL import Image
        red, grey, dark = "#c03030", "#909098", "#1b1325"
        tr = Image.new("RGBA", (20, 30), (0, 0, 0, 0))
        for y in range(4, 26):
            for x in range(5, 15):
                tr.putpixel((x, y), (0xc0, 0x30, 0x30, 255) if y < 12 else (0x90, 0x90, 0x98, 255))
        trace = os.path.join(self.tmp, "trace.png")
        tr.save(trace)
        script = [[call("view", {"mode": "grid", "layer": "reference", "x": 4, "y": 10, "width": 8, "height": 4}, "g")],
                  [call("finish", {"summary": "ok"}, "f")]]
        fake = NS(responses=FakeResponses(script))
        s = run_artist(self.proj, "hy", self.ref, height=26, client=fake, max_usd=3, max_turns=5, log=lambda *_: None,
                       trace=trace, palette={"coat0": red, "steel1": grey, "outline": dark})
        self.assertEqual(s["stop"], "draw: finished")
        self.assertEqual(imaging.load_rgba(s["png"]).size, (20, 30))            # canvas = trace grid, 1:1
        reqs = fake.responses.requests
        self.assertIn("pre-pixelisation", reqs[0]["instructions"])
        first_text = reqs[0]["input"][0]["content"][0]["text"]
        self.assertIn("0=#c03030", first_text)                                   # palette given as single-char keys
        grid = [i for i in reqs[1]["input"] if i.get("type") == "function_call_output"][0]["output"]
        self.assertNotIn("error", grid)
        self.assertIn("0000000", grid.replace(" ", "")[:400] + grid)              # trace pixels read as palette key 0
        self.assertIn("1111111", grid.replace(" ", ""))                           # ... and key 1 (steel)

    def test_face_pass_from_saved_project(self):
        draw = [[call("draw", {"ops": [{"op": "rect", "x": 8, "y": 6, "w": 16, "h": 30, "color": "#e0b090", "fill": True}]}, "d1")],
                [call("finish", {"summary": "body"}, "d2")]]
        fake = NS(responses=FakeResponses(draw))
        s1 = run_artist(self.proj, "fp", self.ref, height=40, client=fake, max_turns=5, log=lambda *_: None,
                        phases=("draw",))
        face = [[call("view", {"mode": "grid", "x": 12, "y": 8, "width": 8, "height": 6}, "f1"),
                 call("draw", {"ops": [{"op": "pixels", "points": [[14, 10], [17, 10]], "color": "#101018"}]}, "f2")],
                [call("finish", {"summary": "eyes"}, "f3")]]
        fake2 = NS(responses=FakeResponses(face))
        cf = os.path.join(self.tmp, "cface.png")
        imaging.load_rgba(self.ref).crop((100, 60, 260, 200)).save(cf)
        s2 = run_artist(self.proj, "fp2", self.ref, height=40, client=fake2, max_turns=5, log=lambda *_: None,
                        start_project=s1["project"], phases=("face",), face=(11, 7, 10, 8), concept_face=cf)
        self.assertEqual(s2["stop"], "face: finished")
        req = fake2.responses.requests[0]
        self.assertIn("FACE PASS", req["instructions"])
        imgs = [c for c in req["input"][0]["content"] if c["type"] == "input_image"]
        self.assertEqual(len(imgs), 3)                       # sprite zoom, trace zoom, concept face close-up
        png = imaging.to_bytes(imaging.load_rgba(s2["png"]))
        w = imaging.load_rgba(s2["png"]).width
        self.assertEqual(tuple(png[(10 * w + 14) * 4:(10 * w + 14) * 4 + 3]), (0x10, 0x10, 0x18))   # eye drawn
        self.assertEqual(tuple(png[(20 * w + 12) * 4:(20 * w + 12) * 4 + 3]), (0xe0, 0xb0, 0x90))  # body kept

    def test_two_views_in_one_round_reach_the_model_as_two_images(self):
        # review finding F07: both images used to be saved as t001_view0.png, so the model saw the second one twice
        script = [[call("draw", {"ops": [{"op": "rect", "x": 2, "y": 2, "w": 6, "h": 6, "color": "#ff0000", "fill": True}]}, "d")],
                  [call("view", {"mode": "image", "x": 0, "y": 0, "width": 10, "height": 10, "scale": 4}, "v1"),
                   call("view", {"mode": "image", "x": 20, "y": 30, "width": 10, "height": 10, "scale": 4}, "v2")],
                  [call("finish", {"summary": "ok"}, "f")]]
        fake = NS(responses=FakeResponses(script))
        run_artist(self.proj, "views", self.ref, height=48, client=fake, max_turns=5, log=lambda *_: None)
        req = fake.responses.requests[2]  # the request that carries the two view results
        imgs = [c["image_url"] for i in req["input"] if i.get("role") == "user" for c in i["content"] if c["type"] == "input_image"]
        self.assertEqual(len(imgs), 2)
        self.assertNotEqual(imgs[0], imgs[1])

    def test_face_pass_after_drawing_is_a_fresh_conversation(self):
        # draw then face in one run: the face pass must not carry the drawing conversation (every call would re-send
        # all earlier rounds and their images; measured $0.51 vs $0.05-0.08 for the same pass)
        script = [[call("draw", {"ops": [{"op": "rect", "x": 8, "y": 6, "w": 16, "h": 30, "color": "#e0b090", "fill": True}]}, "d1")],
                  [call("finish", {"summary": "body"}, "d2")],
                  [call("draw", {"ops": [{"op": "pixel", "x": 14, "y": 10, "color": "#101018"}]}, "f1")],
                  [call("finish", {"summary": "eyes"}, "f2")]]
        fake = NS(responses=FakeResponses(script))
        s = run_artist(self.proj, "fr", self.ref, height=40, client=fake, max_turns=5, log=lambda *_: None,
                       phases=("draw", "face"), face=(11, 7, 10, 8))
        self.assertEqual(s["stop"], "draw: finished; face: finished")
        reqs = fake.responses.requests
        self.assertEqual(reqs[1]["previous_response_id"], "resp_0")          # drawing rounds chain together
        face_first = reqs[2]
        self.assertIn("FACE PASS", face_first["instructions"])
        self.assertNotIn("previous_response_id", face_first)                 # ...the face pass starts fresh
        self.assertFalse(any(i.get("type") == "function_call_output" for i in face_first["input"]))
        self.assertEqual(reqs[3]["previous_response_id"], "resp_2")          # and chains within itself

    def test_face_region_picks_head_skin_not_hands(self):
        w, h = 20, 40
        labels = [None] * (w * h)
        for y in range(2, 38):
            for x in range(4, 16):
                labels[y * w + x] = "coat"
        for y in range(4, 10):
            for x in range(7, 13):
                labels[y * w + x] = "skin"       # face
        for y in range(22, 25):
            for x in range(2, 5):
                labels[y * w + x] = "skin"       # hand, lower down
        for y in range(2, 5):
            for x in range(6, 14):
                labels[y * w + x] = "hair"
        self.assertEqual(face_region(labels, w, h), (6, 4, 8, 7))   # skin rows 5-9 (hair covers row 4), +1 px margin

    def test_face_region_prefers_the_eyes_over_a_raised_hand(self):
        # a waving hand beside the head is skin too, and can be the bigger group (benchmark ref01, 2026-11-09)
        w, h = 40, 100
        labels = [None] * (w * h)

        def fill(x0, y0, x1, y1, name):
            for y in range(y0, y1):
                for x in range(x0, x1):
                    labels[y * w + x] = name
        fill(10, 30, 30, 95, "dress")                 # body
        fill(14, 6, 26, 18, "skin")                    # face
        fill(17, 10, 19, 12, "eyes")
        fill(22, 10, 24, 12, "eyes")
        fill(19, 14, 22, 15, "mouth")
        fill(0, 2, 10, 26, "skin")                     # a big raised hand, separate from the face
        x, y, bw, bh = face_region(labels, w, h)
        self.assertTrue(x >= 10 and x + bw <= 30, (x, y, bw, bh))   # on the face, not the hand
        self.assertTrue(y <= 10 and y + bh >= 15, (x, y, bw, bh))

    def test_face_region_groups_separate_features(self):
        # a mushroom-like face: no skin, separate eye / eye / mouth / blush marks on a "body" material
        w, h = 30, 40
        labels = [None] * (w * h)
        for y in range(2, 38):
            for x in range(3, 27):
                labels[y * w + x] = "body"
        for (x, y) in ((10, 12), (10, 13), (17, 12), (17, 13)):
            labels[y * w + x] = "eyes_and_mouth"
        for x in range(12, 16):
            labels[16 * w + x] = "eyes_and_mouth"
        for (x, y) in ((8, 15), (19, 15)):
            labels[y * w + x] = "blush"
        self.assertEqual(face_region(labels, w, h), (7, 11, 14, 7))   # both eyes, mouth and blush, +1 px

    def test_face_region_ignores_object_faces(self):
        # a helmeted knight: 'shield_face' / 'visor' are not a face, even high up on the figure
        w, h = 30, 60
        labels = [None] * (w * h)
        for y in range(2, 58):
            for x in range(5, 25):
                labels[y * w + x] = "plate_armor"
        for y in range(10, 30):
            for x in range(2, 9):
                labels[y * w + x] = "shield_face"
        for y in range(4, 8):
            for x in range(12, 18):
                labels[y * w + x] = "visor_and_joint_gaps"
        self.assertIsNone(face_region(labels, w, h))

    def test_budget_stops_the_loop(self):
        script = [[call("view", {"mode": "image"}, "v")]]
        fake = NS(responses=FakeResponses(script))
        self.proj.config["pricing"]["gpt-6.1-sol"] = {"input": 2000.0, "output": 2000.0}  # first call alone > $3
        s = run_artist(self.proj, "hero", self.ref, height=40, client=fake, max_usd=3, max_turns=10, log=lambda *_: None)
        self.assertIn("budget", s["stop"])
        self.assertEqual(s["turns"], 1)
        self.assertTrue(os.path.exists(s["png"]))


if __name__ == "__main__":
    unittest.main()
