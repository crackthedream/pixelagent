"""Every app job owns its cancellation flag, including expression jobs."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from pixelagent import cancel
from pixelagent.app.server import Jobs
from pixelagent.app import server
from pixelagent.store import Project, ledger_month_spend


class JobLifecycleTests(unittest.TestCase):
    def test_deleting_legacy_project_seeds_ledger_before_moving_logs(self):
        with tempfile.TemporaryDirectory() as root:
            settings = {**server.default_settings(), "projects_dir": str(Path(root) / "projects"),
                        "backend": "dry_run"}
            proj = Project(str(Path(settings["projects_dir"]) / "P"))
            proj.new_asset("hero", {"prompt": "fixture"})
            proj.log_cost({"asset": "hero", "usd": 3.0})
            with mock.patch.object(server, "SETTINGS_FILE", str(Path(root) / "settings.json")):
                self.assertFalse(Path(server.ledger_path()).exists())
                server.delete_project(settings, {"project": "P"})
                self.assertEqual(ledger_month_spend(server.ledger_path(), settings["projects_dir"]), 3.0)

    def test_new_job_clears_previous_flag_and_cleans_up_on_cancel(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = Jobs()
            cancel.request(root, "hero")
            observed = []
            def work(log):
                observed.append(cancel.requested(root, "hero"))
                cancel.request(root, "hero")
                raise cancel.Cancelled("test cancellation")
            jobs.start(("P", "hero"), work, cancel_root=root)
            jobs.running[("P", "hero")].join(5)
            self.assertEqual(observed, [False])
            self.assertFalse(cancel.requested(root, "hero"))

    def test_rejected_duplicate_does_not_clear_running_jobs_cancel(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = Jobs()
            began, release = threading.Event(), threading.Event()
            def work(log):
                began.set()
                release.wait(5)
            jobs.start(("P", "hero"), work, cancel_root=root)
            self.assertTrue(began.wait(5))
            try:
                cancel.request(root, "hero")
                with self.assertRaisesRegex(ValueError, "already working"):
                    jobs.start(("P", "hero"), work, cancel_root=root)
                self.assertTrue(cancel.requested(root, "hero"))
            finally:
                release.set()
                jobs.running[("P", "hero")].join(5)
            self.assertFalse(cancel.requested(root, "hero"))
