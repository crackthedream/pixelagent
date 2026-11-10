"""Faces at sprite size: which materials belong to the face, measuring a face for the face kit (face_layout), and
resetting a face to the trace (a neutral start for comparing face passes in benchmarks/face_lab.py). Faces
themselves are built with the engine's face kit."""

FACE_WORDS = ("skin", "face", "eye", "cheek", "mouth", "blush", "tongue", "lip", "nose")


def is_face_material(m):
    """'skin', 'eyes_and_mouth', 'face_shadow' yes; 'shield_face', 'clock_face' no (an object's face), no hair."""
    n = m.lower().replace("-", "_").replace(" ", "_")
    return n.split("_")[0].startswith(FACE_WORDS) and "hair" not in n


def reset_face(sprite, trace, labels, width, box, pad=2):
    """The sprite with its face cells put back to the trace (a neutral start for comparing face passes)."""
    out = sprite.convert("RGBA").copy()
    tr = trace.convert("RGBA")
    x, y, w, h = box
    height = len(labels) // width
    for j in range(max(0, y - pad), min(height, y + h + pad)):
        for i in range(max(0, x - pad), min(width, x + w + pad)):
            m = labels[j * width + i]
            if m and is_face_material(m):
                out.putpixel((i, j), tr.getpixel((i, j)))
    return out


# the face kit's open-eye sizes: (width incl. the lash wing, height, default gap) -- keep in step with face_kit.py
KIT_EYES = {"s": (3, 3, 3), "m": (5, 4, 4), "l": (6, 5, 5), "xl": (7, 6, 5), "xxl": (9, 7, 6)}


def _dist(a, b):
    return sum(abs(int(x) - int(y)) for x, y in zip(a[:3], b[:3])) / 765.0


def _skinlike(c, skin, tol):
    """Close to the skin colour AND as warm as skin (red above blue): pale grey or white hair next to a light face
    is close in brightness but not in warmth."""
    return _dist(c, skin) <= tol and (int(c[0]) - int(c[2])) >= 0.5 * (int(skin[0]) - int(skin[2]))


def face_layout(img, box, tol=0.16):
    """Measure the face in `box` (x, y, w, h) of an RGBA sprite and place the face kit's parts the way hand-made
    sprites in this style do (Alice in Cradle): eyes plus gap spanning almost the whole face width, the lash row just
    under the fringe and the eyes low in the face, the mouth about halfway between the eyes and the chin.
    Returns a dict (skin, skin_w, left, right, fringe_y, chin_y, cx, size, gap, eye_y, mouth_y) or None."""
    im = img.convert("RGBA")
    W, H = im.size
    x, y, w, h = box
    px = im.load()
    # skin: the most common opaque colour in the face's central band (below the fringe, above the chin)
    counts = {}
    for j in range(y + h // 3, min(H, y + h - 1)):
        for i in range(max(0, x + w // 4), min(W, x + w - w // 4)):
            c = px[i, j]
            if c[3] >= 200:
                counts[c[:3]] = counts.get(c[:3], 0) + 1
    if not counts:
        return None
    skin = max(counts, key=lambda c: (counts[c], sum(c)))
    runs = {}   # per row: the widest stretch of skin, bridging gaps up to 10 px (eyes up to the kit's 9-px size, mouth)
    for j in range(max(0, y - 2), min(H, y + h + 3)):
        xs = [i for i in range(max(0, x - 3), min(W, x + w + 3)) if px[i, j][3] >= 200 and _skinlike(px[i, j], skin, tol)]
        segs = []
        for i in xs:
            if segs and i - segs[-1][1] <= 10:
                segs[-1][1] = i + 1
            else:
                segs.append([i, i + 1])
        if segs:
            best = max(segs, key=lambda r: r[1] - r[0])
            if best[1] - best[0] >= 2:
                runs[j] = tuple(best)
    if not runs:
        return None
    widest = max(b - a for a, b in runs.values())
    rows = sorted(runs)
    fringe_y = next(j for j in rows if runs[j][1] - runs[j][0] >= 0.6 * widest)
    chin_y = fringe_y   # the face ends where the skin narrows into the neck (or stops)
    for j in rows:
        if j > fringe_y and j - chin_y <= 1 and runs[j][1] - runs[j][0] >= 0.4 * widest:
            chin_y = j
    if chin_y - fringe_y < 8:
        # implausibly short (a turned face whose lower rows narrow fast, a hand across the face): faces in this style
        # are about as tall below the fringe as they are wide, so estimate the chin from the widest row
        chin_y = min(H - 1, fringe_y + round(0.85 * widest))
    band = [runs[j] for j in rows if fringe_y <= j <= chin_y]
    left, right = max(band, key=lambda r: r[1] - r[0])
    skin_w = right - left
    cx = (left + right - 1) // 2
    # the biggest eyes that fit with at most one pixel less than their gap; the outer wings may touch the face's edge
    size = "s"
    for name, (ew, eh, gap) in KIT_EYES.items():
        if 2 * ew + gap - 1 <= skin_w + 1:
            size = name
    ew, eh, gap = KIT_EYES[size]
    gap = max(2, min(gap, skin_w + 1 - 2 * ew))
    # the eyes sit low: the game leaves ~5 px from the eye bottom to the chin on an 18-px face, i.e. ~0.28 of the
    # face width; bigger faces keep that proportion
    below_eyes = max(4, min(9, round(0.28 * skin_w)))
    eye_y = max(fringe_y, chin_y - eh - below_eyes)
    eye_y = min(eye_y, max(fringe_y, chin_y - eh - 3))
    below = chin_y - (eye_y + eh)
    mouth_y = min(eye_y + eh + max(1, round(below * 0.45)), max(eye_y + eh + 1, chin_y - 1))
    return {"skin": "#%02x%02x%02x" % skin, "skin_w": skin_w, "left": left, "right": right - 1, "fringe_y": fringe_y,
            "chin_y": chin_y, "cx": cx, "size": size, "gap": gap, "eye_y": eye_y, "mouth_y": mouth_y}
