import os
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from pixelagent.pipeline import Pipeline
from pixelagent.steps.scene import scroll_factor, seam_error, to_pixel
from pixelagent.store import Project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def quiet(*_):
    pass


class SceneTests(unittest.TestCase):
    def test_layers_become_binary_alpha_pixel_art_with_few_colours(self):
        rng = np.random.default_rng(3)
        a = np.zeros((200, 300, 4), np.uint8)
        a[100:, :, :3] = rng.integers(0, 255, (100, 300, 3))
        a[100:, :, 3] = 220                                       # the image model's slightly see-through shapes
        px = np.asarray(to_pixel(Image.fromarray(a), (150, 100), 12, False, None))
        self.assertEqual(px.shape, (100, 150, 4))
        self.assertEqual(set(np.unique(px[..., 3]).tolist()), {0, 255})  # alpha is binary
        self.assertTrue((px[:49, :, 3] == 0).all() and (px[51:, :, 3] == 255).all())
        self.assertLessEqual(len(np.unique(px[px[..., 3] > 0][:, :3], axis=0)), 12)
        sky = np.asarray(to_pixel(Image.new("RGBA", (300, 200), (120, 160, 220, 255)), (150, 100), 8, True, None))
        self.assertTrue((sky[..., 3] == 255).all())

    def test_seam_error_sees_breaks_across_the_wrap(self):
        a = np.zeros((10, 20, 4), np.uint8)
        a[..., 3] = 255
        a[:, :, 0] = np.arange(20)[None, :] * 10                   # a ramp: the last column differs from the first
        self.assertGreater(seam_error(Image.fromarray(a)), 50)          # 190 in one channel of three
        a[:, :, 0] = 50
        self.assertEqual(seam_error(Image.fromarray(a)), 0.0)
        b = np.zeros((10, 20, 4), np.uint8)                        # clear on both ends: matches
        self.assertEqual(seam_error(Image.fromarray(b)), 0.0)

    def test_a_layer_is_cut_where_it_repeats_and_blended_without_new_colours(self):
        from pixelagent.steps.scene import best_wrap, wrap_layer
        W, H = 200, 40
        a = np.zeros((H, W, 4), np.uint8)
        a[..., 3] = 255
        a[:, :, 0] = (np.arange(W) % 37) * 6                      # a pattern that repeats every 37 columns
        a[:, :, 1] = 90
        im = Image.fromarray(a)
        x0, P, err = best_wrap(im, min_frac=0.75, blend=8)
        self.assertEqual(P % 37, 0)                                # cut on a whole number of repeats
        self.assertLess(err, 1.0)
        out = np.asarray(wrap_layer(im, x0, P, 8))
        self.assertEqual(out.shape[1], P)
        colours = {tuple(c) for c in a.reshape(-1, 4)}
        self.assertTrue({tuple(c) for c in out.reshape(-1, 4)} <= colours)      # the blend adds no colours
        gap = np.zeros((H, W, 4), np.uint8)                        # trees with clear gaps between them
        for x in (10, 70, 130):
            gap[:, x:x + 30] = (40, 120, 60, 255)
        x0, P, err = best_wrap(Image.fromarray(gap), min_frac=0.6, blend=8)
        self.assertEqual(err, 0.0)

    def test_scroll_factors_grow_with_nearness(self):
        f = [scroll_factor(d) for d in (0, 0.3, 0.65, 1.0)]
        self.assertEqual(f, sorted(f))
        self.assertEqual((f[0], f[-1]), (0.05, 1.0))

    def test_a_scene_runs_and_exports_for_the_engines(self):
        tmp = tempfile.mkdtemp(prefix="pxa_scene_")
        try:
            shutil.copy(os.path.join(ROOT, "config.toml"), os.path.join(tmp, "config.toml"))
            proj = Project(tmp, {"backend": {"mode": "dry_run"}})
            proj.new_asset("cave", {"kind": "scene", "prompt": "a crystal cave"})
            self.assertEqual(Pipeline(proj, "cave").run(auto_approve=True, log=quiet), "complete")
            a = proj.asset("cave")
            px = a.state.steps["scene_pixel"].info
            self.assertEqual(px["size"], [810, 540])
            out = a.state.steps["scene_export"].outputs
            for k in ("png", "png_x2", "parallax", "layers", "meta", "godot", "tiled", "unity", "readme"):
                self.assertIn(k, out)
            with open(a.path(out["godot"]), encoding="utf-8") as f:
                tscn = f.read()
            self.assertIn('type="ParallaxBackground"', tscn)
            self.assertEqual(tscn.count('type="ParallaxLayer"'), 4)
            self.assertIn("motion_mirroring = Vector2(810, 0)", tscn)
            with open(a.path(out["tiled"]), encoding="utf-8") as f:
                tmx = f.read()
            self.assertEqual(tmx.count("<imagelayer"), 4)
            self.assertIn('repeatx="1"', tmx)
            layers = sorted(os.listdir(a.path(out["layers"])))
            self.assertEqual(len(layers), 4)
            im = Image.open(os.path.join(a.path(out["layers"]), layers[0]))
            self.assertEqual(im.size, (810, 540))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
