"""Speckle cleanup after the drawing: what it may and may not change."""
import unittest

from pixelagent.cleanup import cleanup_overlay
from pixelagent.config import load_config, load_engine

load_engine(load_config())
import art  # noqa: E402

BASE, NEAR, BRIGHT = (90, 60, 140, 255), (98, 66, 150, 255), (250, 240, 255, 255)


def canvas(w, h, fill):
    return bytearray(bytes(fill) * (w * h))


def put(d, w, x, y, c):
    i = (y * w + x) * 4
    d[i:i + 4] = bytes(c)


def get(d, w, x, y):
    i = (y * w + x) * 4
    return tuple(d[i:i + 4])


class CleanupTests(unittest.TestCase):
    def test_specks_merge_details_and_faces_stay(self):
        w = h = 16
        d = canvas(w, h, BASE)
        put(d, w, 3, 3, NEAR)          # a speck one shade off: noise
        put(d, w, 8, 3, BRIGHT)        # a 1-px highlight: deliberate
        put(d, w, 12, 12, NEAR)        # a speck inside the face: placed in the face pass
        put(d, w, 5, 10, (0, 0, 0, 0))  # a hole: transparent stays transparent
        labels = ["coat"] * (w * h)
        overlay, n = cleanup_overlay(art, w, h, bytes(d), labels, face=(11, 11, 3, 3))
        self.assertEqual(n, 1)
        self.assertEqual(get(overlay, w, 3, 3), BASE)                  # the speck took its neighbours' colour
        for x, y in ((8, 3), (12, 12), (5, 10), (0, 0)):               # everything else is not in the overlay
            self.assertEqual(get(overlay, w, x, y)[3], 0, (x, y))

    def test_materials_are_never_mixed(self):
        w = h = 8
        d = canvas(w, h, BASE)
        put(d, w, 4, 4, NEAR)
        labels = ["coat"] * (w * h)
        labels[4 * w + 4] = "skin"     # a one-pixel material of its own is not noise of the coat
        overlay, n = cleanup_overlay(art, w, h, bytes(d), labels)
        self.assertEqual(n, 0)
        self.assertEqual(bytes(overlay), bytes(w * h * 4))


if __name__ == "__main__":
    unittest.main()
