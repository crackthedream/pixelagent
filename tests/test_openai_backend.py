"""M1 tests: the OpenAI backend driven by a fake client (no network, no cost)."""
import base64
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from PIL import Image  # noqa: E402

from pixelagent import imaging, openai_backend, synth  # noqa: E402
from pixelagent.models import BudgetExceeded  # noqa: E402
from pixelagent.pipeline import Pipeline  # noqa: E402
from pixelagent.schemas import (CharacterSpec, ClusterLabel, Critique, Issue, Material, MaterialLabels,  # noqa: E402
                                MaterialList, Pick, Region, RegionEdit, RegionPlan, RegionVariants, Scores)
from pixelagent.steps import matmap  # noqa: E402
from pixelagent.store import Project  # noqa: E402

FAKE_KEY = "sk-test-FAKEKEY-0123456789"


def _png_b64(seed):
    im, _ = synth.draw_character(seed=seed, size=384, background=(210, 210, 214))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _fake_region_edit(instr, k=0):
    """Return the current region from the prompt's grid with one pixel recoloured (a valid, different edit;
    variant k uses the k-th alternative key so variants differ)."""
    keys = re.findall(r"^(\S) = #[0-9a-f]{6}", instr, re.M)
    lines = instr.split("Current region (x labels on top, y labels on the left):\n", 1)[1].splitlines()[2:]
    rows = [ln.strip().split(" ", 1)[1] for ln in lines if ln.strip()]
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            if ch != ".":
                new = [key for key in keys if key != ch][k % (len(keys) - 1)]
                rows[j] = row[:i] + new + row[i + 1:]
                return RegionEdit(rows=rows, rationale="fake edit")
    return RegionEdit(rows=rows, rationale="nothing to edit")


class FakeResponses:
    def __init__(self, log):
        self.log = log
        # critic answers in order; the last one repeats. Default: pass at once (no repair loop).
        self.critiques = [Critique(scores=Scores(silhouette=8), overall=8.2,
                                   issues=[Issue(area="face", problem="eyes uneven")], verdict="pass")]
        self.n_crit = 0

    def parse(self, **kw):
        self.log.append(("parse", kw))
        fmt = kw["text_format"]
        text = kw["input"][0]["content"][0]["text"]
        if fmt is RegionPlan:
            out = RegionPlan(regions=[Region(name="face", x=8, y=4, w=10, h=8, goal="readable eyes")])
            return NS(output_parsed=out, usage=NS(input_tokens=1000, output_tokens=200))
        if fmt is RegionVariants:
            n = int(re.search(r"Return exactly (\d+) different variants", text).group(1))
            out = RegionVariants(variants=[_fake_region_edit(kw["instructions"], k) for k in range(n)])
            return NS(output_parsed=out, usage=NS(input_tokens=1000, output_tokens=200))
        if fmt is Critique:
            out = self.critiques[min(self.n_crit, len(self.critiques) - 1)]
            self.n_crit += 1
            return NS(output_parsed=out, usage=NS(input_tokens=1000, output_tokens=200))
        if fmt is CharacterSpec:
            out = CharacterSpec(name="hero", summary="test hero", height_px=48,
                                materials=[Material(name="skin", base_color="#eec4a0"), Material(name="hair", base_color="#7a402c"),
                                           Material(name="coat", base_color="#345ca0"), Material(name="boots", base_color="#5a3a28")])
        elif fmt is Pick:
            out = Pick(index=1, reason="fake")
        elif fmt is MaterialLabels:
            ids = [c["id"] for c in json.loads(text)["clusters"]]
            names = ["skin", "hair", "coat", "boots"]
            out = MaterialLabels(labels=[ClusterLabel(cluster=i, material=names[i % 4]) for i in ids])
        elif fmt is MaterialList:
            out = MaterialList(materials=[Material(name=n) for n in ("skin", "hair", "coat", "pants", "boots", "eyes")])
        else:
            raise AssertionError(fmt)
        usage = NS(input_tokens=1000, output_tokens=200, input_tokens_details=None, output_tokens_details=None)
        return NS(output_parsed=out, usage=usage)


