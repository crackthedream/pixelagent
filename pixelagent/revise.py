"""Revising a finished sprite instead of redrawing it.

A rejection note (or a fix request on a finished character) used to start the whole drawing again: about $0.90,
and good parts could be lost. Now the artist continues from the current drawing with the note, in a short session.
With an area, only that rectangle may change: afterwards every pixel outside it is put back from the version the
revision started from, layer by layer, so the guarantee does not depend on the model following instructions."""


def confine(new, old, box):
    """Keep `new`'s changes only inside box=(x, y, w, h); everything outside is restored from `old` (same layers by
    name; layers the model added stay empty outside the box; layers it removed come back outside the box).
    Works on engine Document objects, first frame (character sprites). Returns the number of pixels restored."""
    x0, y0, bw, bh = box
    x1, y1 = x0 + bw, y0 + bh
    W, H = new.w, new.h
    old_by_name = {l.name: l for l in old.layers if not l.reference}
    restored = 0

    def outside(x, y):
        return not (x0 <= x < x1 and y0 <= y < y1)

    for layer in new.layers:
        if layer.reference:
            continue
        src = old_by_name.get(layer.name)
        cel = layer.cels[0]
        for y in range(H):
            for x in range(W):
                if outside(x, y):
                    i = (y * W + x) * 4
                    want = bytes(src.cels[0][i:i + 4]) if src is not None else b"\x00\x00\x00\x00"
                    if bytes(cel[i:i + 4]) != want:
                        cel[i:i + 4] = want
                        restored += 1
    names = {l.name for l in new.layers}
    for layer in old.layers:  # a layer the model deleted: what it held outside the area comes back
        if layer.reference or layer.name in names:
            continue
        back = layer.clone()
        cel = back.cels[0]
        for y in range(max(0, y0), min(H, y1)):
            for x in range(max(0, x0), min(W, x1)):
                i = (y * W + x) * 4
                cel[i:i + 4] = b"\x00\x00\x00\x00"
        new.layers.append(back)
    return restored


def clamp_box(box, w, h):
    """A user-drawn area clipped to the canvas; None if nothing is left."""
    if not box:
        return None
    x, y, bw, bh = (int(round(float(v))) for v in box)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + bw), min(h, y + bh)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return (x0, y0, x1 - x0, y1 - y0)
