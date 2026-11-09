"""Draw the PixelAgent app icon (PixelAgent.ico): the brand mark from the UI, an indigo rounded square with two
white pixels on a diagonal, rendered pixel-exact at every Windows icon size."""
import os
import sys

from PIL import Image, ImageDraw

ACCENT = (94, 92, 230, 255)   # systemIndigo, the app's accent
SIZES = (16, 24, 32, 48, 64, 128, 256)


def mark(size):
    s = size * 4  # draw large, then downsample once: smooth rounded corners, crisp pixels
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    inset = round(s * 0.04)
    d.rounded_rectangle([inset, inset, s - inset - 1, s - inset - 1], radius=round(s * 0.23), fill=ACCENT)
    cell = s // 4  # the UI mark is an 8x8 grid with 2x2 pixels at (2,2) and (4,4)
    for x, y in ((1, 1), (2, 2)):
        d.rectangle([x * cell, y * cell, (x + 1) * cell - 1, (y + 1) * cell - 1], fill=(255, 255, 255, 255))
    return im.resize((size, size), Image.LANCZOS)


def main(out):
    big = mark(256)
    big.save(out, format="ICO", sizes=[(n, n) for n in SIZES], append_images=[mark(n) for n in SIZES[:-1]])
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "PixelAgent.ico"))
