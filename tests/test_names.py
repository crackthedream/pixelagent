"""Display names never become unsafe paths (review finding F04)."""
import os
import shutil
import tempfile
import unittest

from pixelagent.names import safe_filename
from pixelagent.pipeline import Pipeline
from pixelagent.store import Project


class NameTests(unittest.TestCase):
    def test_safe_filename(self):
        cases = {"Knight/Paladin": "Knight_Paladin", "Mage:Blue": "Mage_Blue", "../../evil": "evil",
                 "C:\\Windows\\x": "C__Windows_x", "Lantern Keeper": "Lantern_Keeper", "CON": "CON_",
                 "nul.txt": "nul_txt", "  ..  ": "item", "": "item", "灯笼守护者": "灯笼守护者",
                 "a" * 200: "a" * 60, ".hidden": "hidden"}
        for name, want in cases.items():
            self.assertEqual(safe_filename(name), want, name)

    def test_a_hostile_name_exports_inside_its_folder(self):
        tmp = tempfile.mkdtemp(prefix="pixelagent-names-")
        try:
            proj = Project(os.path.join(tmp, "P"), {"backend": {"mode": "dry_run"}})
            proj.new_asset("hero", {"prompt": "a knight with a red scarf", "name": "../Knight/Paladin:CON"})
            pipe = Pipeline(proj, "hero")
            pipe.run(auto_approve=True, log=lambda m: None)
            a = proj.asset("hero")
            self.assertEqual(a.state.status, "complete")
            png = a.output("export", "png")
            export_dir = os.path.normpath(a.path(a.state.steps["export"].info["dir"]))
            self.assertTrue(os.path.normpath(png).startswith(export_dir + os.sep), png)
            self.assertEqual(os.path.basename(png), "Knight_Paladin_CON.png")
            for root, _, files in os.walk(tmp):  # nothing was written outside the project
                if files:
                    self.assertTrue(os.path.normpath(root).startswith(os.path.normpath(proj.root)), root)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_step_outputs_cannot_leave_their_folder(self):
        tmp = tempfile.mkdtemp(prefix="pixelagent-names-")
        try:
            proj = Project(os.path.join(tmp, "P"), {"backend": {"mode": "dry_run"}})
            proj.new_asset("hero", {"prompt": "x"})
            pipe = Pipeline(proj, "hero")
            from pixelagent.pipeline import Ctx
            ctx = Ctx(proj, pipe.asset, pipe.backend, pipe.steps[-1], os.path.join(tmp, "out"))
            for bad in ("../x.png", "..\\..\\x.png", "/abs/x.png", "C:/x.png"):
                with self.assertRaises(ValueError, msg=bad):
                    ctx.path(bad)
            self.assertTrue(ctx.path("layers/01_body.png").startswith(os.path.normpath(os.path.join(tmp, "out"))))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
