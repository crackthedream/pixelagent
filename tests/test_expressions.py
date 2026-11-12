import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from pixelagent import expressions, synth
from pixelagent.expressions import (_sprite_index, colour_map, face_box, hair_zone, passes, safe_name, skin_fix,
                                    skin_kept)
from pixelagent.pipeline import Pipeline
from pixelagent.steps.build import GRADES
from pixelagent.store import Project


class ExpressionTests(unittest.TestCase):
    def test_colours_snap_to_the_character_or_keep_their_graded_hue(self):
        final = Image.new("RGBA", (3, 1))
        final.putdata([(245, 221, 210, 255), (60, 58, 90, 255), (0, 0, 0, 0)])   # skin, hair navy, background
        to_final = colour_map(final, None)
        self.assertEqual(to_final((246, 222, 211)), (245, 221, 210))           # nearly the skin: the skin itself
        self.assertEqual(to_final((230, 120, 140)), (230, 120, 140))           # a pink tongue stays pink
        graded = colour_map(final, GRADES["medium"])((230, 120, 140))
        self.assertNotEqual(graded, (60, 58, 90))                              # not snapped to the hair
        self.assertGreater(graded[0], graded[2])                               # still a warm pink, only muted

    def test_face_box_from_labelled_eyes(self):
        w, h = 30, 60
        labels = [None] * (w * h)
        for y in range(14, 17):
            for x in list(range(10, 13)) + list(range(17, 20)):
                labels[y * w + x] = "eyes"
        x0, y0, x1, y1 = face_box(labels, w, h)
        self.assertLessEqual(x0, 10)
        self.assertGreaterEqual(x1, 20)
        self.assertLess(y0, 14)                                                # room for the brows
        self.assertGreaterEqual(y1, 17 + 4)                                    # down to the mouth

    def test_image_coordinates_map_to_sprite_pixels_on_the_grid(self):
        edges = [3.0, 13.0, 23.5, 33.0, 43.0]                                  # a drifting block grid
        self.assertEqual(_sprite_index(edges, 3.0, 0), 0)
        self.assertEqual(_sprite_index(edges, 24.0, 0), 2)
        self.assertEqual(_sprite_index(edges, 24.0, 2), 4)                     # margin - crop
        self.assertEqual(_sprite_index(edges, 999, 0), 3)                      # clamped to the last block

    def test_a_shifted_skin_tone_is_moved_back_and_noise_is_not_a_change(self):
        skin, hair, eye = (245, 221, 210, 255), (60, 58, 90, 255), (40, 120, 70, 255)
        base = np.zeros((8, 10, 4), np.uint8)
        base[...] = skin
        base[0] = hair
        base[3, 2:4] = base[3, 6:8] = eye
        where = np.ones((8, 10), bool)
        read = base.copy().astype(int)
        rng = np.random.default_rng(1)
        face = (base[..., :3] == skin[:3]).all(-1)
        read[face, :3] += np.array([-18, -22, -40])                  # the edit repainted the skin tan
        read[..., :3] += rng.integers(-2, 3, read[..., :3].shape)    # an image model's colours are never exact
        read[3, 2:4] = read[3, 6:8] = (245, 221, 210, 255)           # ...and closed the eyes: skin where they were
        read[4, 2:4] = read[4, 6:8] = (70, 40, 40, 255)              # a lash line below
        out = skin_fix(np.clip(read, 0, 255).astype(np.uint8), base, where)
        same = (out == base).all(-1)
        self.assertTrue(same[face & (np.arange(8)[:, None] != 4)].all())       # the skin is the original skin again
        self.assertTrue(same[0].all())                                           # the hair's noise is not a change
        self.assertFalse(same[3, 2:4].any() or same[4, 2:4].any())               # the eyes did change
        self.assertTrue((np.abs(out[4, 2].astype(int) - (70, 40, 40, 255)) <= 3).all())   # the lash keeps its colour
        final = base.copy()
        self.assertEqual(skin_kept(final, final, where), 1.0)
        worse = final.copy()
        worse[face] = (220, 190, 150, 255)
        self.assertEqual(skin_kept(final, worse, where), 0.0)

    def test_the_fringe_and_side_locks_are_out_of_bounds(self):
        skin, hair, eye = (245, 221, 210, 255), (120, 80, 60, 255), (40, 120, 70, 255)
        f = np.zeros((20, 20, 4), np.uint8)
        f[2:18, 2:18] = skin
        f[2:8, 2:18] = hair                                   # the hair above the face...
        f[8:10, 4:7] = hair                                   # ...a fringe lock hanging into the box
        f[8:16, 2:4] = hair                                   # a side lock at the left edge
        f[11, 6:9] = f[11, 12:15] = eye                       # eyes, with skin all around
        f[10, 12:15] = hair                                   # a brow in the hair's colour, not touching the fringe
        box = (2, 8, 18, 16)
        z = hair_zone(f, box)
        self.assertTrue(z[8:10, 4:7].all() and z[8:16, 2:4].all())
        self.assertFalse(z[11, 6:9].any() or z[11, 12:15].any() or z[10, 12:15].any())
        self.assertFalse(z[:8].any() or z[16:].any())          # only inside the box
        bald = f.copy()
        bald[2:8, 2:18] = skin                                 # nothing but skin above: no hair to protect
        self.assertFalse(hair_zone(bald, box).any())
        f[8:15, 2:18] = hair                                   # hair over most of the box: not a fringe, no zone
        self.assertFalse(hair_zone(f, box).any())

    def test_names_and_the_pass_rule(self):
        self.assertEqual(safe_name("Smug!"), "smug")
        self.assertEqual(safe_name("a shy, blushing smile"), "a-shy-blushing-smile")
        with self.assertRaises(ValueError):
            safe_name("!!!")
        ok = {"reads": True, "same_skin": True, "same_hair": True, "eyes_in_style": True, "skin_kept": 0.8}
        self.assertTrue(passes(ok))
        self.assertFalse(passes({**ok, "eyes_in_style": False}))
        self.assertFalse(passes({**ok, "skin_kept": 0.2}))
        self.assertTrue(passes({"skin_kept": 0.9}))                              # no vision check: the skin alone


class ExpressionRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pxa_expr_")
        self.proj = Project(os.path.join(self.tmp, "p"), {"backend": {"mode": "dry_run"}})
        im, _ = synth.draw_character(2, size=384, background=(200, 200, 204))
        ref = os.path.join(self.tmp, "ref.png")
        im.save(ref)
        self.proj.new_asset("hero", {"references": [ref], "height": 48})
        Pipeline(self.proj, "hero").run(auto_approve=True, log=lambda *_a, **_k: None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_your_own_expression_and_a_retry_after_a_failed_check(self):
        calls, prompts_seen, sizes = [], [], []
        shared = self.proj.config["images"].get("draft_size")

        def judge(backend, neutral, face, text, out_path):
            calls.append(text)
            bad = len(calls) == 1
            return expressions.FaceCheck(reads=True, same_skin=True, same_hair=True, eyes_in_style=not bad,
                                         issue="the eyes turned red" if bad else "")
        from pixelagent import models
        real = models.StubImages.generate

        def generate(self_, role, prompt, n, out_dir, refs=None, stub=None, quality=None, mask=None, size=None):
            prompts_seen.append(prompt)
            sizes.append((size, self_.cfg["images"].get("draft_size")))
            return real(self_, role, prompt, n, out_dir, refs, stub, quality, mask, size)
        with mock.patch.object(expressions, "judge", judge), mock.patch.object(models.StubImages, "generate", generate):
            r = expressions.make(self.proj, "hero", custom={"Smug!": "a smug half-smile with one brow raised"},
                                 log=lambda *_: None)
        self.assertEqual(list(r["expressions"]), ["smug"])                       # only the user's own: no presets
        e = r["expressions"]["smug"]
        self.assertEqual((e["attempts"], e["kept_attempt"], e["passed"], e["custom"]), (2, 2, True, True))
        self.assertIn("smug half-smile", prompts_seen[0])
        self.assertIn("the eyes turned red", prompts_seen[1])
        with Image.open(self.proj.asset("hero").output("concept", "concept")) as c:
            want = f"{c.width}x{c.height}"
        # the concept's size goes with each call; the project's shared image size is never changed
        self.assertEqual(sizes, [(want, shared), (want, shared)])
        self.assertNotIn("rejected", prompts_seen[0])
        a = self.proj.asset("hero")
        final = Image.open(a.output("export", "png")).convert("RGBA")
        face = Image.open(os.path.join(a.dir, "expressions", "smug.png")).convert("RGBA")
        x0, y0, x1, y1 = r["face_box"]
        fa, na = np.asarray(final).copy(), np.asarray(face).copy()
        fa[y0:y1, x0:x1] = na[y0:y1, x0:x1] = 0
        self.assertTrue(np.array_equal(fa, na))                                  # outside the face: pixel for pixel
        with self.assertRaises(ValueError):
            expressions.make(self.proj, "hero", custom={"x": "  "}, log=lambda *_: None)
        # presets still work by name, next to the user's own: the face is not located again (a box moved by one
        # pixel would drop the faces made before)
        with mock.patch.object(expressions, "locate_face", side_effect=AssertionError("located again")):
            r = expressions.make(self.proj, "hero", ["blink"], log=lambda *_: None)
        self.assertEqual(list(r["expressions"]), ["smug", "blink"])
        moved = [r["face_box"][0] + 1] + r["face_box"][1:]
        with mock.patch.object(expressions, "locate_face", return_value=tuple(moved)):
            r2 = expressions.make(self.proj, "hero", ["happy"], log=lambda *_: None, relocate=True)
        self.assertEqual((list(r2["expressions"]), r2["face_box"]), (["happy"], moved))   # asked for: a fresh set
        with open(os.path.join(a.dir, "expressions", "expressions.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["expressions"]["happy"]["passed"])


if __name__ == "__main__":
    unittest.main()
