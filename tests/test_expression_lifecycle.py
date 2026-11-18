"""Expression lifecycle at the make/UI/pack seam, with free model doubles."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image

from pixelagent import cancel, expressions, gamepack
from pixelagent.store import Project


class ExpressionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pxa_expression_lifecycle_")
        self.addCleanup(self.tmp.cleanup)
        self.proj = Project(self.tmp.name, {"backend": {"mode": "dry_run"},
                                          "finish": {"mode": "native", "grade": "none"},
                                          "expressions": {"judge": True, "retries": 1}})
        self.asset = self.proj.new_asset("hero", {"kind": "character"})
        self.root = Path(self.asset.dir)
        self.sprite = Image.new("RGBA", (8, 8), (180, 200, 230, 255))
        self.sprite.save(self.root / "base.png")
        self.sprite.save(self.root / "final.png")
        self.sprite.resize((80, 80), Image.Resampling.NEAREST).save(self.root / "concept.png")
        grid = {"xs": list(range(0, 81, 10)), "ys": list(range(0, 81, 10)), "px": 10, "py": 10}
        self.write_json("geom.json", {"grid": grid, "margin": 0, "crop": [0, 0, 8, 8],
                                      "s": 10, "gx0": 0, "gy0": 0})
        self.write_json("labels.json", {"width": 8, "height": 8, "labels": ["skin"] * 64})
        self.write_json("meta.json", {"pivot": [4, 7]})
        for step, outputs in {"concept": {"concept": "concept.png"},
                              "pixelize": {"pixel_base": "base.png", "geom": "geom.json"},
                              "materials": {"labelmap": "labels.json"},
                              "export": {"png": "final.png", "meta": "meta.json"}}.items():
            rec = self.asset.record(step)
            rec.status, rec.outputs = "done", outputs
        self.asset.save()
        self.calls = []
        self.cancel_on = None
        self.reject = False
        self.box = (1, 1, 6, 4)
        backend = SimpleNamespace(images=SimpleNamespace(generate=self.generate))
        self.backend = self.patch(expressions, "make_backend", return_value=backend)
        self.location = self.patch(expressions, "locate_face", side_effect=self.locate)
        self.judgement = self.patch(expressions, "judge", side_effect=self.judge)
        self.patch_path("pixelagent.pixelart.sprite_from_pixel_art", side_effect=lambda *_a, **_k:
                        (self.sprite.copy(), {}))
        self.addCleanup(cancel.clear, self.proj.root, "hero")

    def patch(self, target, name, **kwargs):
        p = mock.patch.object(target, name, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def patch_path(self, target, **kwargs):
        p = mock.patch(target, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def write_json(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def called(self, kind):
        self.calls.append(kind)
        if self.cancel_on == (kind, self.calls.count(kind)):
            cancel.request(self.proj.root, "hero")

    def locate(self, *_args):
        self.called("locate")
        return self.box

    def generate(self, _role, _prompt, _n, out_dir, refs, *_args, **_kwargs):
        self.called("image")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "gen00.png")
        with Image.open(refs[0]) as im:
            im.save(path)
        return [path]

    def judge(self, *_args):
        self.called("judge")
        return expressions.FaceCheck(reads=not self.reject, same_skin=True, same_hair=True,
                                     eyes_in_style=True, issue="redraw" if self.reject else "")

    def make(self, names, **kwargs):
        return expressions.make(self.proj, "hero", names, log=lambda *_: None, **kwargs)

    def current(self):
        return expressions.load_current(self.proj.asset("hero"))

    def test_cancel_before_start_makes_no_backend_or_model_call(self):
        cancel.request(self.proj.root, "hero")
        with self.assertRaises(cancel.Cancelled):
            self.make(["happy"])
        self.backend.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_cancel_during_location_stops_before_image(self):
        self.cancel_on = ("locate", 1)
        with self.assertRaises(cancel.Cancelled):
            self.make(["happy"])
        self.assertEqual(self.calls, ["locate"])

    def test_cancel_during_image_stops_judge_retry_and_next_expression(self):
        self.cancel_on = ("image", 1)
        self.reject = True
        with self.assertRaises(cancel.Cancelled):
            self.make(["happy", "sad"])
        self.assertEqual(self.calls, ["locate", "image"])

    def test_cancel_during_judge_stops_retry_and_next_expression(self):
        self.cancel_on = ("judge", 1)
        self.reject = True
        with self.assertRaises(cancel.Cancelled):
            self.make(["happy", "sad"])
        self.assertEqual(self.calls, ["locate", "image", "judge"])

    def test_cancel_keeps_previously_finished_expression(self):
        self.cancel_on = ("image", 2)
        with self.assertRaises(cancel.Cancelled):
            self.make(["happy", "sad"])
        self.assertEqual(list(self.current()["expressions"]), ["happy"])
        self.assertEqual(self.calls.count("image"), 2)
        self.assertEqual(self.calls.count("judge"), 1)

    def test_same_source_keeps_face_location_and_prior_expressions(self):
        self.make(["happy"])
        result = self.make(["blink"])
        self.assertEqual(self.location.call_count, 1)
        self.assertEqual(list(result["expressions"]), ["happy", "blink"])
        self.assertTrue(expressions.compatible(self.proj.asset("hero"), result))
        self.assertEqual([c[0] for c in gamepack._clips(self.proj, "hero")[1]],
                         ["stand", "face_happy", "face_blink"])

    def test_regenerating_the_same_name_changes_its_display_revision(self):
        first = self.make(["happy"])["expressions"]["happy"]
        second = self.make(["happy"])["expressions"]["happy"]
        self.assertTrue(first["revision"])
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertNotEqual(first["drawing"], second["drawing"])
        self.assertTrue((self.root / "expressions" / first["drawing"]).is_file())

    def test_reuse_cannot_pick_leftover_drawings_from_an_old_source(self):
        first = self.make(["happy"])["expressions"]["happy"]
        im = self.sprite.copy()
        im.putpixel((0, 0), (0, 0, 0, 255))
        im.save(self.root / "final.png")
        self.make(["sad"])
        self.assertTrue((self.root / "expressions" / first["drawing"]).is_file())
        before = list(self.calls)
        with self.assertRaises(ValueError):
            self.make(["happy"], reuse=True)
        self.assertEqual(self.calls, before)

    def test_reuse_reads_the_kept_retry_without_model_calls(self):
        self.judgement.side_effect = [
            expressions.FaceCheck(reads=False, same_skin=True, same_hair=True, eyes_in_style=True, issue="redraw"),
            expressions.FaceCheck(reads=True, same_skin=True, same_hair=True, eyes_in_style=True, issue=""),
        ]
        first = self.make(["happy"])["expressions"]["happy"]
        self.assertEqual(first["kept_attempt"], 2)
        before = list(self.calls)
        with mock.patch.object(expressions.imaging, "load_rgba", wraps=expressions.imaging.load_rgba) as load:
            second = self.make(["happy"], reuse=True)["expressions"]["happy"]
        self.assertEqual(self.calls, before)
        self.assertEqual(second["drawing"], first["drawing"])
        self.assertEqual(second["kept_attempt"], 2)
        load.assert_any_call(str(self.root / "expressions" / first["drawing"]))

    def test_each_source_change_hides_stale_faces_and_relocates(self):
        for source in ("concept.png", "base.png", "geom.json", "final.png"):
            with self.subTest(source=source):
                self.make(["happy"])
                old = (self.root / source).read_bytes()
                if source.endswith(".json"):
                    geom = json.loads(old)
                    geom["crop"][0] += 1
                    self.write_json(source, geom)
                else:
                    with Image.open(self.root / source) as im:
                        im = im.copy()
                    im.putpixel((0, 0), (99, 100, 101, 255))
                    im.save(self.root / source)
                self.assertEqual(self.current(), {})
                self.assertEqual([c[0] for c in gamepack._clips(self.proj, "hero")[1]], ["stand"])
                self.assertTrue((self.root / "expressions" / "happy.png").is_file())
                before = self.location.call_count
                result = self.make(["sad"])
                self.assertEqual(self.location.call_count, before + 1)
                self.assertEqual(list(result["expressions"]), ["sad"])
                history = list((self.root / "expressions" / "_history").glob("set-*/expressions.json"))
                self.assertTrue(any("happy" in json.loads(p.read_text(encoding="utf-8"))["expressions"]
                                    and (p.parent / "happy.png").is_file() for p in history))
                (self.root / source).write_bytes(old)

    def test_version_reference_or_pending_step_invalidates_old_set(self):
        result = self.make(["happy"])
        asset = self.proj.asset("hero")
        asset.record("concept").status = "pending"
        asset.save()
        self.assertEqual(self.current(), {})
        asset.record("concept").status = "done"
        (self.root / "concept-v2.png").write_bytes((self.root / "concept.png").read_bytes())
        asset.record("concept").outputs["concept"] = "concept-v2.png"
        asset.save()
        self.assertFalse(expressions.compatible(self.proj.asset("hero"), result))
        asset.record("concept").outputs["concept"] = "concept.png"
        asset.save()
        self.assertTrue(expressions.compatible(self.proj.asset("hero"), result))

    def test_interrupted_metadata_replace_keeps_last_complete_checkpoint(self):
        self.make(["happy"])
        with mock.patch.object(expressions.os, "replace", side_effect=OSError("interrupted save")):
            with self.assertRaises(OSError):
                self.make(["blink"])
        self.assertEqual(list(self.current()["expressions"]), ["happy"])
        self.assertEqual(list((self.root / "expressions").glob(".expressions-*.tmp")), [])

    def test_legacy_or_broken_metadata_is_preserved_but_not_published(self):
        result = self.make(["happy"])
        result.pop("source", None)
        self.write_json("expressions/expressions.json", result)
        self.assertEqual(self.current(), {})
        self.assertEqual([c[0] for c in gamepack._clips(self.proj, "hero")[1]], ["stand"])
        self.assertTrue((self.root / "expressions" / "happy.png").is_file())
        self.write_json("expressions/expressions.json", ["old format"])
        self.assertEqual(self.current(), {})
        (self.root / "expressions" / "expressions.json").write_text("{unfinished", encoding="utf-8")
        self.assertEqual(self.current(), {})

    def test_reuse_never_draws_for_stale_or_missing_source(self):
        self.make(["happy"])
        before = list(self.calls)
        with self.assertRaises(ValueError):
            self.make(["sad"], reuse=True)
        self.assertEqual(self.calls, before)
        im = self.sprite.copy()
        im.putpixel((0, 0), (0, 0, 0, 255))
        im.save(self.root / "final.png")
        with self.assertRaises(ValueError):
            self.make(["happy"], reuse=True)
        self.assertEqual(self.calls, before)


if __name__ == "__main__":
    unittest.main()
