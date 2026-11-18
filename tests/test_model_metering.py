"""Model request and spend regressions using injected clients only; no key or network access."""
import base64
import json
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from pixelagent.agent_session import AgentSession
from pixelagent.metering import PricingError, UsageUnpriced, price_profile, usage_price
from pixelagent.models import make_backend
from pixelagent.openai_backend import OpenAIImages, OpenAILLM
from pixelagent.schemas import Pick
from pixelagent.store import Project


def usage(inputs=1000, outputs=100, cached=0, written=0, split=None):
    details = NS(cached_tokens=cached, cache_write_tokens=written)
    if split is not None:
        details.cache_write_tokens_details = NS(ttl_30m_tokens=split[0], ttl_12h_tokens=split[1])
    return NS(input_tokens=inputs, output_tokens=outputs, input_tokens_details=details)


class ModelMeteringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pxa-models-")
        self.project = Project(self.temp.name, {"backend": {"mode": "dry_run"},
                                              "budget": {"monthly_usd": 1000, "max_usd_per_asset": 1000}})
        self.project.new_asset("hero", {"prompt": "test"})
        self.output = os.path.join(self.temp.name, "output")
        os.makedirs(self.output)
        self.model = "gpt-6.2-sol"
        self.project.config["models"]["director"] = self.model
        self.project.config["reasoning"]["director"] = "none"
        self.result = Pick(index=0, reason="test")
        self.client = NS(responses=NS(parse=mock.Mock(return_value=NS(output_parsed=self.result, usage=usage())),
                                      create=mock.Mock()), images=NS(generate=mock.Mock(), edit=mock.Mock()))
        self.mcp = NS(tools=lambda: [], call=lambda *args: ("ok", [], False))
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()
        self.temp.cleanup()

    def text(self):
        return OpenAILLM(self.project, "hero", self.client).structured("director", "Stable rubric", "Evaluate this", [],
                                                                    Pick, lambda: self.result)

    def session(self, model=None, effort="none"):
        s = AgentSession(self.project, "hero", self.mcp, self.client, model or self.model, self.output,
                         effort=effort, log=lambda *_: None)
        self.sessions.append(s)
        return s

    def finish(self, rid="r1", cid="f1", tokens=None):
        return NS(id=rid, usage=tokens or usage(), output=[NS(type="function_call", name="finish", call_id=cid,
                                                           arguments='{"summary":"done"}')])

    def costs(self):
        with open(os.path.join(self.project.root, "costs.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_62_structured_request_uses_developer_prefix_and_supported_settings(self):
        self.assertEqual(self.text(), self.result)
        request = self.client.responses.parse.call_args.kwargs
        self.assertNotIn("instructions", request)
        self.assertEqual(request["input"][0], {"role": "developer", "content": "Stable rubric"})
        self.assertEqual(request["input"][1]["role"], "user")
        self.assertEqual(request["reasoning"], {"effort": "low"})
        self.assertEqual(request["service_tier"], "default")
        self.assertEqual(request["prompt_cache_options"], {"ttl": "30m"})
        self.assertTrue({"temperature", "top_p", "top_logprobs"}.isdisjoint(request))

    def test_minimal_effort_maps_to_low(self):
        self.project.config["reasoning"]["director"] = "minimal"
        self.text()
        self.assertEqual(self.client.responses.parse.call_args.kwargs["reasoning"]["effort"], "low")

    def test_legacy_request_shape_is_preserved(self):
        self.project.config["models"]["director"] = "gpt-6-luna"
        self.text()
        request = self.client.responses.parse.call_args.kwargs
        self.assertEqual(request["instructions"], "Stable rubric")
        self.assertEqual(request["input"][0]["role"], "user")
        self.assertEqual(request["service_tier"], "default")
        self.assertNotIn("prompt_cache_options", request)

    def test_all_builtin_legacy_text_paths_pin_standard_tier(self):
        for model in ("gpt-6-astra", "gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra"):
            with self.subTest(model=model):
                self.project.config["models"]["director"] = model
                self.text()
                self.assertEqual(self.client.responses.parse.call_args.kwargs["service_tier"], "default")
                self.client.responses.create.return_value = self.finish()
                self.session(model=model).converse("draw", "DRAW", [], 1)
                self.assertEqual(self.client.responses.create.call_args.kwargs["service_tier"], "default")

    def test_custom_priced_model_keeps_compatible_request_shape(self):
        self.project.config["models"]["director"] = "custom-priced-model"
        self.project.config["pricing"]["custom-priced-model"] = {"input": 1, "output": 2}
        self.text()
        self.assertNotIn("service_tier", self.client.responses.parse.call_args.kwargs)

    def test_every_chained_artist_round_receives_current_phase_instructions(self):
        self.client.responses.create.side_effect = [
            NS(id="r1", usage=usage(), output=[NS(type="function_call", name="view", call_id="v1", arguments="{}")]),
            self.finish("r2", "f2"), self.finish("r3", "f3")]
        s = self.session()
        self.assertEqual(s.converse("draw", "DRAW instruction", [], 3)[0], "finished")
        self.assertEqual(s.converse("face", "FACE instruction", [], 1)[0], "finished")
        requests = [c.kwargs for c in self.client.responses.create.call_args_list]
        self.assertEqual([r["instructions"] for r in requests], ["DRAW instruction", "DRAW instruction", "FACE instruction"])
        self.assertNotIn("previous_response_id", requests[0])
        self.assertEqual(requests[1]["previous_response_id"], "r1")
        self.assertEqual(requests[2]["previous_response_id"], "r2")
        self.assertEqual(requests[2]["input"][0]["call_id"], "f2")
        self.assertTrue(all(r["reasoning"]["effort"] == "low" for r in requests))

    def test_session_model_change_requires_fresh_conversation(self):
        self.client.responses.create.side_effect = [self.finish(), self.finish("r2", "f2")]
        s = self.session()
        s.converse("draw", "DRAW", [], 1)
        s.model = "gpt-6.2-astra"
        with self.assertRaisesRegex(ValueError, "fresh session"):
            s.converse("face", "FACE", [], 1)
        self.assertEqual(self.client.responses.create.call_count, 1)
        s.converse("face", "FACE", [], 1, fresh=True)
        self.assertNotIn("previous_response_id", self.client.responses.create.call_args.kwargs)

    def test_unknown_text_model_is_refused_before_request(self):
        self.project.config["models"]["director"] = "custom-model"
        with self.assertRaises(PricingError):
            self.text()
        self.client.responses.parse.assert_not_called()

    def test_unknown_direct_artist_override_is_refused_before_request(self):
        with self.assertRaises(PricingError):
            self.session(model="custom-artist").converse("draw", "DRAW", [], 1)
        self.client.responses.create.assert_not_called()

    def test_unknown_image_model_is_refused_before_request_or_output_creation(self):
        self.project.config["models"]["image_final"] = "custom-image"
        dest = os.path.join(self.output, "new")
        with self.assertRaises(PricingError):
            OpenAIImages(self.project, "hero", self.client).generate("image_final", "draw", 1, dest)
        self.client.images.generate.assert_not_called()
        self.assertFalse(os.path.exists(dest))

    def test_partial_62_prices_are_refused_before_request(self):
        for field in ("input", "output", "cached_input", "cache_write_30m", "long_input_multiplier"):
            with self.subTest(field=field):
                p = self.project.config["pricing"][self.model]
                value = p.pop(field)
                try:
                    with self.assertRaises(PricingError):
                        self.text()
                finally:
                    p[field] = value
        self.client.responses.parse.assert_not_called()

    def test_partial_image_prices_are_refused_before_request(self):
        model = self.project.config["models"]["image_final"]
        self.project.config["pricing"][model].pop("image_input")
        with self.assertRaises(PricingError):
            OpenAIImages(self.project, "hero", self.client).generate("image_final", "draw", 1, self.output)
        self.client.images.generate.assert_not_called()

    def test_nonfinite_negative_or_boolean_rates_never_reach_provider(self):
        for bad in (float("nan"), float("inf"), -1, True, "2"):
            with self.subTest(rate=bad):
                self.project.config["pricing"][self.model]["input"] = bad
                with self.assertRaises(PricingError):
                    self.text()
        self.client.responses.parse.assert_not_called()

    def test_cached_reads_and_writes_are_disjoint_not_surcharges(self):
        tokens = usage(inputs=1000, outputs=100, cached=200, written=300)
        self.client.responses.parse.return_value.usage = tokens
        self.text()
        expected = (500 * 2 + 200 * .1 + 300 * 2.5 + 100 * 10) / 1e6
        self.assertAlmostEqual(self.project.cost_total("hero"), expected, places=8)
        entry = self.costs()[0]
        self.assertEqual(entry["cached_tokens"], 200)
        self.assertEqual(entry["cache_write_tokens"], 300)
        self.assertEqual(entry["price_profile"]["cache_write_30m"], 2.5)

    def test_cache_ttl_breakdown_is_used_when_returned_by_api(self):
        p = price_profile(self.project.config, self.model, "llm")
        tokens = usage(inputs=1000, outputs=100, cached=200, written=300, split=(100, 200))
        expected = (500 * 2 + 200 * .1 + 100 * 2.5 + 200 * 5 + 100 * 10) / 1e6
        self.assertAlmostEqual(usage_price(p, tokens, "llm"), expected)

    def test_long_context_threshold_applies_to_whole_request(self):
        p = price_profile(self.project.config, self.model, "llm")
        for inputs, factor, out_factor in ((272000, 1, 1), (272001, 2, 1.5)):
            with self.subTest(inputs=inputs):
                tokens = usage(inputs=inputs, outputs=100, cached=200, written=300)
                expected = (((inputs - 500) * 2 + 200 * .1 + 300 * 2.5) * factor + 100 * 10 * out_factor) / 1e6
                self.assertAlmostEqual(usage_price(p, tokens, "llm"), expected)

    def test_artist_and_structured_paths_share_same_cost_calculation(self):
        tokens = usage(inputs=273000, outputs=100, cached=200, written=300)
        self.client.responses.parse.return_value.usage = tokens
        self.text()
        self.client.responses.create.return_value = self.finish(tokens=tokens)
        s = self.session()
        s.converse("draw", "DRAW", [], 1)
        entries = self.costs()
        self.assertAlmostEqual(entries[0]["usd"], entries[1]["usd"])
        self.assertAlmostEqual(s.spent, entries[1]["usd"])

    def test_missing_usage_stops_current_job_but_allows_deliberate_new_job(self):
        llm = OpenAILLM(self.project, "hero", self.client)
        self.client.responses.parse.return_value.usage = None
        with self.assertRaises(UsageUnpriced):
            llm.structured("director", "rubric", "ask", [], Pick, lambda: self.result)
        self.assertIsNone(self.costs()[0]["usd"])
        self.assertEqual(self.costs()[0]["pricing_status"], "unknown")
        with self.assertRaisesRegex(UsageUnpriced, "This job stopped"):
            llm.structured("director", "rubric", "ask", [], Pick, lambda: self.result)
        self.assertEqual(self.client.responses.parse.call_count, 1)
        self.client.responses.parse.return_value.usage = usage()
        self.text()  # a deliberate new backend/job can proceed; no historical/global ledger edits
        self.assertEqual(self.client.responses.parse.call_count, 2)
        self.assertEqual(self.costs()[0]["pricing_status"], "unknown")
        self.assertEqual(self.costs()[1]["pricing_status"], "estimated")

    def test_unknown_usage_stops_both_text_and_images_in_the_same_job(self):
        backend = make_backend(self.project, "hero", mode="openai", client=self.client)
        self.client.responses.parse.return_value.usage = None
        with self.assertRaises(UsageUnpriced):
            backend.llm.structured("director", "rubric", "ask", [], Pick, lambda: self.result)
        with self.assertRaisesRegex(UsageUnpriced, "This job stopped"):
            backend.images.generate("image_final", "draw", 1, self.output)
        self.client.images.generate.assert_not_called()

    def test_unknown_artist_usage_stops_that_session_only(self):
        self.client.responses.create.return_value = self.finish()
        self.client.responses.create.return_value.usage = None
        s = self.session()
        with self.assertRaises(UsageUnpriced):
            s.converse("draw", "DRAW", [], 1)
        with self.assertRaisesRegex(UsageUnpriced, "This job stopped"):
            s.converse("draw", "DRAW", [], 1)
        self.assertEqual(self.client.responses.create.call_count, 1)
        self.client.responses.create.return_value = self.finish("new", "new-finish")
        self.assertEqual(self.session().converse("draw", "DRAW", [], 1)[0], "finished")

    def test_inconsistent_cache_split_is_not_reported_as_a_known_cost(self):
        self.client.responses.parse.return_value.usage = usage(inputs=100, cached=70, written=60)
        with self.assertRaises(UsageUnpriced):
            self.text()
        self.assertEqual(self.costs()[0]["pricing_status"], "unknown")

    def test_nonstandard_62_response_is_not_metered_as_standard(self):
        self.client.responses.parse.return_value.service_tier = "priority"
        with self.assertRaises(UsageUnpriced):
            self.text()
        self.assertEqual(self.costs()[0]["pricing_status"], "unknown")

    def test_nonstandard_legacy_and_custom_responses_are_not_silently_underpriced(self):
        self.project.config["pricing"]["custom-priced-model"] = {"input": 1, "output": 2}
        for model in ("gpt-6.1-terra", "gpt-6-luna", "custom-priced-model"):
            with self.subTest(model=model):
                self.project.config["models"]["director"] = model
                self.client.responses.parse.return_value.service_tier = "priority"
                with self.assertRaises(UsageUnpriced):
                    self.text()
                self.assertEqual(self.costs()[-1]["pricing_status"], "unknown")

    def test_image_log_keeps_input_breakdown_used_for_cost(self):
        self.client.images.generate.return_value = NS(data=[NS(b64_json=base64.b64encode(b"fake").decode())],
            usage=NS(input_tokens=1000, output_tokens=100, input_tokens_details=NS(text_tokens=200, image_tokens=800)))
        OpenAIImages(self.project, "hero", self.client).generate("image_final", "draw", 1, self.output)
        entry = self.costs()[0]
        self.assertEqual(entry["input_tokens_details"]["image_tokens"], 800)
        self.assertEqual(entry["input_tokens_details"]["text_tokens"], 200)
        self.assertAlmostEqual(entry["usd"], (200 * 5 + 800 * 8 + 100 * 30) / 1e6)

    def test_nonzero_image_usage_requires_both_modality_counts(self):
        for details in (None, NS(text_tokens=1000), NS(image_tokens=1000)):
            with self.subTest(details=details):
                self.client.images.generate.return_value = NS(data=[], usage=NS(
                    input_tokens=1000, output_tokens=100, input_tokens_details=details))
                images = OpenAIImages(self.project, "hero", self.client)
                with self.assertRaises(UsageUnpriced):
                    images.generate("image_final", "draw", 1, self.output)
                self.assertEqual(self.costs()[-1]["pricing_status"], "unknown")
                calls = self.client.images.generate.call_count
                with self.assertRaisesRegex(UsageUnpriced, "This job stopped"):
                    images.generate("image_final", "draw", 1, self.output)
                self.assertEqual(self.client.images.generate.call_count, calls)

    def test_image_cache_total_without_modality_split_does_not_invent_discount(self):
        model = self.project.config["models"]["image_final"]
        p = price_profile(self.project.config, model, "image")
        tokens = NS(input_tokens=1000, output_tokens=100,
                    input_tokens_details=NS(text_tokens=200, image_tokens=800, cached_tokens=700))
        self.assertAlmostEqual(usage_price(p, tokens, "image"), (200 * 5 + 800 * 8 + 100 * 30) / 1e6)

    def test_provider_error_never_escapes_with_secret_text(self):
        secret = "FAKE-CREDENTIAL-FRAGMENT-NOT-A-REAL-KEY"
        for kind in ("text", "image", "artist"):
            with self.subTest(kind=kind):
                self.client.responses.parse.side_effect = RuntimeError(secret)
                self.client.images.generate.side_effect = RuntimeError(secret)
                self.client.responses.create.side_effect = RuntimeError(secret)
                with self.assertRaises(RuntimeError) as cm:
                    if kind == "text":
                        self.text()
                    elif kind == "image":
                        OpenAIImages(self.project, "hero", self.client).generate("image_final", "draw", 1, self.output)
                    else:
                        self.session().converse("draw", "DRAW", [], 1)
                self.assertNotIn(secret, str(cm.exception))
                self.assertNotIn(secret, json.dumps(self.costs()))


if __name__ == "__main__":
    unittest.main()
