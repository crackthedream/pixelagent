"""Offline regressions for local-edit coordinates and interrupted Classic drawings."""
import copy
import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from PIL import Image

from pixelagent import config, imaging, synth
from pixelagent.models import BudgetExceeded
from pixelagent.pipeline import Ctx, Pipeline
from pixelagent.pixelart import source_box, sprite_from_pixel_art
from pixelagent.steps import artist_step, concept, pixelize


def block_fixture():
    bg = (222, 222, 226)
    cells = Image.new("RGB", (12, 12), bg)
    for y in range(3, 9):
        for x in range(3, 9):
            cells.putpixel((x, y), (180, 70, 80))
    cells.putpixel((5, 2), (90, 60, 100))
    cells.putpixel((5, 6), (30, 200, 60))
    grid = {"p": 8, "px": 8, "py": 8, "x0": 0, "y0": 0,
            "xs": list(range(0, 97, 8)), "ys": list(range(0, 97, 8))}
    image = cells.resize((96, 96), Image.Resampling.NEAREST)
    old, geom = sprite_from_pixel_art(image, margin=2, grid=grid)
    cells.putpixel((5, 2), bg)
    return image, cells.resize(image.size, Image.Resampling.NEAREST), old, geom


class NativeRevisionIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="revision_integrity_")
        self.root = Path(self.tmp.name)
        self.image, self.edited, self.old, self.geom = block_fixture()
        self.image.save(self.root / "base.png")
        self.edited.save(self.root / "edited.png")

    def tearDown(self):
        self.tmp.cleanup()

    def context(self, fix, name="step"):
        ctx = Ctx.__new__(Ctx)
        ctx.out = str(self.root / name)
        Path(ctx.out).mkdir()
        ctx.cfg = {"finish": {"mode": "native"}, "images": {"draft_size": "1024x1536"}}
        ctx.style = {"canvas_margin": 2}
        ctx.asset = NS(state=NS(inputs={"concept_fix": fix}, steps={}),
                       path=lambda rel: str(self.root / rel))
        ctx.outputs, ctx.info = {}, {}
        return ctx

    def test_erasing_extreme_pixel_preserves_original_geometry_and_outside_pixels(self):
        fix = {"note": "remove hat tip", "box": [4, 2, 1, 1], "geom": self.geom, "from_project": "old"}
        ctx = self.context(fix)
        ctx.input = lambda *_: str(self.root / "edited.png")
        ctx.spec = lambda: NS(asset_type="character")
        ctx.style["canvas_margin"] = 9  # a changed setting cannot move a local edit either
        pixelize._native(ctx)
        new = imaging.load_rgba(ctx.outputs["pixel_base"])
        new_geom = json.loads(Path(ctx.outputs["geom"]).read_text(encoding="utf-8"))
        self.assertEqual(new_geom, self.geom)
        self.assertEqual(new.size, self.old.size)
        self.assertEqual(new.getpixel((4, 2))[3], 0)
        config.load_engine(config.load_config())
        canvas, projectio = importlib.import_module("canvas"), importlib.import_module("project")

        def document(im):
            doc = canvas.Document("hero", *im.size)
            doc.layers[0].cels[0] = bytearray(im.tobytes())
            return doc

        old_doc = document(self.old)
        old_preview = imaging.from_bytes(old_doc.w, old_doc.h, old_doc.composite())
        projectio.save_project(old_doc, str(self.root / "old"))
        ctx.projectio = projectio
        ctx.load_project = lambda *_: document(new)
        artist_step.run(ctx)
        result = imaging.load_rgba(ctx.outputs["preview"])
        self.assertEqual(result.size, self.old.size)
        self.assertEqual(result.getpixel((4, 2))[3], 0)
        self.assertEqual(result.getpixel((4, 6)), (30, 200, 60, 255))
        for y in range(result.height):
            for x in range(result.width):
                if (x, y) != (4, 2):
                    self.assertEqual(result.getpixel((x, y)), old_preview.getpixel((x, y)), (x, y))
        self.assertNotIn("concept_fix", ctx.asset.state.inputs)

    def test_new_pixel_in_original_margin_does_not_move_crop(self):
        edited = self.image.copy()
        # Source cell (2, 4) belongs to the original left margin at sprite (1, 4).
        edited.paste((20, 140, 190), (16, 32, 24, 40))
        new, geom = sprite_from_pixel_art(edited, geometry=self.geom)
        self.assertEqual(geom, self.geom)
        self.assertEqual(new.getpixel((1, 4)), (20, 140, 190, 255))
        self.assertEqual(new.getpixel((4, 6)), self.old.getpixel((4, 6)))

    def test_invalid_or_resized_geometry_is_rejected(self):
        for change in (lambda g: g.update(size=[1, 1]),
                       lambda g: g["grid"].update(xs=[0, 8, 7, 96]),
                       lambda g: g.update(source_size=[192, 192]),
                       lambda g: g.pop("grid")):
            geom = copy.deepcopy(self.geom)
            change(geom)
            with self.subTest(geometry=geom), self.assertRaisesRegex(ValueError, "cannot align"):
                sprite_from_pixel_art(self.edited, geometry=geom)

    def test_mask_uses_uneven_independent_axis_edges(self):
        geom = {"source_size": [100, 150], "size": [8, 8], "crop": [1, 1, 7, 7], "margin": 1,
                "s": 12, "gx0": 1, "gy0": 2,
                "grid": {"p": 12, "px": 10, "py": 14, "x0": 1, "y0": 2,
                         "xs": [1, 10, 20, 31, 43, 56, 70, 85, 100],
                         "ys": [2, 17, 33, 50, 68, 87, 107, 128, 150]}}
        self.assertEqual(source_box(geom, [3, 3, 1, 1], (100, 150), pad=1), (20, 33, 56, 87))
        Image.new("RGB", (100, 150), "white").save(self.root / "base.png")
        fix = {"note": "red eye", "box": [3, 3, 1, 1], "geom": geom, "from_concept": "base.png"}
        ctx = self.context(fix)
        calls = []

        def generate(*args, **kwargs):
            calls.append(kwargs)
            mask = imaging.load_rgba(kwargs["mask"])
            hole = Image.eval(mask.getchannel("A"), lambda a: 255 - a)
            self.assertEqual(hole.getbbox(), (20, 33, 56, 87))
            return [str(self.root / "base.png")]

        ctx.backend = NS(images=NS(generate=generate))
        concept._revise(ctx, fix)
        self.assertEqual(calls[0]["size"], "100x150")

    def test_native_edit_rejects_model_image_size_change(self):
        fix = {"note": "red hat", "box": [4, 2, 1, 1], "geom": self.geom, "from_concept": "base.png"}
        ctx = self.context(fix)
        Image.new("RGB", (192, 192), "white").save(self.root / "wrong.png")
        ctx.backend = NS(images=NS(generate=lambda *a, **k: [str(self.root / "wrong.png")]))
        with self.assertRaisesRegex(ValueError, "image size differs"):
            concept._revise(ctx, fix)
        self.assertNotIn("concept", ctx.outputs)
        self.assertNotIn("concept_done", fix)

    def test_missing_geometry_stops_before_image_request(self):
        fix = {"note": "red hat", "box": [4, 2, 1, 1], "from_concept": "base.png"}
        ctx = self.context(fix)
        with self.assertRaisesRegex(ValueError, "geometry is missing"):
            concept._revise(ctx, fix)  # no backend exists: a model request would fail this test

    def test_completion_refuses_changed_canvas_or_missing_baseline_and_keeps_request(self):
        for missing in (False, True):
            fix = {"note": "red hat", "box": [4, 2, 1, 1], "from_project": "old"}
            ctx = self.context(fix, f"completion_{missing}")
            ctx.load_project = lambda *_: NS(w=10, h=10)
            if not missing:
                (self.root / "old").mkdir()
                (self.root / "old" / "project.json").write_text("{}", encoding="utf-8")
            else:
                fix["from_project"] = "absent"
            ctx.projectio = NS(load_project=lambda *_: NS(w=10, h=11))
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "cannot preserve"):
                artist_step.run(ctx)
            self.assertIs(ctx.asset.state.inputs["concept_fix"], fix)
            self.assertEqual(ctx.outputs, {})


