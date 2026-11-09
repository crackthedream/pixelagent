"""Minimal MCP client over stdio (JSON-RPC 2.0, newline-delimited) for the pixelliner server.

The artist agent talks to the engine exactly like any MCP host would: initialize, tools/list, tools/call."""
import json
import os
import subprocess
import sys
import threading


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, server_path, env=None, python=None):
        full_env = dict(os.environ)
        full_env.update(env or {})
        full_env["PYTHONIOENCODING"] = "utf-8"
        # in the bundled exe there is no python.exe: the exe itself serves MCP when started with --mcp-server
        cmd = [sys.executable, "--mcp-server"] if getattr(sys, "frozen", False) else [python or sys.executable, "-I", server_path]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env)
        self._id = 0
        self._lock = threading.Lock()
        self.stderr_lines = []
        threading.Thread(target=self._drain, daemon=True).start()
        init = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "pixelagent-artist", "version": "0.1"}})
        self.server_info = init.get("serverInfo", {})
        self.notify("notifications/initialized")

    def _drain(self):
        for line in self.proc.stderr:
            self.stderr_lines.append(line.decode("utf-8", "replace"))
            del self.stderr_lines[:-200]

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method, params=None):
        with self._lock:
            self._id += 1
            rid = self._id
            msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
            self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        if not line:
            raise MCPError("MCP server closed the connection:\n" + "".join(self.stderr_lines[-20:]))
        resp = json.loads(line.decode("utf-8"))
        if resp.get("id") != rid:
            raise MCPError(f"unexpected response id {resp.get('id')} (expected {rid})")
        if "error" in resp:
            raise MCPError(resp["error"].get("message", str(resp["error"])))
        return resp.get("result", {})

    def tools(self):
        return self.request("tools/list").get("tools", [])

    def call(self, name, arguments):
        """Returns (text, [png bytes...], is_error)."""
        import base64
        res = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        texts, images = [], []
        for c in res.get("content", []):
            if c.get("type") == "text":
                texts.append(c["text"])
            elif c.get("type") == "image":
                images.append(base64.b64decode(c["data"]))
        return "\n".join(texts), images, bool(res.get("isError"))

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
