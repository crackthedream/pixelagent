"""Spending limits: one monthly limit for all projects, a limit per item, checks before paid work starts, never
blocking the free final export, and the drawing loop stopping cleanly when a limit is reached."""
import datetime
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from pixelagent.agent_session import AgentSession
from pixelagent.app import server
from pixelagent.models import budget_problem, month_spend
from pixelagent.store import Project


def spend(project, usd, asset="x", when=None):
    """Record spending the way the app does (the project's log and the app ledger)."""
    t = (when or datetime.datetime.now()).isoformat(timespec="seconds")
    project.log_cost({"time": t, "asset": asset, "usd": usd})


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pixelagent-budget-")
        self.settings = {**server.default_settings(), "projects_dir": self.tmp, "backend": "openai",
                         "monthly_usd": 5.0, "asset_usd": 3.0}
        # the ledger lives next to the settings file: keep it in the temp folder, never the user's real one
        self.patch = mock.patch.object(server, "SETTINGS_FILE", os.path.join(self.tmp, "cfg", "settings.json"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def proj(self, name):
        return Project(os.path.join(self.tmp, name), server.overrides(self.settings))

    def test_monthly_limit_counts_every_project(self):
        a, b = self.proj("A"), self.proj("B")
        spend(a, 3.0)
        self.assertIsNone(budget_problem(b))
        spend(b, 2.5)                                     # 5.5 in total, though each project is under 5
        self.assertIn("monthly limit", budget_problem(a))
        self.assertIn("monthly limit", budget_problem(b))
        last_month = datetime.datetime.now().replace(day=1) - datetime.timedelta(days=2)
        c = self.proj("C")
        spend(c, 50.0, when=last_month)                   # last month does not count
        self.assertEqual(round(month_spend(a), 2), 5.5)

    def test_deleting_a_project_keeps_its_spending(self):
        # review finding F02: the monthly total used to be summed from project folders, so deleting a project
        # (or changing the projects folder) let new paid work through although the money was spent
        a = self.proj("A")
        self.proj("B")
        spend(a, 5.5)
        self.assertIn("monthly limit", budget_problem(self.proj("B")))
        server.delete_project(self.settings, {"project": "A"})
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "A")))
        self.assertIn("monthly limit", budget_problem(self.proj("B")))
        with self.assertRaises(ValueError):
            server.create_character(self.settings, {"project": "B", "prompt": "a knight"})
        moved = {**self.settings, "projects_dir": os.path.join(self.tmp, "elsewhere")}
        os.makedirs(moved["projects_dir"])
        self.assertIn("monthly limit", budget_problem(Project(os.path.join(moved["projects_dir"], "C"),
                                                               server.overrides(moved))))

    def test_the_ledger_starts_from_existing_spending(self):
        # projects that spent money before the ledger existed are counted once, when the ledger is created
        legacy = Project(os.path.join(self.tmp, "Old"))           # no ledger in its config: like a run before it
        legacy.log_cost({"asset": "x", "usd": 4.0})
        self.assertFalse(os.path.exists(server.ledger_path()))
        self.assertAlmostEqual(month_spend(self.proj("New")), 4.0)
        self.assertTrue(os.path.exists(server.ledger_path()))
        self.assertAlmostEqual(month_spend(self.proj("New")), 4.0)  # seeded once, not on every read

    def test_limit_per_item(self):
        p = self.proj("A")
        spend(p, 3.2, asset="hero")
        self.assertIn("limit per item", budget_problem(p, "hero"))
        self.assertIsNone(budget_problem(p, "other"))

    def test_paid_work_is_refused_before_anything_is_created(self):
        p = self.proj("A")
        spend(p, 6.0)
        with self.assertRaises(ValueError) as cm:
            server.create_character(self.settings, {"project": "A", "prompt": "a knight"})
        self.assertIn("Nothing was started", str(cm.exception))
        self.assertEqual(p.asset_ids(), [])
        # test runs are free and never refused
        with mock.patch.object(server, "start_run") as run:
            server.create_character({**self.settings, "backend": "dry_run"}, {"project": "A", "prompt": "a knight"})
        run.assert_called_once()

    def gate(self, p, aid, step):
        a = p.new_asset(aid, {"prompt": "x"})
        a.record(step).status = "awaiting_gate"
        a.save()

    def test_the_final_approval_is_never_blocked(self):
        p = self.proj("A")
        spend(p, 6.0)                                     # over the monthly limit
        self.gate(p, "done-drawing", "review")                 # gate C: approving only exports (free)
        with mock.patch.object(server, "start_run") as run:
            server.decide(self.settings, {"project": "A", "id": "done-drawing", "approve": True})
        run.assert_called_once()
        self.assertFalse(run.call_args.kwargs.get("check", True))
        self.gate(p, "planned", "intake")                      # gate A: approving starts paid work
        with mock.patch.object(server, "start_run") as run, self.assertRaises(ValueError):
            server.decide(self.settings, {"project": "A", "id": "planned", "approve": True})
        run.assert_not_called()
        self.assertEqual(p.asset("planned").state.gates, {})   # and nothing was recorded

    def test_drawing_loop_stops_cleanly_at_a_limit(self):
        p = self.proj("A")
        spend(p, 3.5, asset="hero")
        calls = []
        client = NS(responses=NS(create=lambda **kw: calls.append(kw)))
        mcp = NS(tools=lambda: [], call=lambda *a: ("", [], False))
        sess = AgentSession(p, "hero", mcp, client, "gpt-test", self.tmp, log=lambda m: None)
        stop, summary = sess.converse("draw", "x", [{"type": "input_text", "text": "go"}], 10)
        sess.close()
        self.assertTrue(stop.startswith("stopped: this item has used its $3.00 limit"), stop)
        self.assertEqual(calls, [])                            # no model call was made


if __name__ == "__main__":
    unittest.main()
