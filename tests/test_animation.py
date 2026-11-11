"""M3 animation: dry-run flow with gates, and the per-frame drawing loop driven by a fake model over real MCP."""
import base64
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from PIL import Image  # noqa: E402

from pixelagent import imaging, synth  # noqa: E402
from pixelagent.pipeline import Pipeline  # noqa: E402
from pixelagent.schemas import AnimationSpec, FramePlan  # noqa: E402
from pixelagent.store import Project  # noqa: E402


def quiet(*_a, **_k):
    pass


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class AnimationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pxa_anim_")
        self.root = os.path.join(self.tmp, "项目 anim")
        self.proj = Project(self.root, {"backend": {"mode": "dry_run"}})
        im, _ = synth.draw_character(2, size=384, background=(200, 200, 204))
        ref = os.path.join(self.tmp, "ref.png")
        im.save(ref)
        self.proj.new_asset("hero", {"references": [ref], "height": 48})
        Pipeline(self.proj, "hero").run(auto_approve=True, log=quiet)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _anim(self, aid, prompt, frames=None):
        self.proj.new_asset(aid, {"kind": "animation", "character": "hero", "anim": aid, "prompt": prompt,
                                  "frames": frames})
        return Pipeline(self.proj, aid)

    def test_dry_run_with_gates_and_exports(self):
        pipe = self._anim("hero-walk", "walk cycle", 6)
        self.assertEqual(pipe.run(log=quiet), "awaiting_gate")
        self.assertEqual(pipe.pending_gate().gate, "P")
        pipe.decide(True)
        self.assertEqual(pipe.run(log=quiet), "awaiting_gate")
        self.assertEqual(pipe.pending_gate().gate, "R")
        pipe.decide(True)
        self.assertEqual(pipe.run(log=quiet), "complete")
        a = self.proj.asset("hero-walk")
        meta = json.loads(rd(a.output("anim_export", "meta")))
        self.assertEqual(len(meta["frames"]), 6)
        self.assertTrue(meta["loop"])
        gif = Image.open(a.output("anim_export", "gif"))
        self.assertEqual(gif.n_frames, 6)
        sheet = imaging.load_rgba(a.output("anim_export", "sheet"))
        self.assertEqual(sheet.width, meta["size"][0] * 6)
        self.assertEqual(len(os.listdir(a.output("anim_export", "frames"))), 6)
        info = a.state.steps["anim_traces"].info
        self.assertEqual(len(info["lifts"]), 6)

    def test_reject_pose_sheet_regenerates_with_note(self):
        pipe = self._anim("hero-idle", "idle breathing", 4)
        pipe.run(log=quiet)
        pipe.decide(False, note="arms closer to the body")
        pipe.run(log=quiet)
        a = self.proj.asset("hero-idle")
        self.assertEqual(a.state.steps["anim_poses"].version, 2)
        prompt = rd(os.path.join(a.dir, a.state.steps["anim_poses"].info["dir"], "pose_prompt.txt"))
        self.assertIn("arms closer to the body", prompt)

    def test_reject_final_redraws_with_note(self):
        self.proj.config["finish"]["mode"] = "artist"   # the model-drawn animation path
        pipe = self._anim("hero-jump", "jump", 3)
        pipe.run(auto_approve=False, log=quiet)
        pipe.decide(True)                                    # pose sheet
        pipe.run(log=quiet)
        self.assertEqual(pipe.pending_gate().gate, "R")
        pipe.decide(False, note="keep the cap still")
        a = self.proj.asset("hero-jump")
        self.assertEqual(a.state.steps["anim_draw"].status, "pending")
        self.assertEqual(a.state.inputs["notes"]["anim_draw"], ["keep the cap still"])
        Pipeline(self.proj, "hero-jump").run(log=quiet)
        a = self.proj.asset("hero-jump")
        self.assertEqual(a.state.steps["anim_draw"].version, 2)
        self.assertEqual(a.state.steps["anim_poses"].version, 1)    # the approved pose sheet is kept

    def test_reduce_frames_are_the_pose_sheet_reductions(self):
        # the reduce finish (default): no drawing; each frame is its pose-sheet figure reduced onto the character's
        # grid and palette, with no outline ring
        self.proj.config["finish"]["mode"] = "reduce"
        pipe = self._anim("hero-wave", "wave", 4)
        self.assertEqual(pipe.run(auto_approve=True, log=quiet), "complete")
        a = self.proj.asset("hero-wave")
        info = a.state.steps["anim_draw"].info
        self.assertIn("reduce", info["reason"])
        prompt = rd(os.path.join(a.dir, a.state.steps["anim_poses"].info["dir"], "pose_prompt.txt"))
        self.assertIn("NOT pixel art", prompt)
        oc = self.proj.config["style"]["outline_color"]
        oc = tuple(int(oc[i:i + 2], 16) for i in (1, 3, 5))
        for i in range(4):
            frame = imaging.to_bytes(imaging.load_rgba(a.output("anim_draw", f"frame{i}")))
            trace = imaging.to_bytes(imaging.load_rgba(a.output("anim_traces", f"trace{i}")))
            self.assertEqual(frame, trace, i)
            self.assertNotIn(oc, {tuple(frame[k:k + 3]) for k in range(0, len(frame), 4) if frame[k + 3]})

    def test_native_frames_are_the_pixel_art_sheet_read_back(self):
        # the native finish (default): the sheet is drawn as pixel art on one block grid; every frame is its figure
        # read back pixel for pixel (the stand-in sheet is the character's own sprite as blocks, so every frame
        # must equal that sprite exactly)
        self.assertEqual(self.proj.config["finish"]["mode"], "native")
        pipe = self._anim("hero-bob", "bob", 4)
        self.assertEqual(pipe.run(auto_approve=True, log=quiet), "complete")
        a = self.proj.asset("hero-bob")
        block = a.state.steps["anim_poses"].info["block"]
        self.assertGreaterEqual(block, 3)
        prompt = rd(os.path.join(a.dir, a.state.steps["anim_poses"].info["dir"], "pose_prompt.txt"))
        self.assertIn("hand-pixelled", prompt)
        self.assertIn(f"{block}x{block}", prompt)
        sprite = imaging.load_rgba(self.proj.asset("hero").output("export", "png"))
        sprite = sprite.crop(sprite.getbbox())
        want = imaging.to_bytes(sprite)
        for i in range(4):
            f = imaging.load_rgba(a.output("anim_draw", f"frame{i}"))
            f = f.crop(f.getbbox())
            self.assertEqual(f.size, sprite.size, i)
            got = imaging.to_bytes(f)
            self.assertLessEqual(max(abs(got[k] - want[k]) for k in range(len(got)) if k % 4 != 3 and got[k - k % 4 + 3]), 3, i)

    def test_frames_drawn_too_big_are_read_back_at_the_sprites_height(self):
        from PIL import Image
        from pixelagent.steps.anim import read_pixel_frames
        sprite = imaging.load_rgba(self.proj.asset("hero").output("export", "png"))
        sprite = sprite.crop(sprite.getbbox())
        block, n = 6, 4
        for scale in (1.0, 1.12, 0.9):                     # the image model drew the walk this much bigger/smaller
            big = sprite.resize((round(sprite.width * scale), round(sprite.height * scale)), Image.NEAREST)
            big = big.resize((big.width * block, big.height * block), Image.NEAREST)
            sheet = Image.new("RGBA", (n * (big.width + 60), big.height + 80), (222, 222, 226, 255))
            boxes = []
            for i in range(n):
                x = 30 + i * (big.width + 60)
                sheet.alpha_composite(big, (x, 40))
                boxes.append((x, 40, x + big.width, 40 + big.height))
            frames, s, info = read_pixel_frames(sheet, boxes, block, sprite.height)
            for f in frames:
                self.assertLessEqual(abs(f.height - sprite.height), 1, (scale, f.size))
            self.assertEqual(info["resized"], scale != 1.0, scale)
            if scale == 1.0:                               # within tolerance: exactly as drawn
                import numpy as np
                f0, sp0 = np.asarray(frames[0]).copy(), np.asarray(sprite).copy()
                f0[f0[..., 3] == 0] = 0                   # transparent pixels: RGB does not matter
                sp0[sp0[..., 3] == 0] = 0
                self.assertTrue(np.array_equal(f0, sp0))

    def test_reduce_review_note_goes_to_the_pose_sheet(self):
        pipe = self._anim("hero-hop", "hop", 3)
        self.assertEqual(pipe.run(log=quiet), "awaiting_gate")                    # gate P
        pipe.decide(True)
        self.assertEqual(Pipeline(self.proj, "hero-hop").run(log=quiet), "awaiting_gate")
        pipe = Pipeline(self.proj, "hero-hop")
        self.assertEqual(pipe.pending_gate().gate, "R")
        pipe.decide(False, note="smaller jump")
        # the frames come from the sheet, so the note redoes the sheet, which waits for approval again
        self.assertEqual(Pipeline(self.proj, "hero-hop").run(log=quiet), "awaiting_gate")
        pipe = Pipeline(self.proj, "hero-hop")
        self.assertEqual(pipe.pending_gate().gate, "P")
        a = self.proj.asset("hero-hop")
        self.assertEqual(a.state.steps["anim_poses"].version, 2)
        prompt = rd(os.path.join(a.dir, a.state.steps["anim_poses"].info["dir"], "pose_prompt.txt"))
        self.assertIn("smaller jump", prompt)
        pipe.decide(True)
        self.assertEqual(Pipeline(self.proj, "hero-hop").run(log=quiet), "awaiting_gate")   # the note is used once
        self.assertEqual(Pipeline(self.proj, "hero-hop").pending_gate().gate, "R")
        self.assertEqual(self.proj.asset("hero-hop").state.steps["anim_poses"].version, 2)

    def test_character_must_be_finished(self):
        self.proj.new_asset("raw", {"prompt": "x"})
        self.proj.new_asset("raw-walk", {"kind": "animation", "character": "raw", "prompt": "walk"})
        with self.assertRaises(ValueError):
            Pipeline(self.proj, "raw-walk").run(auto_approve=True, log=quiet)

    def test_frames_drawn_one_by_one_from_previous_frame(self):
        self.proj.config["finish"]["mode"] = "artist"   # the model-drawn animation path
        n = 3

        def sheet_b64():
            fig, _ = synth.draw_character(2, size=300)
            fig = fig.crop(imaging.content_bbox(fig))
            s = Image.new("RGB", (900, 400), (222, 222, 226))
            for i in range(n):
                s.paste(fig.convert("RGB"), (40 + i * 300, 380 - fig.height), fig)
            buf = io.BytesIO()
            s.save(buf, "PNG")
            return base64.b64encode(buf.getvalue()).decode()

        creates = []

        def create(**kw):
            creates.append(kw)
            frame = int(kw["instructions"].split("frame ")[1].split(" of")[0]) - 1
            first_of_frame = "previous_response_id" not in kw
            if first_of_frame:   # mark one pixel in this frame, then finish
                out = [NS(type="function_call", name="draw", call_id=f"d{frame}", arguments=json.dumps(
                    {"frame": frame, "ops": [{"op": "pixels", "points": [[1 + frame, 1]], "color": "#ff00ff"}]}))]
            else:
                out = [NS(type="function_call", name="finish", call_id=f"f{frame}", arguments='{"summary": "ok"}')]
            return NS(id=f"r{len(creates)}", output=out, usage=NS(input_tokens=100, output_tokens=10,
                                                                  input_tokens_details=NS(cached_tokens=0)))

        def parse(**kw):
            out = AnimationSpec(name="walk", summary="walk", frames=[FramePlan(index=i, pose=f"p{i}", duration_ms=100 + i)
                                                                      for i in range(n)])
            return NS(output_parsed=out, usage=NS(input_tokens=10, output_tokens=10))

        def edit(**kw):
            return NS(data=[NS(b64_json=sheet_b64())], usage=NS(input_tokens=10, output_tokens=10,
                                                                 input_tokens_details=NS(image_tokens=0, text_tokens=10)))

        client = NS(responses=NS(create=create, parse=parse), images=NS(edit=edit, generate=edit))
        self.proj.new_asset("hero-run", {"kind": "animation", "character": "hero", "anim": "run", "prompt": "run"})
        self.proj.config["backend"]["mode"] = "openai"
        os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")
        status = Pipeline(self.proj, "hero-run", mode="openai", client=client).run(auto_approve=True, log=quiet)
        self.assertEqual(status, "complete")
        # one fresh conversation per frame (no previous_response_id at a frame's first request)
        firsts = [kw for kw in creates if "previous_response_id" not in kw]
        self.assertEqual(len(firsts), n)
        tools = {t["name"] for t in firsts[0]["tools"]}
        self.assertNotIn("palette", tools)
        self.assertNotIn("layer", tools)
        a = self.proj.asset("hero-run")
        meta = json.loads(rd(a.output("anim_export", "meta")))
        self.assertEqual([f["duration_ms"] for f in meta["frames"]], [100, 101, 102])
        sheet = imaging.load_rgba(a.output("anim_export", "sheet"))
        fw = meta["size"][0]
        d = imaging.to_bytes(sheet)

        def magenta(frame, x):
            i = (1 * sheet.width + frame * fw + x) * 4
            return tuple(d[i:i + 3]) == (255, 0, 255)
        # frame k carries its own mark AND the marks of earlier frames (it started as a copy of the previous one)
        self.assertTrue(magenta(0, 1))
        self.assertTrue(magenta(1, 1) and magenta(1, 2))
        self.assertTrue(magenta(2, 1) and magenta(2, 2) and magenta(2, 3))
        self.assertFalse(magenta(0, 2))

    def test_a_limit_stops_the_frames_honestly_and_resumes(self):
        self.proj.config["finish"]["mode"] = "artist"   # the model-drawn animation path
        # review finding F06: a spending stop used to copy frames forward and still reach the review as complete
        n = 3

        def sheet_b64():
            fig, _ = synth.draw_character(2, size=300)
            fig = fig.crop(imaging.content_bbox(fig))
            s = Image.new("RGB", (900, 400), (222, 222, 226))
            for i in range(n):
                s.paste(fig.convert("RGB"), (40 + i * 300, 380 - fig.height), fig)
            buf = io.BytesIO()
            s.save(buf, "PNG")
            return base64.b64encode(buf.getvalue()).decode()

        firsts = []
        state = {"spend_at_frame": 1}

        def create(**kw):
            frame = int(kw["instructions"].split("frame ")[1].split(" of")[0]) - 1
            if "previous_response_id" not in kw:
                firsts.append(frame)
                if frame == state["spend_at_frame"]:  # this frame's first call uses up the per-item limit
                    self.proj.log_cost({"asset": "hero-sprint", "kind": "llm", "role": "x", "model": "x", "usd": 50})
                out = [NS(type="function_call", name="draw", call_id=f"d{frame}", arguments=json.dumps(
                    {"frame": frame, "ops": [{"op": "pixels", "points": [[1 + frame, 1]], "color": "#ff00ff"}]}))]
            else:
                out = [NS(type="function_call", name="finish", call_id=f"f{frame}", arguments='{"summary": "ok"}')]
            return NS(id=f"r{len(firsts)}-{frame}", output=out, usage=NS(input_tokens=100, output_tokens=10,
                                                                          input_tokens_details=NS(cached_tokens=0)))

        def parse(**kw):
            out = AnimationSpec(name="sprint", summary="sprint", loop=False,
                                frames=[FramePlan(index=i, pose=f"p{i}", duration_ms=100) for i in range(n)])
            return NS(output_parsed=out, usage=NS(input_tokens=10, output_tokens=10))

        def edit(**kw):
            return NS(data=[NS(b64_json=sheet_b64())], usage=NS(input_tokens=10, output_tokens=10,
                                                                 input_tokens_details=NS(image_tokens=0, text_tokens=10)))

        client = NS(responses=NS(create=create, parse=parse), images=NS(edit=edit, generate=edit))
        self.proj.new_asset("hero-sprint", {"kind": "animation", "character": "hero", "anim": "sprint", "prompt": "sprint"})
        self.proj.config["backend"]["mode"] = "openai"
        os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")
        from pixelagent.models import BudgetExceeded
        with self.assertRaises(BudgetExceeded) as cm:
            Pipeline(self.proj, "hero-sprint", mode="openai", client=client).run(auto_approve=True, log=quiet)
        self.assertIn("frame 2 of 3", str(cm.exception))
        a = self.proj.asset("hero-sprint")
        self.assertEqual(a.state.steps["anim_draw"].status, "failed")
        review = a.state.steps.get("anim_review")
        self.assertTrue(review is None or review.status != "awaiting_gate")           # never offered for review
        # raise the limit and try again: frame 1 is kept, drawing resumes at frame 2
        self.proj.config["budget"]["max_usd_per_asset"] = 1000.0
        state["spend_at_frame"] = None
        firsts.clear()
        self.assertEqual(Pipeline(self.proj, "hero-sprint", mode="openai", client=client).run(auto_approve=True, log=quiet),
                         "complete")
        self.assertEqual(firsts, [1, 2])                           # frame 1 was not drawn again
        a = self.proj.asset("hero-sprint")
        self.assertFalse(os.path.exists(os.path.join(a.dir, "checkpoints", "anim_draw")))
        # review finding F11: a non-looping animation plays once in every GIF (no NETSCAPE loop block)
        for key in ("gif", "gif_x4"):
            with open(a.output("anim_export", key), "rb") as f:
                self.assertNotIn(b"NETSCAPE2.0", f.read(), key)
        with open(a.output("anim_draw", "gif"), "rb") as f:
            self.assertNotIn(b"NETSCAPE2.0", f.read())
        self.assertFalse(json.loads(rd(a.output("anim_export", "meta")))["loop"])


if __name__ == "__main__":
    unittest.main()
