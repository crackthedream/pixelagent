"""Face trace: small dark features survive the reduction to sprite size; only face cells change."""
import unittest

import numpy as np
from PIL import Image

from pixelagent.face import face_trace, feature_colour, is_face_material, reset_face

SKIN, EYE, HAIR, OUT = (236, 190, 160), (40, 30, 60), (120, 70, 40), (27, 19, 37)


class FaceTests(unittest.TestCase):
    def test_face_material_names(self):
        for yes in ("skin", "eyes_and_mouth", "face_shadow", "Cheek blush", "mouth-inner"):
            self.assertTrue(is_face_material(yes), yes)
        for no in ("shield_face", "clock_face", "hair", "skin_hair_shadow", "plate_armor"):
            self.assertFalse(is_face_material(no), no)

    def test_a_small_dark_eye_survives_where_averaging_loses_it(self):
        cell = np.array([SKIN] * 75 + [EYE] * 25, dtype=np.float32)   # an eye covering a quarter of the cell
        avg = cell.mean(0)
        self.assertGreater(avg.mean(), 140)                            # averaging: still skin-coloured
        self.assertEqual(tuple(int(v) for v in feature_colour(cell)), EYE)
        speck = np.array([SKIN] * 95 + [EYE] * 5, dtype=np.float32)   # a stray dark speck is ignored
        self.assertEqual(tuple(int(v) for v in feature_colour(speck)), SKIN)

    def test_only_face_cells_change(self):
        # sprite 6x4: top row hair, rows 1-3 skin; the concept is 10x larger with a dark eye in cell (2, 2)
        w, h, s = 6, 4, 10
        labels = ["hair"] * w + ["skin"] * (w * 3)
        trace = Image.new("RGBA", (w, h), (*SKIN, 255))
        for i in range(w):
            trace.putpixel((i, 0), (*HAIR, 255))
        trace.putpixel((0, 3), (*EYE, 255))            # the eye colour is in the palette; the outline colour too
        trace.putpixel((5, 3), (*OUT, 255))
        concept = Image.new("RGBA", (w * s, h * s), (*SKIN, 255))
        for x in range(2 * s, 2 * s + 6):
            for y in range(2 * s, 2 * s + 6):
                concept.putpixel((x, y), (*EYE, 255))
        for x in range(w * s):                         # dark strands in the hair row must NOT be pulled in
            for y in range(0, s, 3):
                concept.putpixel((x, y), (*OUT, 255))
        geom = {"s": s, "margin": 0, "crop": [0, 0], "gx0": 0, "gy0": 0}
        out, changed = face_trace(trace, concept, geom, labels, w, (0, 0, w, h), pad=0)
        self.assertEqual(out.getpixel((2, 2))[:3], EYE)                  # the eye appears in the face
        self.assertEqual([out.getpixel((i, 0))[:3] for i in range(w)], [HAIR] * w)   # hair row untouched
        self.assertGreaterEqual(changed, 1)
        # resetting puts the face back to the trace and leaves the rest of the sprite alone
        sprite = out.copy()
        sprite.putpixel((1, 0), (255, 0, 0, 255))
        back = reset_face(sprite, trace, labels, w, (0, 0, w, h), pad=0)
        self.assertEqual(back.getpixel((2, 2)), trace.getpixel((2, 2)))
        self.assertEqual(back.getpixel((1, 0))[:3], (255, 0, 0))


if __name__ == "__main__":
    unittest.main()
