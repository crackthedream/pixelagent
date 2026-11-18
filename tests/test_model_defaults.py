"""Model-default upgrades must preserve explicit project choices and their pricing."""
from pathlib import Path
import tempfile
import unittest

from pixelagent.config import load_config


class ModelDefaultTests(unittest.TestCase):
    def test_main_roles_upgrade_without_replacing_specialized_roles(self):
        cfg = load_config()
        for role in ("director", "detail", "repair", "animator", "artist"):
            self.assertEqual(cfg["models"][role], "gpt-6.2-sol", role)
        self.assertEqual(cfg["models"]["final_critic"], "gpt-6.2-astra")
        for role in ("vision_pick", "labeler", "critic"):
            self.assertEqual(cfg["models"][role], "gpt-6.1-terra", role)
        self.assertEqual(cfg["models"]["utility"], "gpt-6-luna")
        self.assertEqual(cfg["models"]["image_draft"], "gpt-image-2.6-flare")
        self.assertEqual(cfg["models"]["image_final"], "gpt-image-2.6-sunburst")
        self.assertEqual(cfg["reasoning"]["director"], "medium")
        self.assertEqual(cfg["reasoning"]["final_critic"], "high")
        self.assertEqual(cfg["reasoning"]["utility"], "none")

    def test_project_and_runtime_overrides_keep_explicit_legacy_choices(self):
        with tempfile.TemporaryDirectory(prefix="pxa_model_defaults_") as folder:
            Path(folder, "config.toml").write_text(
                '[models]\ndirector = "gpt-6.1-sol"\nfinal_critic = "gpt-6-astra"\n'
                'image_final = "gpt-image-2.5-sunburst"\n'
                '[reasoning]\ndirector = "low"\n'
                '[pricing."gpt-6-astra"]\ninput = 9.0\n', encoding="utf-8")
            project = load_config(folder)
            self.assertEqual(project["models"]["director"], "gpt-6.1-sol")
            self.assertEqual(project["models"]["final_critic"], "gpt-6-astra")
            self.assertEqual(project["models"]["image_final"], "gpt-image-2.5-sunburst")
            self.assertEqual(project["models"]["detail"], "gpt-6.2-sol")
            self.assertEqual(project["reasoning"]["director"], "low")
            self.assertEqual(project["pricing"]["gpt-6-astra"]["input"], 9.0)
            self.assertEqual(project["pricing"]["gpt-6-astra"]["output"], 50.0)
            self.assertEqual(project["pricing"]["gpt-6-astra"]["cached_input"], 1.0)
            runtime = load_config(folder, {"models": {"director": "gpt-6-luna"}})
            self.assertEqual(runtime["models"]["director"], "gpt-6-luna")
            self.assertEqual(runtime["models"]["final_critic"], "gpt-6-astra")
        self.assertEqual(load_config()["models"]["director"], "gpt-6.2-sol")

    def test_text_prices_cover_cache_writes_and_long_context(self):
        prices = load_config()["pricing"]
        for model, values in {
            "gpt-6.2-sol": (2.0, 0.10, 2.50, 5.0, 10.0),
            "gpt-6.2-astra": (8.0, 0.40, 10.0, 20.0, 40.0),
            "gpt-6-astra": (10.0, 1.0, 12.5, 25.0, 50.0),
            "gpt-6.1-sol": (2.0, 0.10, 2.5, 5.0, 10.0),
            "gpt-6.1-terra": (1.0, 0.05, 1.25, 2.5, 5.0),
            "gpt-6-luna": (0.10, 0.01, 0.125, 0.25, 0.50),
        }.items():
            with self.subTest(model=model):
                p = prices[model]
                self.assertEqual(tuple(p[k] for k in ("input", "cached_input", "cache_write_30m",
                                                      "cache_write_12h", "output")), values)
                self.assertEqual(p["long_context_threshold"], 272000)
                self.assertEqual(p["long_input_multiplier"], 2.0)
                self.assertEqual(p["long_output_multiplier"], 1.5)

    def test_old_prices_and_image_prices_remain_available(self):
        prices = load_config()["pricing"]
        for model, rates in {"gpt-6-astra": (10.0, 50.0), "gpt-6.1-sol": (2.0, 10.0),
                             "gpt-6.1-terra": (1.0, 5.0), "gpt-6-luna": (0.10, 0.50)}.items():
            self.assertEqual((prices[model]["input"], prices[model]["output"]), rates)
        for model in ("gpt-image-2.6-flare", "gpt-image-2.6-sunburst"):
            self.assertEqual(prices[model], {"text_input": 5.0, "image_input": 8.0, "output": 30.0})


if __name__ == "__main__":
    unittest.main()
