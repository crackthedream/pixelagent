import os
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from pixelagent.pipeline import Pipeline
from pixelagent.steps.kit import LEVEL, T, _roles, _wrap_h, _wrap_v, level_tiles
from pixelagent.store import Project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def quiet(*_):
    pass


class KitTests(unittest.TestCase):
    def test_every_role_the_test_level_uses_comes_from_the_chunk(self):
        roles = {r for _, r, _, _ in _roles()}
        self.assertEqual(len(roles), 6 * 4 + 4)                      # a 6x4 ground block and a 4-tile platform
        used = {r for row in level_tiles() for r in row if r}
        self.assertTrue(used <= roles, used - roles)
        self.assertEqual(len({len(r) for r in LEVEL}), 1)

    def test_repeating_tiles_wrap_onto_themselves_without_new_colours(self):
        rng = np.random.default_rng(2)
        pal = np.array([[90, 140, 70, 255], [120, 90, 60, 255], [70, 60, 50, 255]], np.uint8)
        strip = pal[rng.integers(0, 3, (T, 3 * T))]
        tile = _wrap_h(strip, 0, T, 8)
        self.assertEqual(tile.shape, (T, T, 4))
        self.assertTrue({tuple(c) for c in tile.reshape(-1, 4)} <= {tuple(c) for c in pal})
        # column 0 mostly continues column T-1's neighbour in the strip (what followed the tile)
        self.assertGreater((tile[:, 0] == strip[:, T]).all(1).mean(), 0.7)
        col = pal[rng.integers(0, 3, (3 * T, T))]
        self.assertEqual(_wrap_v(col, 0, T, 8).shape, (T, T, 4))

    def test_a_kit_runs_and_exports_for_the_engines(self):
        tmp = tempfile.mkdtemp(prefix="pxa_kit_")
        try:
            shutil.copy(os.path.join(ROOT, "config.toml"), os.path.join(tmp, "config.toml"))
            proj = Project(tmp, {"backend": {"mode": "dry_run"}})
            proj.new_asset("cave", {"kind": "scene", "prompt": "a crystal cave"})
            self.assertEqual(Pipeline(proj, "cave").run(auto_approve=True, log=quiet), "complete")
            proj.new_asset("cavekit", {"kind": "kit", "prompt": "cave floor", "scene": "cave"})
            self.assertEqual(Pipeline(proj, "cavekit").run(auto_approve=True, log=quiet), "complete")
            a = proj.asset("cavekit")
            self.assertEqual(a.state.steps["kit_plan"].info["scene"], "cave")
            out = a.state.steps["kit_export"].outputs
            ts = Image.open(a.path(out["tileset"]))
            self.assertEqual(ts.size, (6 * T, 5 * T))                 # 28 tiles, 6 per row
            with open(a.path(out["godot"]), encoding="utf-8") as f:
                tres = f.read()
            self.assertIn('type="TileSet"', tres)
            self.assertEqual(tres.count("one_way = true"), 4)          # the platform's tiles
            self.assertEqual(tres.count("/0 = 0"), 28)
            with open(a.path(out["tiled_level"]), encoding="utf-8") as f:
                tmx = f.read()
            self.assertIn('source="tileset.tsx"', tmx)
            data = tmx.split('<data encoding="csv">')[1].split("</data>")[0]
            self.assertEqual(len([v for v in data.replace("\n", "").split(",") if v.strip()]), 26 * 9)
            level = Image.open(a.path(out["level"]))
            self.assertEqual(level.width, 26 * T)
            self.assertGreaterEqual(level.height, 540)                # the scene stands behind the level
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