class ClassicInterruptionTests(unittest.TestCase):
    def setUp(self):
        import test_openai_backend as fixtures
        self.fixture = fixtures.OpenAIBackendTests()
        self.fixture.setUp()
        self.proj, self.client = self.fixture.proj, self.fixture.client
        self.proj.config["finish"] = {"mode": "artist", "max_usd": 10, "max_turns": 5, "face_pass": False}
        self.proj.config["budget"]["max_usd_per_asset"] = 100
        self.calls = []
        self.finishing = False

        def create(**kwargs):
            self.calls.append(kwargs)
            name = "finish" if self.finishing else "draw"
            args = {"summary": "done"} if self.finishing else {
                "ops": [{"op": "rect", "x": 6, "y": 6, "w": 12, "h": 30, "color": "#3050a0", "fill": True}]}
            out = NS(type="function_call", name=name, call_id=f"call{len(self.calls)}", arguments=json.dumps(args))
            return NS(id=f"resp{len(self.calls)}", output=[out],
                      usage=NS(input_tokens=500, output_tokens=50, input_tokens_details=NS(cached_tokens=0)))

        self.client.responses.create = create
        self.proj.new_asset("hero", {"prompt": "hero"})

    def tearDown(self):
        self.fixture.tearDown()

    def check_interruption_and_resume(self, exception, message):
        with self.assertRaisesRegex(exception, message):
            Pipeline(self.proj, "hero", client=self.client).run(auto_approve=True, log=lambda *_: None)
        a = self.proj.asset("hero")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(a.state.steps["artist"].status, "failed")
        self.assertNotIn("export", a.state.steps)
        self.assertNotEqual(a.state.steps.get("review") and a.state.steps["review"].status, "awaiting_gate")
        checkpoint = Path(a.dir) / "checkpoints" / "artist" / "project"
        self.assertTrue(checkpoint.is_dir())
        self.finishing = True
        self.proj.config["finish"].update(max_usd=10, max_turns=5)
        self.assertEqual(Pipeline(self.proj, "hero", client=self.client).run(auto_approve=True, log=lambda *_: None),
                         "complete")
        self.assertEqual(len(self.calls), 2)
        result = imaging.load_rgba(self.proj.asset("hero").output("export", "png"))
        self.assertIn((48, 80, 160, 255), result.getdata())
        self.assertFalse(checkpoint.exists())

    def test_local_spend_cap_checkpoints_instead_of_completing(self):
        self.proj.config["finish"]["max_usd"] = 0.000001
        self.check_interruption_and_resume(BudgetExceeded, "local budget")

    def test_turn_cap_checkpoints_instead_of_completing(self):
        self.proj.config["finish"]["max_turns"] = 1
        self.check_interruption_and_resume(RuntimeError, "turn limit")

    def test_interrupted_draw_does_not_start_face_pass(self):
        from pixelagent.artist import run_artist
        ref = Path(self.fixture.tmp) / "ref.png"
        synth.draw_character(seed=1, size=384, background=(200, 200, 204))[0].save(ref)
        result = run_artist(self.proj, "hero", str(ref), height=40, client=self.client,
                            max_turns=1, max_usd=10, face=(4, 4, 8, 8), face_turns=3, log=lambda *_: None)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["phases"], ["draw"])
        self.assertIn("draw: stopped: turn limit", result["stop"])

    def test_finished_draw_cannot_silently_skip_required_face_when_budget_runs_out(self):
        from pixelagent.artist import run_artist
        ref = Path(self.fixture.tmp) / "ref.png"
        synth.draw_character(seed=1, size=384, background=(200, 200, 204))[0].save(ref)
        self.finishing = True
        result = run_artist(self.proj, "hero", str(ref), height=40, client=self.client,
                            max_turns=2, max_usd=0.000001, face=(4, 4, 8, 8), face_turns=3, log=lambda *_: None)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("draw: finished; face: stopped: local budget", result["stop"])


if __name__ == "__main__":
    unittest.main()
