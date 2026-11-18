"""A model working through the pixelliner MCP tools: the tool-calling conversation loop shared by the artist
(single sprites) and the animator (one conversation per frame).

Each converse() call runs the Responses API in a loop: the model's function calls are executed on the MCP server,
results (and images) are sent back, until the model calls `finish`, answers without tools, or hits the turn/spend
limits. Costs go to the project's costs.jsonl; every call goes to a transcript."""
import json
import os
import time

from .openai_backend import image_data_url
from .metering import check_usage_guard, price_profile, record_usage, request_error, response_settings, usage_price

DEFAULT_TOOLS = ("palette", "draw", "layer", "view", "lint", "undo", "redo", "compare_reference", "canvas")


def interrupted(reason):
    """The loop stopped before completing its phase (shared by phase and pipeline callers)."""
    return reason == "cancelled" or reason.startswith("stopped:")


def _schema(tool):
    s = dict(tool.get("inputSchema") or {"type": "object", "properties": {}})
    s.setdefault("type", "object")
    s.setdefault("properties", {})
    return s


class AgentSession:
    def __init__(self, project, asset_id, mcp, client, model, out_dir, effort="medium", allowed=DEFAULT_TOOLS,
                 max_usd=3.0, log=print, transcript_name="transcript.jsonl", on_round=None):
        self.project, self.asset_id, self.mcp, self.client = project, asset_id, mcp, client
        self.model, self.out, self.effort, self.allowed = model, out_dir, effort, tuple(allowed)
        self.max_usd, self.log = max_usd, log
        self.on_round = on_round  # called as on_round(session, phase) after each round of tool calls (live preview)
        self.spent, self.turns, self.prev, self.pending = 0.0, 0, None, []
        self._response_model = None
        self._metering_state = {}
        self.transcript = open(os.path.join(out_dir, transcript_name), "a", encoding="utf-8")
        self.tools = [{"type": "function", "name": t["name"], "description": t["description"][:1024],
                       "parameters": _schema(t), "strict": False} for t in mcp.tools() if t["name"] in self.allowed]
        self.tools.append({"type": "function", "name": "finish", "strict": False,
                           "description": "Call when the current task is finished. Give a short summary of what you drew.",
                           "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}}})

    def close(self):
        self.transcript.close()

    def _price(self, usage):
        return usage_price(price_profile(self.project.config, self.model, "llm"), usage, "llm")

    def save_imgs(self, imgs, tag):
        """One file per image. The name carries a per-session sequence number: two calls of the same tool in one
        round (e.g. two `view`s) used to share 't012_view0.png', so the model saw the second image twice."""
        paths = []
        for k, png in enumerate(imgs):
            while True:
                self._img_seq = getattr(self, "_img_seq", 0) + 1
                p = os.path.join(self.out, f"t{self.turns:03d}_{self._img_seq:04d}_{tag}{k}.png")
                if not os.path.exists(p):
                    break
            with open(p, "wb") as f:
                f.write(png)
            paths.append(p)
        return paths

    @staticmethod
    def image_item(path):
        return {"type": "input_image", "image_url": image_data_url(path, 1024), "detail": "high"}

    def _reference_layers(self):
        """Names and indices of the canvas's reference (tracing) layers, read from the engine."""
        txt, _, _ = self.mcp.call("canvas", {"action": "info"})
        refs, n = set(), 0
        for line in txt.splitlines():
            line = line.strip().lstrip("*")
            if not line.startswith("#"):
                continue
            n += 1
            idx, _, rest = line[1:].partition(" ")
            name = rest.split(" (")[0]
            if "reference" in rest.partition(" (")[2] or name == "reference":
                refs.update({name, idx})
        return refs, n

    def _is_ref(self, ref, refs, n):
        if ref is None:
            return False
        s = str(ref).strip()
        if s.lstrip("-").isdigit() and int(s) < 0:
            s = str(int(s) + n)  # -1 = last layer, as the engine reads it
        return s in refs

    def guard(self, name, args):
        """Return an error text if a call must be refused, else None."""
        if name not in self.allowed:
            return f"error: tool '{name}' is not available"
        if name == "layer" and ("reference" in args or args.get("action") == "merge_down"):
            # the tracing layer must stay a reference: turning it (or merging into it) into real pixels would
            # export the converted trace instead of the model's own drawing
            return "error: changing the reference flag and merge_down are disabled here; draw on your own layers"
        if name == "draw":
            # the final pixels must be the model's own: no copying from the tracing layer, no drawing on it (F08)
            ops = args.get("ops") if isinstance(args.get("ops"), list) else []
            if any(isinstance(o, dict) and o.get("from_layer") is not None for o in ops) or args.get("layer") is not None:
                refs, n = self._reference_layers()
                if self._is_ref(args.get("layer"), refs, n):
                    return "error: the reference layer is a guide only; draw on your own layers"
                if any(isinstance(o, dict) and self._is_ref(o.get("from_layer"), refs, n) for o in ops):
                    return ("error: copying pixels from the reference layer is not allowed; read it with `view` "
                            "mode 'grid' and draw the pixels yourself")
        return None

    def converse(self, phase, instructions, content, limit, fresh=False):
        """Run the tool loop. content: list of input_text/input_image parts for a new user message.
        fresh=True starts a new conversation (no earlier context: cheaper for independent tasks such as frames).
        Returns (stop reason, finish summary)."""
        if fresh:
            self.prev, self.pending = None, []
        if self.prev and self._response_model != self.model:
            raise ValueError("The drawing model changed; start a fresh session before continuing this conversation.")
        items = list(self.pending) + [{"role": "user", "content": content}]
        self.pending = []
        n, finished = 0, None
        from . import cancel
        from .models import budget_problem
        while n < limit:
            if cancel.requested(self.project.root, self.asset_id):  # the user cancelled: stop before the next call
                self.pending = items if n else []
                return "cancelled", None
            if self.spent >= self.max_usd:
                self.pending = items if n else []
                return f"stopped: local budget ${self.max_usd:.2f} reached", None
            problem = budget_problem(self.project, self.asset_id)
            if problem:  # stop cleanly: the drawing so far is kept and exported by the caller
                self.pending = items if n else []
                return f"stopped: {problem}", None
            pricing = price_profile(self.project.config, self.model, "llm")
            check_usage_guard(self._metering_state)
            n += 1
            self.turns += 1
            # Phase instructions change between draw/face/revision. In 6.2 they are appended to the
            # current input and are NOT inherited through previous_response_id: resend every round.
            kwargs = dict(model=self.model, instructions=instructions, input=items, tools=self.tools,
                          parallel_tool_calls=True, **response_settings(self.model, self.effort))
            if self.prev:
                kwargs["previous_response_id"] = self.prev
            t = time.perf_counter()
            try:
                resp = self.client.responses.create(**kwargs)
            except Exception as e:
                self.project.log_cost({"asset": self.asset_id, "kind": "llm", "role": "artist", "phase": phase,
                                       "model": self.model, "usd": 0.0, "error": type(e).__name__})
                raise request_error(e) from None
            self.prev = resp.id
            self._response_model = self.model
            usage = getattr(resp, "usage", None)
            usd = record_usage(self.project, self.asset_id, self.model, "llm", "artist", usage, pricing,
                               guard=self._metering_state, phase=phase, service_tier=getattr(resp, "service_tier", None),
                               ms=int((time.perf_counter() - t) * 1000))
            self.spent += usd
            calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
            said = " ".join(getattr(c, "text", "") for o in resp.output if getattr(o, "type", None) == "message"
                            for c in getattr(o, "content", []) or [])
            if not calls:
                self.transcript.write(json.dumps({"phase": phase, "turn": self.turns, "message": said[:2000]},
                                                 ensure_ascii=False) + "\n")
                return "model answered without tool calls", said[:500]
            items, images = [], []
            for c in calls:
                try:
                    args = json.loads(c.arguments or "{}")
                except json.JSONDecodeError as e:
                    args, txt, imgs, err = {}, f"error: arguments are not valid JSON ({e})", [], True
                else:
                    if c.name == "finish":
                        finished = args.get("summary", "")
                        txt, imgs, err = "ok", [], False
                    else:
                        refusal = self.guard(c.name, args)
                        if refusal:
                            txt, imgs, err = refusal, [], True
                        else:
                            txt, imgs, err = self.mcp.call(c.name, args)
                images += self.save_imgs(imgs, c.name)
                items.append({"type": "function_call_output", "call_id": c.call_id,
                              "output": txt[:6000] + (f"\n({len(imgs)} image(s) attached below)" if imgs else "")})
                self.transcript.write(json.dumps({"phase": phase, "turn": self.turns, "tool": c.name,
                                                  "args": json.dumps(args)[:1500], "error": err, "result": txt[:600],
                                                  "images": len(imgs), "usd": round(usd, 5)}, ensure_ascii=False) + "\n")
            self.transcript.flush()
            if images:
                items.append({"role": "user", "content": [{"type": "input_text", "text": "Images from your tool calls:"}] +
                              [self.image_item(p) for p in images]})
            self.log(f"[artist {self.asset_id} {phase}] turn {self.turns}: {', '.join(c.name for c in calls)}  "
                     f"(${self.spent:.3f})")
            if self.on_round and any(c.name in ("draw", "undo", "redo", "layer") for c in calls):
                try:
                    self.on_round(self, phase)
                except Exception:
                    pass
            if finished is not None:
                self.pending = items  # outputs for the last calls go with the next message of this conversation
                return "finished", finished
            if self.spent >= self.max_usd:
                self.pending = items
                return f"stopped: local budget ${self.max_usd:.2f} reached", None
        self.pending = items
        return f"stopped: turn limit {limit} reached", None
