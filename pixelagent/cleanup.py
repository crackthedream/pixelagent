"""Speckle cleanup after the model's drawing.

Measured on ten real characters from anime-style references (benchmark 2026-11-09): the artist cleaned the noise of
prompt-made concepts (isolated pixels 209 -> 94) but not of anime concepts (131 -> 123) and added shade flicker
(neighbour shade changes 0.44 -> 0.54): it chases linework and folds that cannot survive at 96 px. Merging tiny
same-shade clusters into their neighbours within the same material, only between near shades, brought them back to
the prompt-path level (lint 33 -> 45, shade changes 0.54 -> 0.45) without changing the designs.

The face (placed pixel by pixel in the face pass), transparent pixels and anything with real contrast (highlights,
buttons, crease lines: more than max_dl apart in lightness) are never touched. The result is a separate top layer
holding only the changed pixels, so the model's own layers stay as they were."""


def cleanup_overlay(art, w, h, composite, labels, face=None, min_size=3, max_dl=0.13):
    """Returns (overlay RGBA bytes with only the recoloured pixels, number of pixels recoloured)."""
    data = bytearray(composite)
    keyed = []
    fx0 = fy0 = fx1 = fy1 = -1
    if face:
        fx0, fy0 = face[0] - 1, face[1] - 1
        fx1, fy1 = face[0] + face[2] + 1, face[1] + face[3] + 1
    for k, m in enumerate(labels):
        x, y = k % w, k // w
        if data[k * 4 + 3] == 0 or (fx0 <= x < fx1 and fy0 <= y < fy1):
            keyed.append(("_keep", k))  # a label of its own: never merged with anything
        else:
            keyed.append(m or "_none")
    n = art.merge_small_clusters(w, h, data, keyed, min_size=min_size, passes=3, max_dl=max_dl)
    overlay = bytearray(w * h * 4)
    for i in range(0, len(data), 4):
        if data[i:i + 4] != composite[i:i + 4]:
            overlay[i:i + 4] = data[i:i + 4]
    return bytes(overlay), n
