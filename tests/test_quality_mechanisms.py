"""Tests for the root-cause fixes: noise metric, amplitude-aware smoothing and cluster merging, range-preserving
levels, colour families with area-weighted steps, and the fidelity guard on region rewrites."""
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from pixelagent import regions  # noqa: E402
from pixelagent.config import load_config, load_engine  # noqa: E402
from pixelagent.schemas import Pick, RegionEdit, RegionVariants  # noqa: E402
from pixelagent.steps.materials import build_palette  # noqa: E402

load_engine(load_config())
import art  # noqa: E402


def img(w, h, fn):
    d = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            c = fn(x, y)
            if c:
                d[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes(c) + b"\xff"
    return d


def pixels(d):
    return [tuple(d[i:i + 4]) for i in range(0, len(d), 4)]


class NoiseAndClusters(unittest.TestCase):
    def test_shade_noise_separates_checker_from_clusters(self):
        a, b = (100, 60, 40), (120, 75, 50)
        checker = img(12, 12, lambda x, y: a if (x + y) % 2 else b)
        blocks = img(12, 12, lambda x, y: a if x < 6 else b)
        n1, n2 = art.shade_noise(12, 12, pixels(checker)), art.shade_noise(12, 12, pixels(blocks))
        self.assertGreater(n1["shade_changes"], 0.9)
        self.assertLess(n2["shade_changes"], 0.1)
        self.assertEqual(n2["singletons"], 0)
        self.assertLess(art.lint(12, 12, checker)["score"], art.lint(12, 12, blocks)["score"])

    def test_speckle_counts_isolated_dots_not_diagonal_lines(self):
        a, dot = (100, 60, 40), (160, 110, 80)
        dots = img(12, 12, lambda x, y: dot if (x % 4, y % 4) == (1, 1) else a)
        diag = img(12, 12, lambda x, y: dot if x == y else a)   # a 1-px diagonal line (like an outline step)
        self.assertEqual(art.shade_noise(12, 12, pixels(dots))["singletons"], 9)
        self.assertEqual(art.shade_noise(12, 12, pixels(diag))["singletons"], 0)

    def test_outline_runs_merge_along_diagonals(self):
        w = h = 8
        dark, darker = (40, 30, 50), (25, 20, 35)
        # a diagonal outline whose colour flickers every other pixel
        d = img(w, h, lambda x, y: (dark if x % 2 else darker) if x == y else None)
        ring = ["ring" if x == y else None for y in range(h) for x in range(w)]
        art.merge_small_clusters(w, h, d, ring, min_size=3, max_dl=0.25, connectivity=8)
        cols = {tuple(d[(i * w + i) * 4:(i * w + i) * 4 + 3]) for i in range(w)}
        self.assertEqual(len(cols), 1)

    def test_smoothing_removes_low_amplitude_texture_but_keeps_highlight_lines(self):
        w = h = 9
        base, tex, hi = (110, 110, 115), (122, 122, 127), (235, 235, 240)

        def fn(x, y):
            if x == 4:
                return hi                       # thin bright highlight line: structure
            return tex if (x * 7 + y * 3) % 5 == 0 else base   # low-amplitude texture: noise
        d = img(w, h, fn)
        labels = ["metal"] * (w * h)
        L = art.smooth_lightness(w, h, d, labels)
        Lbase, Lhi = art.rgb_to_lab(base)[0], art.rgb_to_lab(hi)[0]
        for y in range(1, h - 1):
            self.assertAlmostEqual(L[y * w + 4], Lhi, places=3)              # highlight line survives
            for x in (1, 2, 6, 7):
                self.assertAlmostEqual(L[y * w + x], Lbase, delta=0.02)      # texture flattened

    def test_merge_small_clusters_keeps_glints(self):
        w = h = 7
        mid, near, glint = (100, 80, 60), (112, 92, 70), (250, 240, 220)
        d = img(w, h, lambda x, y: near if (x, y) == (2, 2) else glint if (x, y) == (4, 4) else mid)
        labels = ["cloak"] * (w * h)
        art.merge_small_clusters(w, h, d, labels, min_size=3)
        self.assertEqual(tuple(d[(2 * w + 2) * 4:(2 * w + 2) * 4 + 3]), mid)     # adjacent-step flicker merged
        self.assertEqual(tuple(d[(4 * w + 4) * 4:(4 * w + 4) * 4 + 3]), glint)   # deliberate glint kept

    def test_merge_respects_material_borders(self):
        d = img(4, 1, lambda x, y: (100, 0, 0) if x == 1 else (110, 10, 10))
        labels = ["a", "b", "a", "a"]
        art.merge_small_clusters(4, 1, d, labels, min_size=3)
        self.assertEqual(tuple(d[4:7]), (100, 0, 0))   # pixel 1 is its own material: never recoloured


class Levels(unittest.TestCase):
    def test_levels_keep_extremes(self):
        vals = [0.5] * 400 + [0.52] * 300 + [0.9] * 15 + [0.15] * 15   # few highlight/shadow pixels
        lv = art.lightness_levels(vals, 4)
        self.assertAlmostEqual(lv[0], 0.15, places=2)
        self.assertAlmostEqual(lv[-1], 0.9, places=2)

    def test_ramp_for_levels_hits_levels_and_hue_shifts(self):
        levels = [0.3, 0.5, 0.7, 0.85]
        ramp = art.ramp_for_levels((150, 60, 50), levels, hue_shift=24)
        got = [art.rgb_to_lab(c)[0] for c in ramp]
        for a, b in zip(got, levels):
            self.assertAlmostEqual(a, b, delta=0.02)
        hues = [art.rgb_to_lch(c)[2] for c in ramp]
        self.assertNotAlmostEqual(hues[0], hues[-1], delta=5)


class Families(unittest.TestCase):
    def _px(self, colors_by_mat):
        px, px_of = [], {}
        for m, (c, n) in colors_by_mat.items():
            for _ in range(n):
                px_of.setdefault(m, []).append(len(px))
                px.append(tuple(c) + (255,))
        return px, px_of

    def test_dark_blue_and_dark_brown_do_not_share(self):
        px, px_of = self._px({"robe": ((35, 40, 90), 200), "boots": ((70, 45, 30), 80)})
        fam, ramps = build_palette(art, px, px_of, (set(), ()), budget=31)
        self.assertEqual(len(fam), 2)

    def test_greys_share_and_focal_stays_alone(self):
        px, px_of = self._px({"armor": ((150, 150, 155), 300), "sword": ((190, 190, 195), 40),
                              "helmet": ((120, 120, 125), 60), "skin": ((230, 190, 160), 30),
                              "leather": ((200, 160, 120), 50)})
        fam, ramps = build_palette(art, px, px_of, (set(), ("skin",)), budget=31)
        fam_of = {m: f for f, ms in fam.items() for m in ms}
        self.assertEqual(fam_of["armor"], fam_of["sword"])
        self.assertEqual(fam_of["armor"], fam_of["helmet"])
        self.assertEqual(fam["skin"], ["skin"])

    def test_steps_follow_area_and_fit_budget(self):
        import random
        rnd = random.Random(1)
        px, px_of = [], {}
        for m, hue, n in (("cloak", (90, 60, 40), 600), ("gem", (40, 90, 200), 12), ("trim", (200, 170, 60), 60)):
            for _ in range(n):
                k = rnd.uniform(0.5, 1.4)
                px_of.setdefault(m, []).append(len(px))
                px.append(tuple(min(255, int(v * k)) for v in hue) + (255,))
        fam, ramps = build_palette(art, px, px_of, (set(), ()), budget=12)
        steps = {f: len(r["ramp"]) for f, r in ramps.items()}
        self.assertLessEqual(sum(steps.values()), 12)
        self.assertGreater(steps["cloak"], steps["gem"])


class FidelityMeasure(unittest.TestCase):
    def test_blurred_fidelity_tolerates_crisp_detail_but_not_colour_loss(self):
        w = h = 10
        skin, mid, dark, red = (230, 190, 160), (190, 160, 135), (60, 40, 40), (200, 30, 30)
        # reference: a blurry downscale where the eye is a soft mid-tone smudge
        ref = img(w, h, lambda x, y: mid if (x, y) in ((4, 4), (5, 4)) else skin)
        crisp = img(w, h, lambda x, y: dark if (x, y) == (4, 4) else skin)       # crisp 1-px eye mark
        reg = (2, 2, 6, 6)
        base = art.fidelity(w, h, ref, ref, reg)["delta_e"]
        self.assertLess(art.fidelity(w, h, crisp, ref, reg)["delta_e"] - base, 0.05)
        ref2 = img(w, h, lambda x, y: red if 3 <= x <= 6 and 3 <= y <= 6 else (140, 140, 140))
        grey = img(w, h, lambda x, y: (140, 140, 140))
        self.assertGreater(art.fidelity(w, h, grey, ref2, reg)["delta_e"], 0.05)

    def test_pale_wood_does_not_join_grey(self):
        px, px_of = [], {}
        for m, c, n in (("bowstring", (200, 200, 200), 40), ("bow_wood", (196, 170, 135), 80)):
            for _ in range(n):
                px_of.setdefault(m, []).append(len(px))
                px.append(c + (255,))
        fam, _ = build_palette(art, px, px_of, (set(), ()), budget=31)
        self.assertEqual(len(fam), 2)

    def test_off_by_one_rows_are_repaired(self):
        palette = {"a0": (10, 10, 10, 255)}
        rgb_of, char_of, _ = regions.legend(palette)
        data = img(4, 2, lambda x, y: (10, 10, 10))
        out = regions.apply_rows(data, 4, 2, (0, 0, 4, 2), [char_of[(10, 10, 10)] * 3, char_of[(10, 10, 10)] * 5], rgb_of)
        self.assertEqual(len(out), len(data))
        with self.assertRaises(ValueError):
            regions.apply_rows(data, 4, 2, (0, 0, 4, 2), ["0" * 6, "0" * 4], rgb_of)


class FidelityGuard(unittest.TestCase):
    def test_variant_that_deletes_concept_colour_is_rejected(self):
        w = h = 10
        red, grey = (200, 30, 30), (140, 140, 140)
        ref = img(w, h, lambda x, y: red if 3 <= x <= 6 and 3 <= y <= 6 else grey)
        data = bytearray(ref)
        palette = {"red0": red + (255,), "grey0": grey + (255,)}
        rgb_of, char_of, _ = regions.legend(palette)
        region = (2, 2, 6, 6)
        cur = [r.split(" ", 1)[1] for r in regions.region_grid(data, w, h, region, char_of).splitlines()[2:]]
        all_grey = [char_of[grey] * 6] * 6
        tweak = list(cur)
        tweak[0] = char_of[red] + tweak[0][1:]   # small change that keeps the emblem

        class B:
            class llm:
                @staticmethod
                def structured(role, instr, text, images, schema, stub):
                    if schema is RegionVariants:
                        return RegionVariants(variants=[RegionEdit(rows=all_grey), RegionEdit(rows=tweak)])
                    return Pick(index=1)   # the picker would take whatever is #1

        class Ctx:
            backend = B
            art = art
            out = tempfile.mkdtemp()

            def path(self, n):
                return os.path.join(self.out, n)

            def spec(self):
                from pixelagent.schemas import CharacterSpec
                return CharacterSpec(name="t")

        new, info = regions.rewrite_region(Ctx(), data, w, h, palette, region, "shield", "fix", n=2, ref=ref)
        self.assertEqual(len(info["rejected_by_fidelity"]), 1)     # the all-grey variant never reached the picker
        self.assertEqual(info["picked"], 1)
        self.assertEqual(tuple(new[(4 * w + 4) * 4:(4 * w + 4) * 4 + 3]), red)   # emblem still there


if __name__ == "__main__":
    unittest.main()
