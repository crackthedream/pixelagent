import os
import sys


def _stdio_for_windowed_exe(mcp_child):
    """A windowed PixelAgent.exe starts without a console, so sys.stdout/stderr can be None."""
    if mcp_child:
        # the engine talks JSON-RPC over the pipes its parent gave it (it uses sys.stdin.buffer / sys.stdout.buffer)
        if sys.stdin is None:
            sys.stdin = open(0, "r", encoding="utf-8", closefd=False)
        if sys.stdout is None:
            sys.stdout = open(1, "w", encoding="utf-8", newline="\n", closefd=False)
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        return
    if sys.stdout is None or sys.stderr is None:
        d = os.path.join(os.path.expanduser("~"), ".pixelagent")
        os.makedirs(d, exist_ok=True)
        log = open(os.path.join(d, "app.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


if "--mcp-server" in sys.argv:  # used by the bundled exe to run the pixelliner engine as an MCP server
    _stdio_for_windowed_exe(True)
    from pixelagent.config import load_config, load_engine
    load_engine(load_config())
    import server as _engine_server
    _engine_server.main()
else:
    _stdio_for_windowed_exe(False)
    from pixelagent.app.server import main
    main()
