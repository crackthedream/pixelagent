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

    def test_character_must_be_finished(self):
        self.proj.new_asset("raw", {"prompt": "x"})
        self.proj.new_asset("raw-walk", {"kind": "animation", "character": "raw", "prompt": "walk"})
        with self.assertRaises(ValueError):
            Pipeline(self.proj, "raw-walk").run(auto_approve=True, log=quiet)

    def test_frames_drawn_one_by_one_from_previous_frame(self):
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


if __name__ == "__main__":
    unittest.main()