class FakeImages:
    def __init__(self, log):
        self.log = log
        self.bad_map = False   # True: answer the material-map request with a blank image (unusable map)

    def _resp(self, n):
        usage = NS(input_tokens=100, output_tokens=4000, input_tokens_details=NS(image_tokens=0, text_tokens=100))
        return NS(data=[NS(b64_json=_png_b64(i)) for i in range(n)], usage=usage)

    def generate(self, **kw):
        self.log.append(("generate", kw))
        return self._resp(kw.get("n", 1))

    def edit(self, **kw):
        self.log.append(("edit", {k: v for k, v in kw.items() if k != "image"} | {"n_images": len(kw["image"])}))
        if self.bad_map is not None and "segmentation map" in kw["prompt"]:
            src = Image.open(kw["image"][0]).convert("RGB")
            keys = re.findall(r"= (#[0-9a-f]{6})", kw["prompt"])
            mp = Image.new("RGB", src.size, (255, 255, 255)) if self.bad_map else matmap.stub_map(src, keys, len(keys))
            buf = io.BytesIO()
            mp.save(buf, "PNG")
            usage = NS(input_tokens=100, output_tokens=4000, input_tokens_details=NS(image_tokens=0, text_tokens=100))
            return NS(data=[NS(b64_json=base64.b64encode(buf.getvalue()).decode())], usage=usage)
        return self._resp(kw.get("n", 1))


class FakeClient:
    def __init__(self):
        self.log = []
        self.responses = FakeResponses(self.log)
        self.images = FakeImages(self.log)


def quiet(*_a, **_k):
    pass


