"""M0 tests: engine additions + the whole pipeline in dry-run mode (no API calls).
Run: python -m unittest discover -s tests -v   (from the repo root)"""
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from pixelagent import cli, imaging, synth  # noqa: E402
from pixelagent.config import load_config, load_engine  # noqa: E402
from pixelagent.models import BudgetExceeded  # noqa: E402
from pixelagent.pipeline import Pipeline  # noqa: E402
from pixelagent.store import Project  # noqa: E402

load_engine(load_config())
import art  # noqa: E402
import canvas  # noqa: E402
import project as projectio  # noqa: E402


def quiet(*_a, **_k):
    pass


def rd(path, binary=False):
    with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        # non-ASCII + space in the path on purpose (the user's home directory is non-ASCII)
        self.tmp = tempfile.mkdtemp(prefix="pxa_")
        self.root = os.path.join(self.tmp, "项目 test")
        self.proj = Project(self.root, {"backend": {"mode": "dry_run"}})
        im, _ = synth.draw_character(1, size=512, background=(90, 140, 200))
        self.ref = os.path.join(self.tmp, "参考.png")
        im.save(self.ref)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class EngineTests(unittest.TestCase):
    def test_project_roundtrip(self):
        d = canvas.Document("t", 8, 6)
        d.add_frame(copy_from=0)
        d.layers[0].cels[1][0:4] = b"\x10\x20\x30\xff"
        d.layers.append(canvas.Layer("ref", 8, 6, frames=2))
        d.layers[1].reference = True
        d.palette["a"] = (1, 2, 3, 255)
        folder = os.path.join(tempfile.mkdtemp(), "测试 proj")
        projectio.save_project(d, folder)
        e = projectio.load_project(folder)
        self.assertEqual([l.cels for l in d.layers], [l.cels for l in e.layers])
        self.assertTrue(e.layers[1].reference)
        self.assertEqual(e.palette, d.palette)
        self.assertEqual(e.durations, d.durations)

    def test_lint_counts(self):
        w, h = 10, 10
        data = bytearray(w * h * 4)
        for y in range(2, 8):
            for x in range(2, 8):
                data[(y * w + x) * 4:(y * w + x) * 4 + 4] = b"\xff\x00\x00\xff"
        data[0:4] = b"\x00\xff\x00\xff"            # stray pixel in the corner
        data[(9 * w + 9) * 4 + 3] = 100             # semi-transparent pixel
        res = art.lint(w, h, data, palette=[(255, 0, 0)])
        m = res["metrics"]
        self.assertEqual(m["semi_transparent"], 1)
        self.assertGreaterEqual(m["off_palette"], 1)
        self.assertGreaterEqual(m["stray_fragments"], 1)
        self.assertTrue(any(i["code"] == "semi_transparent" for i in res["issues"]))

    def test_overlay_replacement_keeps_earlier_pixels(self):
        from pixelagent.steps.build import _overlay
        d = canvas.Document("t", 4, 1)
        d.layers[0].cels[0][:] = bytes((10, 10, 10, 255)) * 4
        ctx = type("C", (), {"canvas": canvas})()
        first = bytearray(d.composite())
        first[0:4] = bytes((200, 0, 0, 255))
        _overlay(ctx, d, first, "repair")
        second = bytearray(d.composite())
        second[4:8] = bytes((0, 200, 0, 255))
        _overlay(ctx, d, second, "repair")             # replaces the layer; round 1's pixel must survive
        self.assertEqual(bytes(d.composite()), bytes(second))
        self.assertEqual([l.name for l in d.layers], ["Layer 1", "repair"])

    def test_luminance_transfer_keeps_order(self):
        src = bytearray()
        for v in range(40, 220, 12):
            src.extend((v, v, v, 255))
        n = len(src) // 4
        ramp = [(30, 0, 0), (90, 20, 10), (150, 60, 40), (200, 120, 90), (240, 190, 160)]
        for mode in ("relative", "stretch"):
            out = art.luminance_transfer(n, 1, src, ["m"] * n, {"m": ramp}, mode=mode)
            idx = [ramp.index(tuple(out[i:i + 3])) for i in range(0, len(out), 4)]
            self.assertEqual(idx, sorted(idx), mode)
            self.assertGreater(len(set(idx)), 2, mode)

    def test_antialias_pair_matcher_stays_in_ramps(self):
        w = h = 12
        d = bytearray(w * h * 4)
        A, C, X = (200, 50, 50), (20, 20, 60), (0, 255, 0)
        for y in range(h):
            for x in range(w):
                c = C if x < y // 3 + 2 else A
                d[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes((*c, 255))
        allowed = {A, C, (120, 40, 50)}
        art.antialias(w, h, d, 0.5, lambda a, b: art.Matcher(sorted(allowed | {X} - {X})))
        cols = {tuple(d[i:i + 3]) for i in range(0, len(d), 4)}
        self.assertTrue(cols <= allowed, cols)


class PipelineTests(Base):
    def test_reference_dry_run_auto(self):
        self.proj.new_asset("hero", {"references": [self.ref], "height": 48})
        status = Pipeline(self.proj, "hero").run(auto_approve=True, log=quiet)
        self.assertEqual(status, "complete")
        a = self.proj.asset("hero")
        png = a.output("export", "png")
        im = imaging.load_rgba(png)
        bbox = imaging.content_bbox(im, 1)
        self.assertLessEqual(bbox[3] - bbox[1], 48, "sprite incl. outline must fit the requested height")
        self.assertGreaterEqual(bbox[3] - bbox[1], 46)
        self.assertTrue(bbox[0] >= 2 and bbox[1] >= 2 and im.width - bbox[2] >= 2 and im.height - bbox[3] >= 2,
                        "canvas_margin must stay free around the outline")
        data = imaging.to_bytes(im)
        res = art.lint(im.width, im.height, data,
                       palette=[tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
                                for v in json.loads(rd(a.output("export", "meta")))["palette"].values()],
                       max_colors=32)
        self.assertEqual(res["metrics"]["semi_transparent"], 0)
        self.assertEqual(res["metrics"]["off_palette"], 0)
        self.assertLessEqual(res["metrics"]["colors"], 32)
        self.assertEqual(res["metrics"]["components"], 1, "sprite should be one connected shape")
        self.assertTrue(os.path.exists(os.path.join(a.dir, "review", "index.html")))
        self.assertEqual(a.state.steps["detail"].status, "done")
        doc = projectio.load_project(a.output("export", "project"))
        names = [l.name for l in doc.layers]
        self.assertIn("lines", names)
        self.assertIn("polish", names)
        calls = [json.loads(l) for l in rd(os.path.join(self.root, "costs.jsonl")).splitlines()]
        self.assertTrue(calls and all(c["model"] == "stub" for c in calls))
        self.assertEqual({c["role"] for c in calls if c["kind"] == "llm"}, {"director", "vision_pick", "critic", "detail"})
        self.assertEqual(a.state.steps["materials"].info["label_source"], "material map")

    def test_manual_gates_prompt_path(self):
        self.proj.new_asset("mage", {"prompt": "a young mage with a red coat", "height": 40})
        pipe = Pipeline(self.proj, "mage")
        self.assertEqual(pipe.run(log=quiet), "awaiting_gate")
        self.assertEqual(pipe.pending_gate().gate, "A")
        # user edits the spec file before approving
        spec_path = pipe.asset.output("intake", "spec")
        spec = json.loads(rd(spec_path))
        spec["must_keep"] = ["red coat"]
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        cli.main(["--project", self.root, "approve", "mage", "--stop"])
        pipe = Pipeline(self.proj, "mage")
        self.assertEqual(pipe.run(log=quiet), "awaiting_gate")
        self.assertEqual(pipe.pending_gate().gate, "B")
        n = pipe.asset.state.steps["concept"].info["candidates"]
        self.assertEqual(n, 4)
        cli.main(["--project", self.root, "approve", "mage", "--pick", "2", "--stop"])
        pipe = Pipeline(self.proj, "mage")
        self.assertEqual(pipe.run(log=quiet), "awaiting_gate")
        self.assertEqual(pipe.pending_gate().gate, "C")
        a = pipe.asset
        self.assertEqual(rd(a.output("concept", "concept"), True), rd(a.output("concept", "candidate2"), True))
        self.assertIn("red coat", rd(a.output("intake", "spec")))
        cli.main(["--project", self.root, "approve", "mage"])
        self.assertEqual(self.proj.asset("mage").state.status, "complete")

    def test_reject_reruns_step_with_note(self):
        self.proj.new_asset("rogue", {"prompt": "a rogue"})
        pipe = Pipeline(self.proj, "rogue")
        pipe.run(log=quiet)                      # gate A
        pipe.decide(True)
        pipe.run(log=quiet)                      # gate B
        pipe.decide(False, note="more contrast")
        pipe.run(log=quiet)                      # concept reruns, gate B again
        a = self.proj.asset("rogue")
        self.assertEqual(a.state.steps["concept"].version, 2)
        self.assertEqual(a.state.inputs["notes"]["concept"], ["more contrast"])
        self.assertIn("more contrast", rd(os.path.join(a.dir, a.state.steps["concept"].info["dir"], "concept_prompt.txt")))

    def test_rerun_from_keeps_old_versions(self):
        self.proj.new_asset("hero", {"references": [self.ref], "height": 32})
        Pipeline(self.proj, "hero").run(auto_approve=True, log=quiet)
        pipe = Pipeline(self.proj, "hero")
        pipe.reset_from("lines")
        pipe.run(auto_approve=True, log=quiet)
        a = self.proj.asset("hero")
        self.assertEqual(a.state.steps["lines"].version, 2)
        self.assertEqual(a.state.steps["rebuild"].version, 1)
        self.assertTrue(os.path.isdir(os.path.join(a.dir, "steps", "07_lines", "v001")))

    def test_budget_cap(self):
        self.proj.new_asset("hero", {"references": [self.ref]})
        self.proj.log_cost({"asset": "hero", "kind": "llm", "role": "x", "model": "x", "usd": 99})
        with self.assertRaises(BudgetExceeded):
            Pipeline(self.proj, "hero").run(auto_approve=True, log=quiet)
        self.assertEqual(self.proj.asset("hero").state.steps["intake"].status, "failed")



if __name__ == "__main__":
    unittest.main()
