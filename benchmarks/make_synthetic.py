"""Write synthetic stand-in references (procedural characters on flat backgrounds) for dry runs."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pixelagent import synth  # noqa: E402

BACKGROUNDS = [(90, 140, 200), (230, 230, 225), (60, 70, 60)]

if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "refs"), exist_ok=True)
    for i, bg in enumerate(BACKGROUNDS):
        im, _ = synth.draw_character(seed=i, background=bg)
        p = os.path.join(HERE, "refs", f"synthetic_{i}.png")
        im.save(p)
        print("wrote", p)