def rd(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class OpenAIBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pxa_oa_")
        self.root = os.path.join(self.tmp, "项目 oa")
        # these tests exercise the conversion + detail/repair path; the artist finish has its own test below
        self.proj = Project(self.root, {"backend": {"mode": "openai"}, "finish": {"mode": "repair"}})
        self.client = FakeClient()
        os.environ["OPENAI_API_KEY"] = FAKE_KEY

    def tearDown(self):
        os.environ.pop("OPENAI_API_KEY", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, asset_id, inputs, **kw):
        self.proj.new_asset(asset_id, inputs)
        return Pipeline(self.proj, asset_id, client=self.client).run(auto_approve=True, log=quiet, **kw)

    def test_prompt_path_end_to_end(self):
        self.assertEqual(self._run("hero", {"prompt": "a hero in a blue coat"}), "complete")
        calls = [c for c in self.client.log]
        gens = [kw for name, kw in calls if name == "generate"]
        self.assertEqual(len(gens), 1)
        g = gens[0]
        self.assertEqual((g["model"], g["n"], g["size"], g["background"], g["output_format"]),
                         ("gpt-image-2.6-flare", 4, "1024x1536", "opaque", "png"))
        self.assertIn("a hero in a blue coat", g["prompt"].lower() + " a hero in a blue coat")
        parses = [kw for name, kw in calls if name == "parse"]
        by_fmt = {}
        for kw in parses:
            by_fmt.setdefault(kw["text_format"].__name__, []).append(kw["model"])
        # the cluster labeller is skipped with a usable map; detail = plan + 3 candidates + 1 region pick
        self.assertEqual(by_fmt, {"CharacterSpec": ["gpt-6.1-sol"], "MaterialList": ["gpt-6.1-sol"],
                                  "Pick": ["gpt-6.1-terra", "gpt-6.1-terra"], "RegionPlan": ["gpt-6.1-sol"],
                                  "RegionVariants": ["gpt-6.1-sol"], "Critique": ["gpt-6.1-terra"]})
        self.assertEqual(parses[0]["reasoning"], {"effort": "medium"})
        pick_imgs = [c for c in parses[2]["input"][0]["content"] if c["type"] == "input_image"]
        self.assertTrue(pick_imgs and pick_imgs[0]["image_url"].startswith("data:image/png;base64,"))
        edit = [kw for kw in parses if kw["text_format"] is RegionVariants][0]
        self.assertIn("Return exactly 3 different variants", edit["input"][0]["content"][0]["text"])
        self.assertEqual(len([c for c in edit["input"][0]["content"] if c["type"] == "input_image"]), 3)  # context, region, concept
        critic_imgs = [c for c in parses[-1]["input"][0]["content"] if c["type"] == "input_image"]
        self.assertEqual(len(critic_imgs), 3)
        edits = [kw for name, kw in calls if name == "edit"]
        self.assertEqual(len(edits), 1)
        self.assertEqual((edits[0]["model"], edits[0]["quality"]), ("gpt-image-2.6-sunburst", "medium"))
        self.assertIn("= #ff0000", edits[0]["prompt"])
        a = self.proj.asset("hero")
        self.assertTrue(a.state.steps["matmap"].info["usable"], a.state.steps["matmap"].info)
        self.assertEqual(a.state.steps["materials"].info["label_source"], "material map")
        self.assertEqual(a.state.steps["pixelize"].info["picked"], 1)          # the model's pick was used
        mats = json.loads(rd(a.output("materials", "materials")))["materials"]
        self.assertTrue({m["name"] for m in mats} <= {"skin", "hair", "coat", "pants", "boots", "eyes", "misc"})
        self.assertGreaterEqual(len(mats), 3)
        crit = json.loads(rd(a.output("critique", "critique")))
        self.assertEqual(crit["verdict"], "pass")
        det = a.state.steps["detail"].info
        self.assertEqual(det["regions"][0]["picked"], 1)          # the region pick (index 1) was applied
        self.assertTrue(det["changed_regions"])
        doc_layers = json.loads(rd(os.path.join(a.path(a.state.steps["export"].outputs["project"]), "project.json")))
        self.assertIn("detail", [l["name"] for l in doc_layers["layers"]])
        # costs: 7 text calls (one call per detail region for all variants) + 2 image calls
        costs = [json.loads(l) for l in rd(os.path.join(self.root, "costs.jsonl")).splitlines()]
        self.assertEqual(len(costs), 9)
        sol = [c for c in costs if c["model"] == "gpt-6.1-sol"][0]
        self.assertAlmostEqual(sol["usd"], (1000 * 2 + 200 * 10) / 1e6, places=6)
        img = [c for c in costs if c["kind"] == "image"][0]
        self.assertAlmostEqual(img["usd"], (100 * 5 + 4000 * 30) / 1e6, places=6)
        self.assertGreater(self.proj.cost_total("hero"), 0)

    def test_reference_path_with_clean_and_refine(self):
        ref = os.path.join(self.tmp, "ref.png")
        synth.draw_character(seed=2, size=384, background=(60, 70, 60))[0].save(ref)
        self.proj.config["concept"] = {"clean_reference": True, "refine_after_pick": True}
        self.assertEqual(self._run("ref1", {"references": [ref], "height": 40}), "complete")
        edits = [kw for name, kw in self.client.log if name == "edit"]
        self.assertEqual(len(edits), 3)                      # redraw candidate + refine after pick + material map
        self.assertTrue(all(e["model"] == "gpt-image-2.6-sunburst" and "input_fidelity" not in e for e in edits))
        info = self.proj.asset("ref1").state.steps["concept"].info
        self.assertEqual(info["sources"], ["reference", "reference redrawn"])
        self.assertTrue(info.get("refined"))
        intake = [kw for name, kw in self.client.log if name == "parse"][0]
        self.assertTrue(any(c["type"] == "input_image" for c in intake["input"][0]["content"]))

    def _repair_case(self, second_overall):
        bad = Critique(overall=6.0, verdict="repair", issues=[
            Issue(area="face", region=[8, 4, 10, 8], severity="high", problem="eyes unreadable", fix="add eye marks")])
        self.client.responses.critiques = [bad, Critique(overall=second_overall, verdict="pass" if second_overall >= 8 else "repair",
                                                         issues=bad.issues)]
        self.assertEqual(self._run("hero", {"prompt": "hero"}), "complete")
        a = self.proj.asset("hero")
        return a, json.loads(rd(a.output("critique", "history")))

    def test_repair_round_kept_when_better(self):
        a, hist = self._repair_case(8.5)
        self.assertEqual([h["round"] for h in hist], [0, 1])
        self.assertTrue(hist[1]["kept"])
        self.assertEqual(json.loads(rd(a.output("critique", "critique")))["overall"], 8.5)
        layers = json.loads(rd(os.path.join(a.output("critique", "project"), "project.json")))["layers"]
        self.assertIn("repair", [l["name"] for l in layers])

    def test_repair_round_within_noise_not_kept(self):
        a, hist = self._repair_case(6.1)                    # +0.1 is inside the critic's noise (min_gain 0.25)
        self.assertFalse(hist[1]["kept"])
        self.assertEqual(json.loads(rd(a.output("critique", "critique")))["overall"], 6.0)

    def test_repair_round_reverted_when_worse(self):
        a, hist = self._repair_case(5.0)
        self.assertFalse(hist[1]["kept"])
        self.assertEqual(json.loads(rd(a.output("critique", "critique")))["overall"], 6.0)  # best = round 0
        from pixelagent.config import load_engine
        load_engine(self.proj.config)
        import project as projectio
        doc = projectio.load_project(a.output("critique", "project"))
        from pixelagent import imaging
        self.assertEqual(bytes(doc.composite()), imaging.to_bytes(imaging.load_rgba(a.output("critique", "final_1x"))))

    def test_artist_finish_mode(self):
        self.proj.config["finish"] = {"mode": "artist", "max_usd": 1.5, "max_turns": 5, "face_turns": 3, "face_pass": True}
        script = [[NS(type="function_call", name="draw", call_id="a1", arguments=json.dumps(
                      {"ops": [{"op": "rect", "x": 6, "y": 6, "w": 12, "h": 30, "color": "#3050a0", "fill": True}]}))],
                  [NS(type="function_call", name="finish", call_id="a2", arguments='{"summary": "drawn"}')]]
        calls = []

        def create(**kw):
            calls.append(kw)
            out = script[min(len(calls) - 1, len(script) - 1)]
            return NS(id=f"r{len(calls)}", output=out, usage=NS(input_tokens=500, output_tokens=50,
                                                                input_tokens_details=NS(cached_tokens=0)))
        self.client.responses.create = create
        self.assertEqual(self._run("hero", {"prompt": "hero"}), "complete")
        a = self.proj.asset("hero")
        st = a.state.steps
        self.assertEqual(st["detail"].status, "skipped")
        self.assertEqual(st["artist"].status, "done")
        self.assertIn("draw: finished", st["artist"].info["stop"])
        self.assertEqual(st["critique"].info["rounds"], 0)                    # no automatic repair over the drawing
        self.assertIn("pre-pixelisation", calls[0]["instructions"])
        png = imaging.load_rgba(a.output("export", "png"))
        d = imaging.to_bytes(png)
        cols = {tuple(d[i:i + 3]) for i in range(0, len(d), 4) if d[i + 3]}
        self.assertEqual(cols, {(0x30, 0x50, 0xa0)})                          # the agent's drawing was exported
        costs = [json.loads(l) for l in rd(os.path.join(self.root, "costs.jsonl")).splitlines()]
        self.assertTrue(any(c["role"] == "artist" for c in costs))

    def test_unusable_map_falls_back_to_clusters(self):
        self.client.images.bad_map = True
        self.assertEqual(self._run("hero", {"prompt": "hero"}), "complete")
        a = self.proj.asset("hero")
        self.assertFalse(a.state.steps["matmap"].info["usable"])
        self.assertTrue(a.state.steps["materials"].info["label_source"].startswith("colour clusters"))
        self.assertIn("gpt-6.1-terra", [kw["model"] for name, kw in self.client.log
                                        if name == "parse" and kw["text_format"] is MaterialLabels])

    def test_key_never_written(self):
        self._run("hero", {"prompt": "hero"})
        for dirpath, _, files in os.walk(self.root):
            for fn in files:
                with open(os.path.join(dirpath, fn), "rb") as f:
                    self.assertNotIn(FAKE_KEY.encode(), f.read(), fn)

    def test_monthly_cap(self):
        self.proj.config["budget"]["monthly_usd"] = 1.0
        self.proj.log_cost({"asset": "other", "kind": "llm", "role": "x", "model": "x", "usd": 1.5})
        self.proj.new_asset("hero", {"prompt": "hero"})
        with self.assertRaises(BudgetExceeded):
            Pipeline(self.proj, "hero", client=self.client).run(auto_approve=True, log=quiet)
        self.assertEqual([c for c in self.client.log], [])   # nothing was sent

    def test_missing_key_message(self):
        orig = openai_backend.find_api_key
        openai_backend.find_api_key = lambda: None
        try:
            with self.assertRaises(RuntimeError) as cm:
                openai_backend.make_client(self.proj.config)
            self.assertIn("OPENAI_API_KEY", str(cm.exception))
        finally:
            openai_backend.find_api_key = orig

    def test_schemas_are_strict_compatible(self):
        from openai.lib._pydantic import to_strict_json_schema

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertNotIn(True, [node.get("additionalProperties")], node)
                    self.assertEqual(sorted(node.get("required", [])), sorted(node.get("properties", {})), node)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        for schema in (CharacterSpec, Pick, MaterialLabels, Critique):
            walk(to_strict_json_schema(schema))

    def test_small_images_upscaled_for_models(self):
        p = os.path.join(self.tmp, "tiny.png")
        from PIL import Image
        Image.new("RGBA", (16, 32), (255, 0, 0, 255)).save(p)
        url = openai_backend.image_data_url(p, 1024)
        im = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        self.assertEqual(im.size, (128, 256))


if __name__ == "__main__":
    unittest.main()
