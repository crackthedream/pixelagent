"""Command line: python -m pixelagent --project DIR <command> ...

  new ID [--ref IMG ...] [--prompt TEXT] [--height N] [--view V] [--type character|prop|tile|scene]
  run ID [--dry-run] [--auto-approve] [--from STEP] [--until STEP]
  status [ID]
  approve ID [--pick N] [--note TEXT] [--stop]      answer the waiting gate (continues the run unless --stop)
  reject ID --note TEXT [--stop]                    redo the gated step with your note
  export ID [--open]                                print final files; --open launches PixelLiner with the PNG
"""
import argparse
import json
import os
import subprocess
import sys

from .pipeline import Pipeline
from .store import Project


def _project(args):
    mode = "dry_run" if getattr(args, "dry_run", False) else getattr(args, "backend", None)
    p = Project(args.project, {"backend": {"mode": mode}} if mode else None)
    if args.cmd in ("run", "approve", "reject", "animate"):
        print(f"backend: {p.config['backend']['mode']}" + (" (stub models, no API calls)" if p.config["backend"]["mode"] == "dry_run" else ""))
    return p


def cmd_new(args):
    p = _project(args)
    inputs = {"prompt": args.prompt, "references": args.ref or [], "height": args.height, "view": args.view,
              "asset_type": args.type, "name": args.name}
    if not inputs["prompt"] and not inputs["references"]:
        sys.exit("give --prompt and/or --ref")
    a = p.new_asset(args.id, inputs)
    print(f"created asset '{a.id}' in {a.dir}")


def cmd_run(args):
    p = _project(args)
    pipe = Pipeline(p, args.id)
    if args.from_step:
        pipe.reset_from(args.from_step)
    pipe.run(auto_approve=args.auto_approve, until=args.until)


def cmd_pack(args):
    from . import gamepack
    folder, zpath, summary = gamepack.build(_project(args), args.id)
    print(f"game pack: {zpath}")
    print(f"  {summary['frames']} frames in {summary['cell'][0]}x{summary['cell'][1]} cells, "
          f"pivot {summary['pivot']}; animations: {', '.join(summary['animations'])}")


def cmd_status(args):
    p = _project(args)
    ids = [args.id] if args.id else p.asset_ids()
    for i in ids:
        pipe = Pipeline(p, i)
        st = pipe.asset.state
        print(f"{i}: {st.status}  (cost ${p.cost_total(i):.2f})")
        for s in pipe.steps:
            rec = st.steps.get(s.name)
            status = rec.status if rec else "pending"
            extra = f" v{rec.version:03d}" if rec and rec.version else ""
            print(f"  {s.index:2d}. {s.name:<10} {status}{extra}" + (f"  !! {rec.error}" if rec and rec.error else ""))


def cmd_approve(args):
    p = _project(args)
    pipe = Pipeline(p, args.id)
    s = pipe.decide(True, args.pick, args.note or "")
    print(f"gate {s.gate} approved")
    if not args.stop:
        pipe.run()


def cmd_reject(args):
    p = _project(args)
    pipe = Pipeline(p, args.id)
    s = pipe.decide(False, None, args.note)
    print(f"gate {s.gate} rejected; step '{s.redo_from or s.name}' will run again with your note")
    if not args.stop:
        pipe.run()


def cmd_export(args):
    p = _project(args)
    pipe = Pipeline(p, args.id)
    rec = pipe.asset.state.steps.get("export")
    if not rec or rec.status != "done":
        sys.exit("export has not run yet (finish the pipeline first)")
    for k, v in rec.outputs.items():
        print(f"{k}: {pipe.asset.path(v)}")
    if args.open:
        exe = p.config["engine"]["pixelliner_exe"]
        png = pipe.asset.path(rec.outputs["png"])
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        subprocess.Popen([exe, png], cwd=os.path.dirname(exe), creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"opened in PixelLiner: {png}")


def cmd_animate(args):
    p = _project(args)
    p.asset(args.character)  # must exist
    aid = f"{args.character}-{args.anim}"
    inputs = {"kind": "animation", "character": args.character, "anim": args.anim, "prompt": args.prompt,
              "frames": args.frames}
    if aid not in p.asset_ids():
        p.new_asset(aid, inputs)
        print(f"created animation asset '{aid}'")
    Pipeline(p, aid).run(auto_approve=args.auto_approve)


def cmd_artist(args):
    from .artist import run_artist
    p = _project(args)
    summary = run_artist(p, args.id, args.ref, args.height, max_usd=args.max_usd, max_turns=args.max_turns,
                         effort=args.effort, model=args.model)
    print(json.dumps({k: v for k, v in summary.items() if k != "lint"}, indent=2, ensure_ascii=False))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # non-ASCII paths on Windows consoles
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="pixelagent", description="Reference/prompt -> pixel-art sprite pipeline")
    ap.add_argument("--project", default=".", help="project folder (default: current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new")
    s.add_argument("id")
    s.add_argument("--ref", action="append")
    s.add_argument("--prompt")
    s.add_argument("--height", type=int)
    s.add_argument("--view", choices=["front", "three_quarter", "side", "back"])
    s.add_argument("--type", default="character", choices=["character", "prop", "tile", "scene"])
    s.add_argument("--name")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("run")
    s.add_argument("id")
    s.add_argument("--dry-run", action="store_true", help="stub models, no API calls")
    s.add_argument("--backend", choices=["dry_run", "openai"], help="override [backend] mode for this command")
    s.add_argument("--auto-approve", action="store_true", help="approve every gate with the suggested choice")
    s.add_argument("--from", dest="from_step")
    s.add_argument("--until")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("pack", help="a finished character's game pack: atlas, Aseprite JSON, Godot SpriteFrames")
    s.add_argument("id")
    s.set_defaults(fn=cmd_pack)

    s = sub.add_parser("status")
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=cmd_status)

    for name, fn in (("approve", cmd_approve), ("reject", cmd_reject)):
        s = sub.add_parser(name)
        s.add_argument("id")
        if name == "approve":
            s.add_argument("--pick", type=int)
            s.add_argument("--note")
        else:
            s.add_argument("--note", required=True)
        s.add_argument("--stop", action="store_true", help="record the decision without continuing the run")
        s.add_argument("--dry-run", action="store_true")
        s.add_argument("--backend", choices=["dry_run", "openai"])
        s.set_defaults(fn=fn)

    s = sub.add_parser("animate", help="animate an approved character from your animation prompt")
    s.add_argument("character", help="id of a finished character asset")
    s.add_argument("anim", help="short name for this animation, e.g. walk, attack")
    s.add_argument("--prompt", required=True, help='what should happen, e.g. "6-frame walk cycle, in place"')
    s.add_argument("--frames", type=int, help="exact frame count (otherwise the plan decides)")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--backend", choices=["dry_run", "openai"])
    s.add_argument("--auto-approve", action="store_true")
    s.set_defaults(fn=cmd_animate)

    s = sub.add_parser("artist", help="the model draws the sprite itself via the pixelliner MCP tools")
    s.add_argument("id")
    s.add_argument("--ref", required=True, help="reference image (concept art)")
    s.add_argument("--height", type=int, default=96)
    s.add_argument("--max-usd", type=float, default=3.0)
    s.add_argument("--max-turns", type=int, default=60)
    s.add_argument("--effort", default="medium")
    s.add_argument("--model")
    s.set_defaults(fn=cmd_artist)

    s = sub.add_parser("export")
    s.add_argument("id")
    s.add_argument("--open", action="store_true")
    s.set_defaults(fn=cmd_export)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
