import unittest

import numpy as np
from PIL import Image

from pixelagent.expressions import _sprite_index, colour_map, face_box
from pixelagent.steps.build import GRADES


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


if __name__ == "__main__":
    unittest.main()
