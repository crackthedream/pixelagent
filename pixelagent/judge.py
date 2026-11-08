"""Pairwise judging: is version B better than version A in one region (e.g. the face)?

Absolute critic scores are too noisy (about +-0.4) for subtle changes. A pairwise comparison is easier for a
model, and asking twice with the order swapped cancels position bias: a verdict counts only if it holds in both
orders, otherwise it is a tie."""
import os

from PIL import Image

from . import imaging
from .schemas import Pick

PAIR_PROMPT = """Two pixel-art renderings of the same character's {what} are shown side by side (left = 0, right = 1),
followed by a close-up of the concept art. Which pixel rendering better conveys the concept ({focus}) while
reading clearly as pixel art at small size? Answer index 0 (left) or 1 (right), and a one-sentence reason."""


def _pair_image(a, b, region, concept_crop, out, zoom=14):
    x, y, w, h = region
    tiles = [imaging.zoom(im.crop((x, y, x + w, y + h)), zoom) for im in (a, b)]
    labels = ["0", "1"]
    if concept_crop is not None:
        c = concept_crop.resize((max(1, concept_crop.width * tiles[0].height // concept_crop.height), tiles[0].height))
        tiles.append(c)
        labels.append("concept")
    imaging.contact_sheet(tiles, labels, 1).save(out)
    return out


def compare_pair(llm, a_path, b_path, region, concept_crop, out_dir, what="face",
                 focus="eyes, expression and facial features"):
    """Returns {"winner": "a" | "b" | "tie", "votes": [...], "reasons": [...]}."""
    a, b = imaging.load_rgba(a_path), imaging.load_rgba(b_path)
    pad = 2
    x, y, w, h = region
    region = (max(0, x - pad), max(0, y - pad), min(a.width, x + w + pad) - max(0, x - pad),
              min(a.height, y + h + pad) - max(0, y - pad))
    votes, reasons = [], []
    for order, (left, right) in enumerate(((a, b), (b, a))):
        img = _pair_image(left, right, region, concept_crop, os.path.join(out_dir, f"judge_{what}_{order}.png"))

        def stub():
            return Pick(index=0, reason="dry run")

        pick = llm.structured("final_critic", PAIR_PROMPT.format(what=what, focus=focus), f"comparison {order + 1} of 2",
                              [img], Pick, stub)
        chose_b = (pick.index == 1) if order == 0 else (pick.index == 0)
        votes.append("b" if chose_b else "a")
        reasons.append(pick.reason)
    winner = votes[0] if votes[0] == votes[1] else "tie"
    return {"winner": winner, "votes": votes, "reasons": reasons}
