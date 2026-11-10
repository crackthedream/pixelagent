"""The engine's face kit through MCP: symmetric eyes, highlight on one side, tiny mouths, clear errors."""
import os
import shutil
import tempfile
import unittest

from PIL import Image

from pixelagent.config import load_config, load_engine
from pixelagent.mcp_client import MCPClient


class FaceKitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pxa_kit_")
        self.mcp = MCPClient(os.path.join(load_engine(load_config()), "server.py"), env={"PIXELLINER_MCP_OUT": self.tmp})
        self.mcp.call("new_canvas", {"width": 21, "height": 16, "name": "f"})

    def tearDown(self):
        self.mcp.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def draw(self, ops):
        txt, _, err = self.mcp.call("draw", {"ops": ops})
        return txt, err

    def image(self):
        p = os.path.join(self.tmp, "f.png")
        self.mcp.call("export", {"path": p})
        return Image.open(p).convert("RGBA")

    def test_eyes_are_built_like_the_game_and_mirrored(self):
        txt, err = self.draw([{"op": "eyes", "cx": 10, "y": 4, "size": "m", "style": "open", "iris": "#3a6fd0",
                               "lash": "#101010", "gap": 3}])
        self.assertFalse(err, txt)
        im = self.image()
        px = {(x, y): im.getpixel((x, y)) for x in range(21) for y in range(16) if im.getpixel((x, y))[3]}
        xs = sorted({x for x, _ in px})
        self.assertEqual((xs[0], xs[-1]), (4, 16))        # 5 + 3 gap + 5 = 13 px wide, centred on x=10
        for (x, y), c in px.items():                      # the two eyes are exact mirror images
            self.assertEqual(c, im.getpixel((20 - x, y)), (x, y))
        lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]  # noqa: E731
        lash = (16, 16, 16)
        self.assertEqual([im.getpixel((x, 4))[:3] for x in (6, 7)], [lash] * 2)      # the lash runs across the top
        self.assertEqual(im.getpixel((5, 5))[:3], lash)                              # continues down the outer side
        wing = im.getpixel((4, 5))
        self.assertTrue(wing[3] and wing[:3] != lash)                                # as a softened wing
        self.assertGreater(lum(im.getpixel((6, 5))), 200)                            # a soft white inside that corner
        self.assertLess(lum(im.getpixel((7, 6))), lum(im.getpixel((7, 5))))          # pupil darker than the iris
        self.assertFalse(any(im.getpixel((x, 7))[:3] == lash for x in range(21)))    # no lower outline
        rows = sorted({y for _, y in px})
        self.assertGreaterEqual(rows[-1] - rows[0] + 1, 4)                           # the eye is as tall as the body

    def test_three_quarter_turn_narrows_the_far_eye(self):
        txt, err = self.draw([{"op": "eyes", "cx": 10, "y": 4, "size": "l", "style": "open", "turn": "left",
                               "iris": "#3a6fd0", "lash": "#101010"}])
        self.assertFalse(err, txt)
        im = self.image()
        cols = sorted({x for x in range(21) for y in range(16) if im.getpixel((x, y))[3]})
        runs, start = [], cols[0]
        for a_, b_ in zip(cols, cols[1:] + [None]):
            if b_ is None or b_ != a_ + 1:
                runs.append(a_ - start + 1)
                start = b_
        self.assertEqual(runs, [5, 6])                    # the left (far) eye is a column narrower

    def test_a_bright_iris_becomes_a_mid_tone(self):
        self.draw([{"op": "eyes", "cx": 10, "y": 4, "size": "m", "style": "open", "iris": "#ffb030", "lash": "#101010"}])
        c = self.image().getpixel((8, 5))                 # the iris pixel under the lash
        self.assertLess(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2], 125)

    def test_skin_op_flattens_blotches_and_keeps_features(self):
        skin, blotch, eye = "#f0c8a8", (228, 186, 160, 255), (40, 30, 60, 255)
        self.draw([{"op": "rect", "x": 0, "y": 0, "w": 21, "h": 16, "color": skin, "fill": True},
                   {"op": "pixel", "x": 5, "y": 5, "color": "#e4baa0"}, {"op": "pixel", "x": 9, "y": 6, "color": "#281e3c"}])
        txt, err = self.draw([{"op": "skin", "x": 0, "y": 0, "w": 21, "h": 16}])
        self.assertFalse(err, txt)
        im = self.image()
        self.assertEqual(im.getpixel((5, 5))[:3], (240, 200, 168))   # the blotch became skin
        self.assertEqual(im.getpixel((9, 6))[:3], eye[:3])           # a feature stays

    def test_every_template_is_a_clean_rectangle(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("fk", os.path.join(load_engine(load_config()), "face_kit.py"))
        fk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fk)
        for size, styles in fk.EYES.items():
            w = len(styles["open"][0])
            for style, rows in styles.items():
                self.assertEqual({len(r) for r in rows}, {w}, (size, style))

    def test_xxl_eyes_for_big_headed_characters(self):
        self.mcp.call("new_canvas", {"width": 30, "height": 16, "name": "f"})
        txt, err = self.draw([{"op": "eyes", "cx": 15, "y": 3, "size": "xxl", "style": "open", "iris": "#3a6fd0", "lash": "#101010"}])
        self.assertFalse(err, txt)
        im = self.image()
        xs = sorted({x for x in range(30) for y in range(16) if im.getpixel((x, y))[3]})
        ys = sorted({y for x in range(30) for y in range(16) if im.getpixel((x, y))[3]})
        self.assertEqual((xs[0], xs[-1], xs[-1] - xs[0] + 1), (3, 26, 24))   # 9 + 6 gap + 9, centred on x=15
        self.assertEqual(len(ys), 7)

    def test_xl_eyes_for_big_faces(self):
        txt, err = self.draw([{"op": "eyes", "cx": 10, "y": 3, "size": "xl", "style": "open", "iris": "#3a6fd0", "lash": "#101010"}])
        self.assertFalse(err, txt)
        im = self.image()
        xs = sorted({x for x in range(21) for y in range(16) if im.getpixel((x, y))[3]})
        self.assertEqual((xs[0], xs[-1]), (1, 19))       # 7 + 5 gap + 7 = 19 px wide, centred on x=10

    def test_mouth_colours_come_from_the_skin(self):
        self.draw([{"op": "rect", "x": 0, "y": 0, "w": 21, "h": 16, "color": "#f0c8a8", "fill": True},
                   {"op": "mouth", "cx": 10, "y": 10, "shape": "line"}])
        c = self.image().getpixel((10, 10))[:3]
        self.assertTrue(all(abs(a - b) < 140 for a, b in zip(c, (240, 200, 168))), c)   # soft: near the skin
        self.assertLess(sum(c), sum((240, 200, 168)))                                   # but darker

    def test_mouth_brows_blush_and_errors(self):
        txt, err = self.draw([{"op": "mouth", "cx": 10, "y": 12, "shape": "open_smile", "color": "#802030"},
                              {"op": "brows", "cx": 10, "y": 2, "shape": "angry", "color": "#301010", "gap": 3},
                              {"op": "blush", "cx": 10, "y": 9, "gap": 9, "color": "#f0a0a0"}])
        self.assertFalse(err, txt)
        im = self.image()
        mouth = [(x, y) for x in range(21) for y in range(11, 15) if im.getpixel((x, y))[3]]
        self.assertEqual(sorted({x for x, _ in mouth}), [9, 10, 11, 12])   # a 4-px rounded blob around cx
        for bad in ({"op": "eyes", "cx": 5, "y": 5, "size": "huge"}, {"op": "mouth", "cx": 5, "y": 5, "shape": "grin"}):
            txt, err = self.draw([bad])
            self.assertTrue(err or "error" in txt.lower(), txt)


if __name__ == "__main__":
    unittest.main()
