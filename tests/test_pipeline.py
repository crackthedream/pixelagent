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
    def _export_lint(self, a, max_colors):
        im = imaging.load_rgba(a.output("export", "png"))
        data = imaging.to_bytes(im)
        res = art.lint(im.width, im.height, data,
                       palette=[tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
                                for v in json.loads(rd(a.output("export", "meta")))["palette"].values()],
                       max_colors=max_colors)
        return im, data, res

    def test_reference_dry_run_auto(self):
        # the reduce finish: the reduction is the sprite (docs/FACE_ROOT_CAUSE.md section 4)
        self.proj.config["finish"]["mode"] = "reduce"
        self.proj.new_asset("hero", {"references": [self.ref], "height": 48})
        status = Pipeline(self.proj, "hero").run(auto_approve=True, log=quiet)
        self.assertEqual(status, "complete")
        a = self.proj.asset("hero")
        budget = int(self.proj.config["finish"]["reduce_colors"])
        im, data, res = self._export_lint(a, budget + 40)
        bbox = imaging.content_bbox(im, 1)
        self.assertLessEqual(bbox[3] - bbox[1], 48, "the sprite must fit the requested height")
        self.assertGreaterEqual(bbox[3] - bbox[1], 46)                   # no outline ring: the figure uses it all
        self.assertTrue(bbox[0] >= 2 and bbox[1] >= 2 and im.width - bbox[2] >= 2 and im.height - bbox[3] >= 2,
                        "canvas_margin must stay free around the sprite")
        self.assertEqual(res["metrics"]["semi_transparent"], 0)
        self.assertEqual(res["metrics"]["off_palette"], 0)
        self.assertGreater(res["metrics"]["colors"], 40)                 # soft shading keeps many shades...
        self.assertLessEqual(res["metrics"]["colors"], budget + 40)      # ...within the budget (+ per-material minimums)
        oc = self.proj.config["style"]["outline_color"]
        oc = tuple(int(oc[i:i + 2], 16) for i in (1, 3, 5))
        self.assertNotIn(oc, {tuple(data[k:k + 3]) for k in range(0, len(data), 4) if data[k + 3]})
        self.assertEqual(res["metrics"]["components"], 1, "sprite should be one connected shape")
        st = a.state.steps
        self.assertEqual(st["rebuild"].info["mode"], "reduce")
        spec = json.loads(rd(a.output("intake", "spec")))
        self.assertEqual(spec["max_colors"], budget)                     # the critic is told the reduce budget
        self.assertEqual((st["lines"].status, st["detail"].status), ("skipped", "skipped"))
        self.assertEqual(st["artist"].status, "done")                    # the reduction passed through as the result
        self.assertIn("reduce", st["artist"].info["reason"])
        for m, k in st["rebuild"].info["colors_per_material"].items():   # eyes and mouth keep their own colours
            if "eye" in m or "mouth" in m:
                self.assertGreaterEqual(k, 1, m)
        calls = [json.loads(l) for l in rd(os.path.join(self.root, "costs.jsonl")).splitlines()]
        self.assertTrue(calls and all(c["model"] == "stub" for c in calls))
        self.assertEqual({c["role"] for c in calls if c["kind"] == "llm"}, {"director", "vision_pick", "critic"})
        self.assertTrue(os.path.exists(os.path.join(a.dir, "review", "index.html")))

    def test_native_finish_reads_the_pixel_art_concept_back_exactly(self):
        # the default finish: the image model draws the sprite as crisp blocks (a test run's stand-in does the same
        # with a synthetic character); pixelize reads it back pixel for pixel, rebuild keeps 3-6 tones per material
        from pixelagent import synth
        self.assertEqual(self.proj.config["finish"]["mode"], "native")
        self.proj.new_asset("pix", {"references": [self.ref], "height": 48})
        self.assertEqual(Pipeline(self.proj, "pix").run(auto_approve=True, log=quiet), "complete")
        a = self.proj.asset("pix")
        st = a.state.steps
        self.assertEqual(st["concept"].info["sources"], ["pixel sprite"])
        prompt = rd(os.path.join(a.dir, st["concept"].info["dir"], "concept_prompt.txt"))
        self.assertIn("STANDING upright", prompt)                                # seated references stand up
        self.assertIn("furniture, bench, chair", prompt)
        self.assertIn("NOT chibi", prompt)
        self.assertEqual(st["pixelize"].info["grid"]["p"], 8.0)                 # the block size drawn
        im, _ = synth.draw_character(seed=0, background=None)
        want = imaging.pixelize(im, 48, margin=0)
        got = imaging.load_rgba(a.output("pixelize", "pixel_base"))
        got = got.crop(got.getbbox())
        self.assertEqual(got.size, want.size)
        ga, wa = imaging.to_bytes(got), imaging.to_bytes(want)
        diff = [abs(ga[k] - wa[k]) for k in range(0, len(ga)) if k % 4 != 3 and ga[k - k % 4 + 3] and wa[k - k % 4 + 3]]
        self.assertLessEqual(max(diff), 3)                                       # pixel for pixel
        self.assertTrue(all(1 <= k <= 6 for k in st["rebuild"].info["colors_per_material"].values()))   # 3-6 (fewer if a material has fewer colours)
        self.assertEqual(st["rebuild"].info["mode"], "native")
        self.assertEqual(st["rebuild"].info["grade"], "medium")                # dusty palette, lifted shadows
        from pixelagent.steps.build import _oklab
        import numpy as np

        def chroma(img):
            px = np.asarray(img.convert("RGBA")).reshape(-1, 4)
            lab = _oklab(px[px[:, 3] > 0, :3].astype(float))
            return np.hypot(lab[:, 1], lab[:, 2]).mean()
        self.assertLess(chroma(imaging.load_rgba(a.output("export", "png"))), chroma(got) * 0.85)
        self.assertEqual((st["lines"].status, st["detail"].status), ("skipped", "skipped"))
        oc = self.proj.config["style"]["outline_color"]
        oc = tuple(int(oc[i:i + 2], 16) for i in (1, 3, 5))
        data = imaging.to_bytes(imaging.load_rgba(a.output("export", "png")))
        self.assertNotIn(oc, {tuple(data[k:k + 3]) for k in range(0, len(data), 4) if data[k + 3]})

    def test_palette_grade_mutes_colours_and_lifts_shadows_in_order(self):
        import numpy as np
        from pixelagent.steps.build import GRADES, _oklab, grade_colours
        rng = np.random.default_rng(1)
        cols = rng.integers(0, 256, (400, 3)).astype(np.uint8)
        lab0 = _oklab(cols.astype(float))
        for name in ("soft", "medium", "strong"):
            lab = _oklab(grade_colours(cols, **GRADES[name]).astype(float))
            c0, c1 = np.hypot(lab0[:, 1], lab0[:, 2]), np.hypot(lab[:, 1], lab[:, 2])
            self.assertLess(c1.mean(), c0.mean() * 0.85, name)              # less saturated
            self.assertGreaterEqual(lab[:, 0].min(), lab0[:, 0].min(), name)  # shadows not darker
            dark = lab0[:, 0] < 0.3
            self.assertGreater((lab[dark, 0] - lab0[dark, 0]).mean(), 0.02, name)   # lifted
            o0, o1 = np.argsort(lab0[:, 0]), lab[np.argsort(lab0[:, 0]), 0]
            self.assertGreaterEqual(np.diff(o1).min(), -0.02, name)          # tones keep their order
        greys = np.array([[0, 0, 0], [128, 128, 128], [255, 255, 255]], np.uint8)
        g = grade_colours(greys, **GRADES["medium"])
        self.assertTrue(all(abs(int(c[0]) - int(c[2])) <= 1 for c in g))   # greys stay grey
        self.assertEqual(tuple(g[2]), (255, 255, 255))                     # white stays white

    def test_native_area_revision_keeps_the_grid_and_the_rest(self):
        self.proj.new_asset("pix", {"references": [self.ref], "height": 48})
        self.assertEqual(Pipeline(self.proj, "pix").run(auto_approve=True, log=quiet), "complete")
        a = self.proj.asset("pix")
        before = imaging.to_bytes(imaging.load_rgba(a.output("artist", "preview")))
        grid = a.state.steps["pixelize"].info["grid"]
        pipe = Pipeline(self.proj, "pix")
        self.assertEqual(pipe.request_fix("a red bow", (8, 8, 6, 6)), "concept")
        pipe.reset_from("concept")
        self.assertEqual(Pipeline(self.proj, "pix").run(log=quiet), "awaiting_gate")      # straight to review
        a = self.proj.asset("pix")
        self.assertEqual(a.state.steps["pixelize"].info["grid"], grid)
        after = imaging.to_bytes(imaging.load_rgba(a.output("artist", "preview")))
        self.assertEqual(len(after), len(before))
        w = imaging.load_rgba(a.output("artist", "preview")).width
        for k in range(0, len(after), 4):
            x, y = (k // 4) % w, (k // 4) // w
            if not (8 <= x < 14 and 8 <= y < 14):
                self.assertEqual(after[k:k + 4], before[k:k + 4], (x, y))

    def test_reduce_keeps_dark_lines_and_eye_highlights(self):
        import numpy as np
        from pixelagent.steps.build import is_focal, reduce_material
        rng = np.random.default_rng(0)
        # an iris: a mid-blue body, a few near-black pupil/lash pixels and a few near-white catchlight pixels
        body = np.clip(rng.normal((70, 100, 200), 12, (120, 3)), 0, 255)
        dark = np.clip(rng.normal((25, 25, 45), 4, (8, 3)), 0, 255)
        light = np.clip(rng.normal((245, 245, 250), 3, (6, 3)), 0, 255)
        rgb = np.vstack([body, dark, light])
        lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]  # noqa: E731
        out, used = reduce_material(rgb, 6, focal=True)
        self.assertLessEqual(len(used), 6)
        self.assertLess(min(lum(c) for c in used), lum(dark.mean(0)) + 8)        # the pupil stays near-black
        self.assertGreater(max(lum(c) for c in used), lum(light.mean(0)) - 8)    # the catchlight stays bright
        self.assertTrue(is_focal("eye_irises") and is_focal("mouth") and not is_focal("hair"))

    def test_reduce_revision_edits_the_concept_and_keeps_the_rest(self):
        self.proj.config["finish"]["mode"] = "reduce"
        self.proj.new_asset("hero", {"references": [self.ref], "height": 48})
        self.assertEqual(Pipeline(self.proj, "hero").run(auto_approve=True, log=quiet), "complete")
        a = self.proj.asset("hero")
        before = imaging.to_bytes(imaging.load_rgba(a.output("artist", "preview")))
        offsets = a.state.steps["pixelize"].info["offsets"]
        pipe = Pipeline(self.proj, "hero")
        self.assertEqual(pipe.request_fix("make the bow red", (10, 10, 6, 6)), "concept")
        pipe.reset_from("concept")
        self.assertEqual(Pipeline(self.proj, "hero").run(log=quiet), "awaiting_gate")   # straight to the review (C)
        a = self.proj.asset("hero")
        st = a.state.steps
        self.assertEqual(Pipeline(self.proj, "hero").pending_gate().gate, "C")
        self.assertEqual((st["concept"].version, st["concept"].info["sources"]), (2, ["revised"]))
        self.assertEqual(st["pixelize"].info["offsets"], offsets)                 # the same grid
        self.assertIn("previous grid", st["pixelize"].info["reason"])
        self.assertNotIn("proportions", st["materials"].info)                     # no proportion retry
        self.assertEqual(st["artist"].info["revised"]["via"], "concept")
        self.assertNotIn("concept_fix", a.state.inputs)
        after = imaging.to_bytes(imaging.load_rgba(a.output("artist", "preview")))
        w = imaging.load_rgba(a.output("artist", "preview")).width
        for k in range(0, len(after), 4):                                         # outside the area: identical
            x, y = (k // 4) % w, (k // 4) // w
            if not (10 <= x < 16 and 10 <= y < 16):
                self.assertEqual(after[k:k + 4], before[k:k + 4], (x, y))

    def test_artist_finish_dry_run(self):
        # the drawing finish (classic): ramp palette, outline ring, polish; a stub model passes the trace through
        self.proj.config["finish"]["mode"] = "artist"
        self.proj.new_asset("hero", {"references": [self.ref], "height": 48})
        self.assertEqual(Pipeline(self.proj, "hero").run(auto_approve=True, log=quiet), "complete")
        a = self.proj.asset("hero")
        im, data, res = self._export_lint(a, 40)
        bbox = imaging.content_bbox(im, 1)
        self.assertLessEqual(bbox[3] - bbox[1], 48, "sprite incl. outline must fit the requested height")
        self.assertGreaterEqual(bbox[3] - bbox[1], 46)
        self.assertEqual(res["metrics"]["semi_transparent"], 0)
        self.assertEqual(res["metrics"]["off_palette"], 0)
        self.assertLessEqual(res["metrics"]["colors"], 40)
        self.assertEqual(res["metrics"]["components"], 1, "sprite should be one connected shape")
        self.assertEqual(a.state.steps["detail"].status, "skipped")
        self.assertIn("dry_run", a.state.steps["artist"].info)
        names = [l.name for l in projectio.load_project(a.output("export", "project")).layers]
        self.assertIn("lines", names)
        self.assertIn("polish", names)
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

    def test_default_height_follows_style_and_explicit_wins(self):
        self.proj.new_asset("d1", {"prompt": "a hero"})
        self.proj.new_asset("d2", {"prompt": "a hero", "height": 48})
        style_default = int(self.proj.config["style"]["sprite_height"])
        for aid, want in (("d1", style_default), ("d2", 48)):
            Pipeline(self.proj, aid).run(until="intake", auto_approve=True, log=quiet)
            spec = json.loads(rd(self.proj.asset(aid).output("intake", "spec")))
            self.assertEqual(spec["height_px"], want)

    def test_test_runs_ignore_paid_limits(self):
        # review finding F10: the free stand-in models used to fail at their first step once paid limits were
        # used up. Paid limits are enforced by the OpenAI backend (test_openai_backend) and the app's checks.
        self.proj.new_asset("hero", {"references": [self.ref]})
        self.proj.log_cost({"asset": "hero", "kind": "llm", "role": "x", "model": "x", "usd": 99})
        Pipeline(self.proj, "hero").run(auto_approve=True, log=quiet)
        self.assertEqual(self.proj.asset("hero").state.status, "complete")
        self.assertIsNotNone(BudgetExceeded)



if __name__ == "__main__":
    unittest.main()
