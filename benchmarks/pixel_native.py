"""Read a pixel-art image back to a 1x sprite with the native finish's grid recovery (pixelagent/pixelart.py), e.g.
to check an image model's output or a hand-made enlarged sprite. Free: no model calls.

  python benchmarks/pixel_native.py IMAGE.png OUT.png [--block 8]

The native finish itself (the image model drawing the character as pixel art) runs in the pipeline: [finish]
mode = "native" (docs/FACE_ROOT_CAUSE.md section 7)."""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image  # noqa: E402

from pixelagent.pixelart import sprite_from_pixel_art  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("--block", type=float, default=8, help="the block size the image was asked for")
    a = ap.parse_args()
    sprite, geom = sprite_from_pixel_art(Image.open(a.image), margin=2, expected=a.block)
    sprite.save(a.out)
    bb = sprite.getbbox()
    print(f"{a.out}: {sprite.size}, figure {bb[2] - bb[0]}x{bb[3] - bb[1]} px, block {geom['grid']['p']} px")


if __name__ == "__main__":
    main()
