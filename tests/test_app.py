"""The local app's HTTP API, against a real server on a free port, in dry-run mode (no model calls, no cost)."""
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from pixelagent import store
from pixelagent.app import server
from pixelagent.store import Project


class AppServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pixelagent-app-")
        cls.projects = os.path.join(cls.tmp, "projects")
        cls.patch = mock.patch.object(server, "SETTINGS_FILE", os.path.join(cls.tmp, "settings.json"))
        cls.patch.start()
        server.save_settings({**server.default_settings(), "projects_dir": cls.projects, "backend": "dry_run"})
        cls.httpd = server.AppServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.patch.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body, **kw):
        return self.request("POST", path, body, **kw)

    def ok(self, result):
        status, data = result
        self.assertEqual(status, 200, data)
        return data

    def asset(self, pid, aid):
        from urllib.parse import urlencode
        return self.ok(self.get("/api/asset?" + urlencode({"project": pid, "id": aid})))

    def wait_idle(self, pid, aid, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            d = self.asset(pid, aid)
            if not d["running"]:
                self.assertIsNone(d["job_error"], d["log"][-5:])
                return d
            time.sleep(0.2)
        self.fail(f"{aid} still running after {timeout}s")

    def new_project(self, name):
        self.ok(self.post("/api/projects", {"name": name}))
        return name

    # -- tests

    def test_page_and_static(self):
        status, html = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>PixelAgent</title>", html)
        self.assertEqual(self.get("/static/app.js")[0], 200)
        self.assertIn(self.get("/static/..%2f..%2fserver.py")[0], (403, 404))

    def test_full_character_flow(self):
        # a project name that needs URL encoding in every file link
        pid = self.new_project("Cast & Props #1")
        aid = self.ok(self.post("/api/characters", {"project": pid, "prompt": "a lantern keeper with a blue scarf",
                                                    "name": "Lantern Keeper", "height": 64}))["id"]
        d = self.wait_idle(pid, aid)
        g = d["gate_detail"]
        self.assertEqual(g["kind"], "spec")

        # gate A: edit the plan, then continue
        spec = dict(g["spec"], name="Lamp Keeper")
        self.ok(self.post("/api/decide", {"project": pid, "id": aid, "approve": True, "spec": spec}))
        d = self.wait_idle(pid, aid)
        self.assertEqual(d["name"], "Lamp Keeper")
        g = d["gate_detail"]
        self.assertEqual(g["kind"], "concept")
        self.assertGreaterEqual(len(g["candidates"]), 2)
        status, png = self.get(g["candidates"][0])  # the URL works with "&" and "#" in the project name
        self.assertEqual(status, 200)
        self.assertTrue(png.startswith(b"\x89PNG"))

        # gate B: pick, then the sprite review (gate C), then export
        self.ok(self.post("/api/decide", {"project": pid, "id": aid, "approve": True, "pick": 1}))
        d = self.wait_idle(pid, aid)
        self.assertEqual(d["gate_detail"]["kind"], "sprite")
        self.ok(self.post("/api/decide", {"project": pid, "id": aid, "approve": True}))
        d = self.wait_idle(pid, aid)
        self.assertEqual(d["status"], "complete")
        self.assertFalse(d["paused"])
        self.assertTrue(all(s["status"] == "done" for s in d["steps"]), d["steps"])
        png = [e for e in d["exports"] if e["key"] == "png"][0]
        self.assertEqual(self.get(png["url"])[0], 200)

        items = self.ok(self.get("/api/assets?project=Cast%20%26%20Props%20%231"))["items"]
        self.assertEqual([i["id"] for i in items], [aid])

    def test_decide_needs_a_waiting_gate_and_a_note_to_reject(self):
        pid = self.new_project("Decisions")
        aid = self.ok(self.post("/api/characters", {"project": pid, "prompt": "a small robot"}))["id"]
        self.wait_idle(pid, aid)
        status, data = self.post("/api/decide", {"project": pid, "id": aid, "approve": False})
        self.assertEqual(status, 400)
        self.assertIn("what should change", data["error"])
        # an invalid edited plan is refused before anything is written, and the gate stays open
        status, data = self.post("/api/decide", {"project": pid, "id": aid, "approve": True, "spec": {"name": 3}})
        self.assertEqual(status, 400, data)
        self.assertEqual(self.asset(pid, aid)["gate_detail"]["kind"], "spec")

    def test_create_needs_a_prompt_or_reference(self):
        pid = self.new_project("Empty")
        status, data = self.post("/api/characters", {"project": pid, "prompt": "  "})
        self.assertEqual(status, 400)
        status, data = self.post("/api/characters", {"project": pid, "reference": "data:text/html;base64,PGI+"})
        self.assertEqual(status, 400)

    def test_project_names_cannot_escape_the_projects_folder(self):
        for bad in ("..", ".", " ..", "a/b", "a\\b", "C:", "...", "x\x00y", ""):
            status, _ = self.post("/api/projects", {"name": bad})
            self.assertEqual(status, 400, bad)
        status, _ = self.get("/files?project=..&path=settings.json")
        self.assertEqual(status, 400)
        pid = self.new_project("Files")
        status, _ = self.get(f"/files?project={pid}&path=..%2F..%2Fsettings.json")
        self.assertEqual(status, 403)

    def test_cross_site_and_rebound_requests_are_refused(self):
        self.new_project("Guarded")
        # another web page posting to the app
        status, _ = self.post("/api/projects", {"name": "Evil"}, headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        # DNS rebinding: evil.example re-resolved to 127.0.0.1 -> Host and Origin agree, but are not loopback
        rebound = {"Host": f"evil.example:{self.port}", "Origin": f"http://evil.example:{self.port}"}
        self.assertEqual(self.post("/api/projects", {"name": "Evil"}, headers=rebound)[0], 403)
        self.assertEqual(self.get("/api/overview", headers={"Host": f"evil.example:{self.port}"})[0], 403)
        self.assertNotIn("Evil", self.ok(self.get("/api/overview"))["projects"])
        # the app's own page (localhost spelling too) is fine
        own = {"Host": f"localhost:{self.port}", "Origin": f"http://localhost:{self.port}"}
        self.assertEqual(self.post("/api/current", {"project": "Guarded"}, headers=own)[0], 200)

    def test_settings_are_validated(self):
        self.assertEqual(self.post("/api/settings", {"backend": "something"})[0], 400)
        self.assertEqual(self.post("/api/settings", {"monthly_usd": "lots"})[0], 400)
        self.assertEqual(self.post("/api/settings", {"sprite_height": 100})[0], 400)
        self.assertEqual(self.post("/api/settings", {"monthly_usd": -5})[0], 400)
        self.ok(self.post("/api/settings", {"monthly_usd": "45", "sprite_height": 128}))
        s = self.ok(self.get("/api/overview"))["settings"]
        self.assertEqual((s["monthly_usd"], s["sprite_height"]), (45.0, 128))
        self.ok(self.post("/api/settings", {"monthly_usd": 90, "sprite_height": 160}))   # the AIC-scale default
        o = self.ok(self.get("/api/overview"))
        self.assertEqual(o["settings"]["look"], "soft")                                   # the soft look by default
        self.assertEqual(o["estimates"]["character"]["160"], 0.20)                        # pixel art, no retry
        self.ok(self.post("/api/settings", {"look": "classic"}))
        self.assertEqual(self.ok(self.get("/api/overview"))["estimates"]["character"]["160"], 1.50)
        self.assertEqual(self.post("/api/settings", {"look": "fancy"})[0], 400)
        self.ok(self.post("/api/settings", {"look": "soft"}))
        self.assertEqual(self.ok(self.get("/api/overview"))["settings"]["grade"], "medium")   # dusty colours by default
        self.assertEqual(self.post("/api/settings", {"grade": "neon"})[0], 400)
        self.ok(self.post("/api/settings", {"grade": "strong"}))
        self.assertEqual(self.ok(self.get("/api/overview"))["settings"]["grade"], "strong")
        from pixelagent.app.server import overrides
        self.assertEqual(overrides({**self.ok(self.get("/api/overview"))["settings"], "grade": "none"})["finish"],
                         {"mode": "native", "grade": "none"})
        self.ok(self.post("/api/settings", {"grade": "medium"}))
        self.assertEqual(self.ok(self.get("/api/overview"))["settings"]["sprite_height"], 160)
        self.ok(self.post("/api/settings", {"monthly_usd": 90, "sprite_height": 96}))

    def test_animations_are_listed_by_parent_not_by_id_prefix(self):
        pid = self.new_project("Prefixes")
        proj = Project(os.path.join(self.projects, pid))
        proj.new_asset("knight", {"prompt": "a knight"})
        proj.new_asset("knight-2", {"prompt": "another knight"})
        proj.new_asset("knight-2-walk", {"kind": "animation", "character": "knight-2", "prompt": "walk"})
        self.assertEqual(self.asset(pid, "knight")["animations"], [])
        self.assertEqual(self.asset(pid, "knight-2")["animations"], ["knight-2-walk"])

    def test_an_interrupted_run_shows_as_paused(self):
        pid = self.new_project("Paused")
        proj = Project(os.path.join(self.projects, pid))
        proj.new_asset("idle", {"prompt": "a cat"})  # created, never run (as if the app closed)
        d = self.asset(pid, "idle")
        self.assertTrue(d["paused"])
        self.assertIsNone(d["gate"])
        self.ok(self.post("/api/run", {"project": pid, "id": "idle"}))
        d = self.wait_idle(pid, "idle")
        self.assertFalse(d["paused"])
        self.assertEqual(d["gate"], "intake")

    def test_one_app_per_port(self):
        # a second launch must find this one instead of silently sharing the port (SO_REUSEADDR on Windows)
        self.assertTrue(server.running_instance(self.port))
        with self.assertRaises(OSError):
            server.AppServer(("127.0.0.1", self.port), server.Handler)
        self.assertEqual(self.ok(self.get("/api/ping")), {"ok": True})
        server.LAST_SEEN[0] = 0
        self.get("/api/ping")
        self.assertGreater(server.LAST_SEEN[0], time.time() - 5)

    def drive(self, pid, aid, pick=0):
        """Approve every gate (picking the first concept) until the item is complete."""
        for _ in range(8):
            d = self.wait_idle(pid, aid)
            g = d.get("gate_detail")
            if not g:
                return d
            body = {"project": pid, "id": aid, "approve": True}
            if g["kind"] == "concept":
                body["pick"] = pick
            self.ok(self.post("/api/decide", body))
        self.fail("too many gates")

    def test_character_from_a_reference_image(self):
        import base64
        from PIL import Image
        import io
        buf = io.BytesIO()
        im = Image.new("RGBA", (80, 120), (255, 255, 255, 255))  # a figure on a plain background, like a real reference
        im.paste((200, 60, 50, 255), (25, 15, 55, 105))
        im.paste((240, 220, 190, 255), (32, 22, 48, 40))
        im.save(buf, "PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        pid = self.new_project("References")
        aid = self.ok(self.post("/api/characters", {"project": pid, "reference": data_url, "prompt": "keep the red",
                                                    "name": "Red Block"}))["id"]
        d = self.wait_idle(pid, aid)
        # before any concept exists, the reference is what the stage shows, and it is linked in the details
        self.assertIsNotNone(d["reference"])
        self.assertEqual(d["hero"], d["reference"])
        self.assertEqual(self.get(d["reference"])[0], 200)
        d = self.drive(pid, aid)
        self.assertEqual(d["status"], "complete")

    def test_animation_review_has_frames_and_a_1x_preview(self):
        pid = self.new_project("Motion")
        cid = self.ok(self.post("/api/characters", {"project": pid, "prompt": "a small knight"}))["id"]
        self.assertEqual(self.drive(pid, cid)["status"], "complete")
        aid = self.ok(self.post("/api/animations", {"project": pid, "character": cid, "prompt": "4-frame idle",
                                                    "name": "idle", "frames": 4}))["id"]
        d = self.wait_idle(pid, aid)
        self.assertEqual(d["gate_detail"]["kind"], "poses")
        self.ok(self.post("/api/decide", {"project": pid, "id": aid, "approve": True}))
        d = self.wait_idle(pid, aid)
        g = d["gate_detail"]
        self.assertEqual(g["kind"], "animation")
        self.assertTrue(g["image"].endswith("preview_1x.gif"), g["image"])
        self.assertEqual(len(g["frames"]), 4)
        self.assertTrue(all(f["ms"] for f in g["frames"]))
        status, png = self.get(g["frames"][0]["url"])
        self.assertEqual(status, 200)
        self.assertEqual(png[1:4], b"PNG")
        # redraw with a note keeps the poses and draws a new version of the frames
        self.ok(self.post("/api/decide", {"project": pid, "id": aid, "approve": False, "note": "more bounce"}))
        d = self.wait_idle(pid, aid)
        self.assertIn("v002", d["gate_detail"]["image"])
        d = self.drive(pid, aid)
        self.assertEqual(d["status"], "complete")
        self.assertEqual(self.asset(pid, cid)["animations"], [aid])

    def test_rename_duplicate_and_delete_items(self):
        pid = self.new_project("Manage")
        proj = Project(os.path.join(self.projects, pid))
        proj.new_asset("hero", {"prompt": "a hero"})
        proj.new_asset("hero-walk", {"kind": "animation", "character": "hero", "prompt": "walk", "anim": "walk"})
        self.ok(self.post("/api/item/rename", {"project": pid, "id": "hero", "name": "Brave Hero"}))
        self.assertEqual(self.asset(pid, "hero")["name"], "Brave Hero")
        self.assertEqual(self.post("/api/item/rename", {"project": pid, "id": "hero", "name": "  "})[0], 400)
        # duplicate: a new item with its own id and a "copy" name; animations are not copied
        new = self.ok(self.post("/api/item/duplicate", {"project": pid, "id": "hero"}))["id"]
        self.assertEqual(new, "hero-copy")
        d = self.asset(pid, new)
        self.assertEqual((d["id"], d["name"]), ("hero-copy", "Brave Hero copy"))
        self.assertEqual(d["animations"], [])
        # deleting a character with animations needs an explicit yes, then both go to the trash
        status, data = self.post("/api/item/delete", {"project": pid, "id": "hero"})
        self.assertEqual(status, 400)
        self.assertIn("animation", data["error"])
        self.ok(self.post("/api/item/delete", {"project": pid, "id": "hero", "with_animations": True}))
        ids = [i["id"] for i in self.ok(self.get("/api/assets?project=Manage"))["items"]]
        self.assertEqual(ids, ["hero-copy"])
        # the character and its animation are one entry in Recently Deleted
        entries = [e for e in self.ok(self.get("/api/trash"))["items"] if e["project"] == "Manage"]
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual((e["kind"], e["name"], e["animations"]), ("items", "Brave Hero", 1))
        # a deleted item is a clean 404 for the app to explain
        status, data = self.get("/api/asset?project=Manage&id=hero")
        self.assertEqual((status, data.get("not_found")), (404, True))
        # restore brings both back, the animation still linked to its character
        r = self.ok(self.post("/api/trash/restore", {"entry": e["entry"]}))
        self.assertEqual(r["id"], "hero")
        self.assertEqual(self.asset(pid, "hero")["animations"], ["hero-walk"])
        self.assertEqual([x for x in self.ok(self.get("/api/trash"))["items"] if x["project"] == "Manage"], [])

    def test_restore_never_overwrites(self):
        pid = self.new_project("Clash")
        proj = Project(os.path.join(self.projects, pid))
        proj.new_asset("hero", {"prompt": "first"})
        self.ok(self.post("/api/item/delete", {"project": pid, "id": "hero"}))
        proj.new_asset("hero", {"prompt": "second"})              # a new item took the same id meanwhile
        e = [x for x in self.ok(self.get("/api/trash"))["items"] if x["project"] == pid][0]
        r = self.ok(self.post("/api/trash/restore", {"entry": e["entry"]}))
        self.assertEqual(r["id"], "hero-2")
        self.assertEqual(proj.asset("hero").state.inputs["prompt"], "second")
        self.assertEqual(proj.asset("hero-2").state.inputs["prompt"], "first")
        self.assertEqual(proj.asset("hero-2").state.id, "hero-2")

    def test_delete_now_and_empty_are_permanent_and_explicit(self):
        pid = self.new_project("Bin")
        proj = Project(os.path.join(self.projects, pid))
        for n in ("one", "two", "three"):
            proj.new_asset(n, {"prompt": n})
            self.ok(self.post("/api/item/delete", {"project": pid, "id": n}))
        mine = lambda: [x for x in self.ok(self.get("/api/trash"))["items"] if x["project"] == pid]  # noqa: E731
        self.assertEqual(len(mine()), 3)
        self.ok(self.post("/api/trash/delete", {"entry": mine()[0]["entry"]}))
        self.assertEqual(len(mine()), 2)
        self.assertEqual(self.post("/api/trash/delete", {"entry": "../../projects"})[0], 400)  # never a path
        removed = self.ok(self.post("/api/trash/empty", {}))["removed"]
        self.assertGreaterEqual(removed, 2)
        self.assertEqual(mine(), [])
        self.assertEqual(self.ok(self.get("/api/overview"))["trash_count"], 0)

    def test_revise_a_finished_character_and_an_area(self):
        pid = self.new_project("Revise")
        aid = self.ok(self.post("/api/characters", {"project": pid, "prompt": "a small knight"}))["id"]
        self.assertEqual(self.drive(pid, aid)["status"], "complete")
        d = self.asset(pid, aid)
        self.assertTrue(d["can_revise"])
        self.assertEqual(self.post("/api/fix", {"project": pid, "id": aid, "note": " "})[0], 400)
        self.ok(self.post("/api/fix", {"project": pid, "id": aid, "note": "brighter eyes", "box": [3, 4, 5, 6]}))
        d = self.wait_idle(pid, aid)
        self.assertEqual(d["gate"], "review")                         # the revision comes back for approval
        self.assertEqual(len(d["versions"]["items"]), 2)               # as a new version; the old one is kept
        a = Project(os.path.join(self.projects, pid)).asset(aid)
        self.assertNotIn("fix", a.state.inputs)
        self.assertEqual(a.state.steps["artist"].info["revised"]["box"], [3, 4, 5, 6])
        # at the approval, a note with an area goes the same way (no full redraw)
        self.ok(self.post("/api/decide", {"project": pid, "id": aid, "approve": False, "note": "again", "box": [0, 0, 4, 4]}))
        d = self.wait_idle(pid, aid)
        self.assertEqual(len(d["versions"]["items"]), 3)
        # animations cannot be revised this way
        self.ok(self.post("/api/decide", {"project": pid, "id": aid, "approve": True}))
        self.wait_idle(pid, aid)
        anim = self.ok(self.post("/api/animations", {"project": pid, "character": aid, "prompt": "idle", "frames": 2}))["id"]
        self.wait_idle(pid, anim)
        self.assertEqual(self.post("/api/fix", {"project": pid, "id": anim, "note": "x"})[0], 400)

    def test_a_cancel_only_applies_to_its_own_run(self):
        from pixelagent import cancel
        pid = self.new_project("Cancel")
        aid = self.ok(self.post("/api/characters", {"project": pid, "prompt": "a robot"}))["id"]
        self.wait_idle(pid, aid)                                       # waiting at the plan
        self.assertEqual(self.post("/api/cancel", {"project": pid, "id": aid})[0], 400)   # nothing is running
        cancel.request(os.path.join(self.projects, pid), aid)          # a stale request from an earlier run
        self.ok(self.post("/api/decide", {"project": pid, "id": aid, "approve": True}))
        d = self.wait_idle(pid, aid)
        self.assertNotEqual(d["status"], "cancelled")                  # the new run was not stopped by it
        self.assertEqual(d["gate"], "concept")

    def test_item_ids_cannot_leave_their_project(self):
        # review finding F03: mutations joined the raw id to the assets folder
        a, b = self.new_project("Home"), self.new_project("Other")
        Project(os.path.join(self.projects, b)).new_asset("hero", {"prompt": "x"})
        for bad in ("../../Other/assets/hero", "..\\..\\Other\\assets\\hero", "C:/Windows", "/etc", "hero/../x", ""):
            for path, extra in (("/api/item/delete", {}), ("/api/item/rename", {"name": "X"}),
                                ("/api/item/duplicate", {}), ("/api/run", {}), ("/api/redo", {"stage": "Plan"})):
                status, _ = self.post(path, {"project": a, "id": bad, **extra})
                self.assertEqual(status, 400, (path, bad))
        self.assertEqual(Project(os.path.join(self.projects, b)).asset_ids(), ["hero"])

    def test_items_that_are_working_cannot_be_changed(self):
        pid = self.new_project("Busy")
        Project(os.path.join(self.projects, pid)).new_asset("runner", {"prompt": "x"})
        stop = threading.Event()
        t = threading.Thread(target=stop.wait, daemon=True)
        t.start()
        try:
            with mock.patch.dict(server.JOBS.running, {(pid, "runner"): t}):
                for path, extra in (("/api/item/rename", {"name": "Y"}), ("/api/item/duplicate", {}),
                                    ("/api/item/delete", {})):
                    status, data = self.post(path, {"project": pid, "id": "runner", **extra})
                    self.assertEqual(status, 400, path)
                    self.assertIn("finished working", data["error"])
                self.assertEqual(self.post("/api/project/delete", {"project": pid})[0], 400)
                self.assertEqual(self.post("/api/project/rename", {"project": pid, "name": "Other"})[0], 400)
        finally:
            stop.set()

    def test_rename_and_delete_projects(self):
        self.new_project("Old name")
        self.new_project("Taken")
        self.ok(self.post("/api/current", {"project": "Old name"}))
        self.assertEqual(self.post("/api/project/rename", {"project": "Old name", "name": "Taken"})[0], 400)
        self.assertEqual(self.post("/api/project/rename", {"project": "Old name", "name": ".."})[0], 400)
        self.ok(self.post("/api/project/rename", {"project": "Old name", "name": "New name"}))
        o = self.ok(self.get("/api/overview"))
        self.assertIn("New name", o["projects"])
        self.assertNotIn("Old name", o["projects"])
        self.assertEqual(o["current"], "New name")  # the open project follows its new name
        self.ok(self.post("/api/project/delete", {"project": "New name"}))
        self.assertNotIn("New name", self.ok(self.get("/api/overview"))["projects"])
        e = [x for x in self.ok(self.get("/api/trash"))["items"] if x["kind"] == "project" and x["name"] == "New name"]
        self.assertEqual(len(e), 1)
        # restoring next to a project of the same name gives it a free name
        self.new_project("New name")
        r = self.ok(self.post("/api/trash/restore", {"entry": e[0]["entry"]}))
        self.assertEqual(r["project"], "New name (2)")
        self.assertIn("New name (2)", self.ok(self.get("/api/overview"))["projects"])


class StoreSaveTest(unittest.TestCase):
    def test_save_retries_while_windows_holds_the_file(self):
        tmp = tempfile.mkdtemp(prefix="pixelagent-store-")
        try:
            a = Project(tmp).new_asset("a", {"prompt": "x"})
            real, calls = os.replace, []

            def flaky(src, dst):
                calls.append(1)
                if len(calls) < 3:
                    raise PermissionError(5, "Access is denied")
                return real(src, dst)
            with mock.patch.object(store.os, "replace", flaky):
                a.state.status = "saved after retries"
                a.save()
            self.assertEqual(len(calls), 3)
            self.assertEqual(Project(tmp).asset("a").state.status, "saved after retries")
            self.assertEqual([n for n in os.listdir(a.dir) if n.endswith(".tmp")], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
