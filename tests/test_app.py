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
