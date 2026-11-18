"""Free regressions for accounting and interrupted deletion/restoration; no provider or key access."""
import base64
import builtins
import datetime
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from pixelagent import trash
from pixelagent.models import budget_problem, month_spend
from pixelagent.openai_backend import OpenAIImages
from pixelagent.store import Project
from pixelagent.trash import RecentlyDeleted


class StorageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pixelagent-storage-")
        self.root = self.tmp.name
        self.ledger = os.path.join(self.root, "accounting", "costs.jsonl")
        self.overrides = {"backend": {"mode": "dry_run"}, "budget": {
            "ledger": self.ledger, "monthly_scope_dir": self.root,
            "max_usd_per_asset": 50.0, "monthly_usd": 100.0}}
        self.project = Project(os.path.join(self.root, "A"), self.overrides)
        self.deleted = RecentlyDeleted(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def asset(self, aid="hero", **inputs):
        return self.project.new_asset(aid, {"prompt": "test", **inputs})

    def legacy_asset(self, aid="hero", usd=3.5):
        a = self.asset(aid)
        data = a.state.model_dump()
        data.pop("asset_uuid")
        data.pop("legacy_costs")
        with open(a.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with open(os.path.join(self.project.root, "costs.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.datetime.now().isoformat(), "asset": aid, "usd": usd}) + "\n")
        return a

    def image_backend(self, data=None):
        self.asset()
        self.project.config["models"]["image_final"] = "fake-image"
        self.project.config["pricing"]["fake-image"] = {"text_input": 1, "image_input": 1, "output": 100}
        response = NS(data=data if data is not None else [NS(b64_json=base64.b64encode(b"test").decode())],
                      usage=NS(input_tokens=100, output_tokens=1000,
                               input_tokens_details=NS(image_tokens=0, text_tokens=100)))
        client = NS(images=NS(generate=mock.Mock(return_value=response)))
        return OpenAIImages(self.project, "hero", client=client), client

    def test_success_is_charged_even_when_output_write_fails(self):
        backend, client = self.image_backend()
        real_open = builtins.open

        def fail_output(path, mode="r", *args, **kwargs):
            if str(path).endswith("gen00.png") and mode == "wb":
                raise OSError("output destination full")
            return real_open(path, mode, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=fail_output), self.assertRaises(OSError):
            backend.generate("image_final", "test", 1, os.path.join(self.root, "outputs"))
        client.images.generate.assert_called_once()
        self.assertAlmostEqual(self.project.cost_total("hero"), 0.1001)
        self.assertAlmostEqual(month_spend(self.project), 0.1001)
        self.project.config["budget"]["max_usd_per_asset"] = 0.1
        self.assertIn("limit per item", budget_problem(self.project, "hero"))

    def test_success_with_empty_output_is_still_charged_once(self):
        backend, _ = self.image_backend(data=[])
        with self.assertRaisesRegex(RuntimeError, "no images"):
            backend.generate("image_final", "test", 1, os.path.join(self.root, "outputs"))
        self.assertAlmostEqual(self.project.cost_total("hero"), 0.1001)
        self.assertAlmostEqual(month_spend(self.project), 0.1001)

    def test_success_with_bad_base64_is_charged_before_decoding(self):
        backend, _ = self.image_backend(data=[NS(b64_json="a")])
        with self.assertRaises(ValueError):
            backend.generate("image_final", "test", 1, os.path.join(self.root, "outputs"))
        self.assertAlmostEqual(self.project.cost_total("hero"), 0.1001)

    def test_ledger_survives_failure_to_write_project_mirror(self):
        self.asset()
        real_open = builtins.open

        def fail_mirror(path, mode="r", *args, **kwargs):
            if str(path) == os.path.join(self.project.root, "costs.jsonl") and mode == "a":
                raise OSError("project folder is read only")
            return real_open(path, mode, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=fail_mirror), self.assertRaises(OSError):
            self.project.log_cost({"asset": "hero", "usd": 2.0})
        self.assertEqual(self.project.cost_total("hero"), 2.0)
        self.assertEqual(month_spend(self.project), 2.0)

    def test_reusing_id_starts_clean_and_restore_keeps_original_identity(self):
        a = self.asset()
        original_uuid = a.state.asset_uuid
        self.project.log_cost({"asset": "hero", "usd": 3.0})
        eid = self.deleted.delete_items(self.project.root, ["hero"], {})
        new = self.asset()
        self.assertNotEqual(new.state.asset_uuid, original_uuid)
        self.assertEqual(self.project.cost_total("hero"), 0.0)
        self.project.log_cost({"asset": "hero", "usd": 1.0})
        result = self.deleted.restore(eid)
        self.assertEqual(result["id"], "hero-2")
        self.assertEqual(self.project.asset("hero-2").state.asset_uuid, original_uuid)
        self.assertEqual(self.project.cost_total("hero-2"), 3.0)
        self.assertEqual(self.project.cost_total("hero"), 1.0)
        self.assertEqual(month_spend(self.project), 4.0)

    def test_legacy_spend_migrates_once_and_follows_a_renamed_restore(self):
        self.legacy_asset()
        self.assertEqual(self.project.cost_total("hero"), 3.5)
        self.project.log_cost({"asset": "hero", "usd": 1.25})
        self.assertEqual(self.project.cost_total("hero"), 4.75)
        self.assertEqual(month_spend(self.project), 4.75)
        eid = self.deleted.delete_items(self.project.root, ["hero"], {})
        self.asset()
        self.assertEqual(self.project.cost_total("hero"), 0)
        restored = self.deleted.restore(eid)["id"]
        self.assertEqual(self.project.cost_total(restored), 4.75)
        self.assertEqual(self.project.cost_total(restored, month=True), 4.75)
        self.assertEqual(Project(self.project.root, self.overrides).cost_total(restored), 4.75)

    def test_legacy_delete_migrates_before_the_folder_moves(self):
        self.legacy_asset(usd=4.0)
        eid = self.deleted.delete_items(self.project.root, ["hero"], {})
        self.asset()
        self.assertEqual(self.project.cost_total("hero"), 0)
        aid = self.deleted.restore(eid)["id"]
        self.assertEqual(self.project.cost_total(aid), 4.0)

    def test_duplicate_gets_a_new_uuid_and_no_inherited_spend(self):
        self.legacy_asset(usd=4.0)
        a = self.project.asset("hero")
        self.project.log_cost({"asset": "hero", "usd": 2.0})
        shutil.copytree(a.dir, os.path.join(self.project.root, "assets", "copy"))
        b = self.project.asset("copy")
        b.state.id = "copy"
        b.reset_cost_identity()
        b.save()
        self.assertNotEqual(a.state.asset_uuid, b.state.asset_uuid)
        self.assertEqual(self.project.cost_total("copy"), 0)
        self.assertEqual(self.project.cost_total("hero"), 6.0)

    def test_conflict_restore_keeps_a_maximum_length_id_valid(self):
        aid = "a" * 120
        original = self.asset(aid)
        eid = self.deleted.delete_items(self.project.root, [aid], {})
        self.asset(aid)
        restored = self.deleted.restore(eid)["id"]
        self.assertEqual(len(restored), 120)
        self.assertEqual(self.project.asset(restored).state.asset_uuid, original.state.asset_uuid)

    def test_project_rename_preserves_uuid_and_costs(self):
        a = self.asset()
        self.project.log_cost({"asset": "hero", "usd": 2.0})
        target = os.path.join(self.root, "Renamed")
        os.rename(self.project.root, target)
        p = Project(target, self.overrides)
        self.assertEqual(p.asset("hero").state.asset_uuid, a.state.asset_uuid)
        self.assertEqual(p.cost_total("hero"), 2.0)

    def test_item_restore_after_project_delete_keeps_all_its_costs(self):
        self.legacy_asset(usd=3.0)
        self.project.log_cost({"asset": "hero", "usd": 2.0})
        item = self.deleted.delete_items(self.project.root, ["hero"], {})
        project = self.deleted.delete_project(self.project.root, {})
        self.deleted.restore(item)
        restored = Project(self.project.root, self.overrides)
        self.assertEqual(restored.cost_total("hero"), 5.0)
        self.assertEqual(month_spend(restored), 5.0)
        self.assertIn(project, [e["entry"] for e in self.deleted.entries()])

    def test_metadata_failure_before_delete_does_not_move_files(self):
        a = self.asset()
        with mock.patch.object(self.deleted, "_write_meta", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.deleted.delete_items(self.project.root, ["hero"], {})
        self.assertTrue(os.path.isfile(a.state_path))
        self.assertEqual(self.deleted.entries(), [])

    def test_metadata_failure_after_delete_keeps_a_restorable_entry(self):
        a = self.asset()
        real_write = self.deleted._write_meta
        calls = []

        def fail_final(edir, meta):
            calls.append(meta["phase"])
            if meta["phase"] == "deleted":
                raise OSError("metadata locked")
            real_write(edir, meta)

        with mock.patch.object(self.deleted, "_write_meta", side_effect=fail_final), self.assertRaises(OSError):
            self.deleted.delete_items(self.project.root, ["hero"], {})
        self.assertFalse(os.path.exists(a.dir))
        entry = self.deleted.entries()[0]
        self.assertEqual(entry["phase"], "deleting")
        RecentlyDeleted(self.root).restore(entry["entry"])
        self.assertTrue(os.path.isfile(a.state_path))

    def test_project_metadata_failure_after_move_is_recoverable(self):
        self.asset()
        real_write = self.deleted._write_meta

        def fail_final(edir, meta):
            if meta["phase"] == "deleted":
                raise OSError("disk full")
            real_write(edir, meta)

        with mock.patch.object(self.deleted, "_write_meta", side_effect=fail_final), self.assertRaises(OSError):
            self.deleted.delete_project(self.project.root, {})
        self.assertFalse(os.path.isdir(self.project.root))
        entry = self.deleted.entries()[0]
        self.deleted.restore(entry["entry"])
        self.assertTrue(os.path.isfile(os.path.join(self.project.root, "assets", "hero", "state.json")))

    def test_interrupted_multi_item_delete_is_recoverable(self):
        self.asset()
        self.asset("walk", character="hero")
        real_move = trash._move
        calls = []

        def interrupt(src, dst):
            calls.append(src)
            if len(calls) == 2:
                raise KeyboardInterrupt("simulated process interruption")
            real_move(src, dst)

        with mock.patch.object(trash, "_move", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.deleted.delete_items(self.project.root, ["hero", "walk"], {})
        self.assertEqual(self.project.asset_ids(), ["walk"])
        entry = self.deleted.entries()[0]
        RecentlyDeleted(self.root).restore(entry["entry"])
        self.assertEqual(self.project.asset_ids(), ["hero", "walk"])
        self.assertEqual(self.project.asset("walk").state.inputs["character"], "hero")

    def test_interrupted_restore_resumes_the_same_destinations_and_relationships(self):
        original = self.asset()
        self.asset("walk", character="hero")
        eid = self.deleted.delete_items(self.project.root, ["hero", "walk"], {})
        replacement = self.asset()
        real_move = trash._move
        calls = []

        def interrupt(src, dst):
            calls.append(src)
            if len(calls) == 2:
                raise KeyboardInterrupt("simulated process interruption")
            real_move(src, dst)

        with mock.patch.object(trash, "_move", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.deleted.restore(eid)
        self.assertEqual(self.project.asset_ids(), ["hero", "hero-2"])
        self.assertEqual(self.deleted.entries()[0]["phase"], "restoring")
        result = RecentlyDeleted(self.root).restore(eid)
        self.assertEqual(result["id"], "hero-2")
        self.assertEqual(self.project.asset("hero-2").state.asset_uuid, original.state.asset_uuid)
        self.assertEqual(self.project.asset("hero").state.asset_uuid, replacement.state.asset_uuid)
        self.assertEqual(self.project.asset("walk").state.inputs["character"], "hero-2")
        self.assertEqual(self.deleted.entries(), [])

    def test_failed_restore_metadata_does_not_move_payload(self):
        self.asset()
        eid = self.deleted.delete_items(self.project.root, ["hero"], {})
        with mock.patch.object(self.deleted, "_write_meta", side_effect=OSError("locked")), self.assertRaises(OSError):
            self.deleted.restore(eid)
        self.assertEqual(self.project.asset_ids(), [])
        self.assertEqual(len(self.deleted.entries()), 1)
        self.deleted.restore(eid)
        self.assertEqual(self.project.asset_ids(), ["hero"])

    def test_restore_after_final_cleanup_failure_does_not_duplicate(self):
        self.asset()
        eid = self.deleted.delete_project(self.project.root, {})
        with mock.patch.object(trash.shutil, "rmtree", side_effect=OSError("locked")), self.assertRaises(OSError):
            self.deleted.restore(eid)
        result = RecentlyDeleted(self.root).restore(eid)
        self.assertEqual(result["project"], "A")
        self.assertFalse(os.path.exists(os.path.join(self.root, "A (2)")))

    def test_old_payload_only_orphan_is_visible_and_restorable(self):
        a = self.asset()
        eid = "20260101-010101-abcdef"
        target = os.path.join(self.deleted.dir, eid, "payload", "assets")
        os.makedirs(target)
        os.rename(a.dir, os.path.join(target, "hero"))
        entry = self.deleted.entries()[0]
        self.assertIn("metadata", entry["recovery_note"])
        result = self.deleted.restore(eid)
        self.assertEqual(result["project"], "Recovered items")
        self.assertTrue(os.path.isfile(os.path.join(self.root, result["project"], "assets", "hero", "state.json")))

    def test_old_project_orphan_is_visible_and_restorable(self):
        self.asset()
        eid = "20260101-010101-abcdef"
        payload = os.path.join(self.deleted.dir, eid, "payload")
        os.makedirs(payload)
        os.rename(self.project.root, os.path.join(payload, "A"))
        self.assertEqual(self.deleted.entries()[0]["kind"], "project")
        self.deleted.restore(eid)
        self.assertEqual(self.project.asset_ids(), ["hero"])


if __name__ == "__main__":
    unittest.main()
