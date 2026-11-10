"""Face materials are recognised; resetting a face touches only face cells."""
import unittest

from PIL import Image

from pixelagent.face import face_layout, is_face_material, reset_face

SKIN, EYE = (236, 190, 160), (40, 30, 60)


class FaceTests(unittest.TestCase):
    def test_face_material_names(self):
        for yes in ("skin", "eyes_and_mouth", "face_shadow", "Cheek blush", "mouth-inner"):
            self.assertTrue(is_face_material(yes), yes)
        for no in ("shield_face", "clock_face", "hair", "skin_hair_shadow", "plate_armor"):
            self.assertFalse(is_face_material(no), no)

    def test_reset_face_touches_only_face_cells(self):
        w, h = 6, 4
        labels = ["hair"] * w + ["skin"] * (w * 3)
        trace = Image.new("RGBA", (w, h), (*SKIN, 255))
        sprite = trace.copy()
        sprite.putpixel((2, 2), (*EYE, 255))           # a drawn eye in the face
        sprite.putpixel((1, 0), (255, 0, 0, 255))      # something drawn in the hair
        back = reset_face(sprite, trace, labels, w, (0, 0, w, h), pad=0)
        self.assertEqual(back.getpixel((2, 2)), trace.getpixel((2, 2)))
        self.assertEqual(back.getpixel((1, 0))[:3], (255, 0, 0))

    def test_face_layout_measures_the_face_and_places_the_kit(self):
        HAIR, PALE = (90, 50, 40, 255), (232, 232, 236, 255)
        im = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        for x in range(10, 30):                       # hair cap and fringe down to y=11
            for y in range(4, 12):
                im.putpixel((x, y), HAIR)
        for x in range(12, 28):                       # face: 16 px wide, y 12..24
            for y in range(12, 25):
                im.putpixel((x, y), (*SKIN, 255))
        for x in (15, 16, 23, 24):                    # old eyes inside the face must not split it
            im.putpixel((x, 15), (*EYE, 255))
        for x in range(17, 23):                       # a narrower neck below the chin
            for y in range(25, 30):
                im.putpixel((x, y), (*SKIN, 255))
        for y in range(12, 30):                       # pale hair beside the face is not skin
            im.putpixel((11, y), PALE)
            im.putpixel((28, y), PALE)
        L = face_layout(im, (10, 8, 20, 18))
        self.assertEqual((L["left"], L["right"], L["skin_w"]), (12, 27, 16))
        self.assertEqual((L["fringe_y"], L["chin_y"]), (12, 24))
        self.assertEqual(L["cx"], 19)
        self.assertEqual(L["size"], "l")              # 6 + 5 + 6 = 17 fits a 16-px face (wings touch the edge)
        self.assertGreaterEqual(L["eye_y"], L["fringe_y"])
        self.assertTrue(L["eye_y"] + 5 < L["mouth_y"] < L["chin_y"])


if __name__ == "__main__":
    unittest.main()
