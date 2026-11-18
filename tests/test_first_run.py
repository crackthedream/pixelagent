"""A fresh desktop install is useful without credentials; saved mode choices still win."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pixelagent.app import server


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pixelagent-first-run-")
        self.root = Path(self.tmp.name)
        self.settings = self.root / "profile" / "settings.json"
        self.patch = mock.patch.object(server, "SETTINGS_FILE", str(self.settings))
        self.patch.start()
        self.home = mock.patch.object(server.os.path, "expanduser", return_value=str(self.root))
        self.home.start()

    def tearDown(self):
        self.home.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def test_fresh_profile_starts_in_free_test_run(self):
        with mock.patch.object(server, "key_found", side_effect=AssertionError("no credential lookup")):
            settings = server.load_settings()
        self.assertEqual(settings["backend"], "dry_run")
        self.assertEqual(server.overrides(settings)["backend"]["mode"], "dry_run")
        self.assertTrue(Path(settings["projects_dir"]).is_dir())

    def test_existing_saved_openai_mode_is_preserved(self):
        saved = {**server.default_settings(), "backend": "openai", "sprite_height": 128}
        server.save_settings(saved)
        loaded = server.load_settings()
        self.assertEqual(loaded["backend"], "openai")
        self.assertEqual(loaded["sprite_height"], 128)
        self.assertEqual(server.overrides(loaded)["backend"]["mode"], "openai")

    def test_mode_can_be_saved_in_both_directions(self):
        settings = server.load_settings()
        for mode in ("openai", "dry_run"):
            settings["backend"] = mode
            server.save_settings(settings)
            self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8"))["backend"], mode)
            self.assertEqual(server.load_settings()["backend"], mode)


if __name__ == "__main__":
    unittest.main()
