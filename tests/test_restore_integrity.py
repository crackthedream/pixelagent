"""Interrupted reruns must not make stale drawings compatible with a new concept."""
import json
import tempfile
import unittest
from pathlib import Path

from pixelagent.pipeline import Pipeline
from pixelagent.store import Project


class RestoreIntegrityTests(unittest.TestCase):
    def test_current_drawing_cannot_restore_after_upstream_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project(tmp, {"backend": {"mode": "dry_run"}})
            proj.new_asset("hero", {"prompt": "a fixture"})
            pipe = Pipeline(proj, "hero")
            for step in pipe.steps:
                rec = pipe.asset.record(step.name)
                rec.status = "done"
                rec.info["dir"] = f"steps/{step.index:02d}_{step.name}/v001"
            artist = pipe.asset.record("artist")
            folder = Path(pipe.asset.path(artist.info["dir"]))
            folder.mkdir(parents=True)
            artist.outputs = {"preview": artist.info["dir"] + "/artist.png"}
            (folder / "artist.png").write_bytes(b"fixture")
            (folder / "outputs.json").write_text(json.dumps({
                "outputs": artist.outputs, "upstream": pipe.upstream("artist")}), encoding="utf-8")
            pipe.asset.save()
            self.assertTrue(pipe.versions("artist")[0]["compatible"])
            pipe.reset_from("concept")
            concept = pipe.asset.record("concept")
            concept.info["dir"] = "steps/02_concept/v002"
            concept.status = "done"
            pipe.asset.save()
            self.assertFalse(pipe.versions("artist")[0]["compatible"])
            with self.assertRaisesRegex(ValueError, "earlier plan"):
                pipe.restore_version("artist", 1)
            self.assertEqual(pipe.asset.record("artist").status, "pending")

    def test_legacy_current_drawing_has_no_proven_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project(tmp, {"backend": {"mode": "dry_run"}})
            proj.new_asset("hero", {"prompt": "fixture"})
            pipe = Pipeline(proj, "hero")
            art = pipe.asset.record("artist")
            art.status = "done"
            art.info["dir"] = "steps/10_artist/v001"
            art.outputs = {"preview": art.info["dir"] + "/artist.png"}
            folder = Path(pipe.asset.path(art.info["dir"]))
            folder.mkdir(parents=True)
            (folder / "artist.png").write_bytes(b"fixture")
            pipe.asset.save()
            versions = pipe.versions("artist")
            self.assertEqual(len(versions), 1)
            self.assertFalse(versions[0]["compatible"])
