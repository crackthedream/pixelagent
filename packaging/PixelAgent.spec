# PyInstaller spec for PixelAgent.exe: one windowed exe that serves the app UI and, started again with
# --mcp-server, runs the bundled PixelLiner engine. Build with packaging/build.ps1.
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ENGINE = os.path.abspath(os.path.expanduser(os.environ.get("PIXELLINER_ENGINE", "~/pixelliner-mcp")))
ENGINE_MODULES = ["server", "canvas", "art", "png", "gif", "project", "face_kit"]
for m in ENGINE_MODULES:
    assert os.path.exists(os.path.join(ENGINE, m + ".py")), f"PixelLiner engine module {m}.py not found in {ENGINE}"

datas = [
    (os.path.join(ROOT, "config.toml"), "."),
    (os.path.join(ROOT, "pixelagent", "app", "static"), os.path.join("pixelagent", "app", "static")),
    # the engine as source files too: config.load_engine checks for them under <bundle>/engine
    *[(os.path.join(ENGINE, m + ".py"), "engine") for m in ENGINE_MODULES],
]

a = Analysis(
    [os.path.join(ROOT, "pixelagent", "app", "__main__.py")],
    pathex=[ROOT, ENGINE],
    datas=datas,
    # steps are imported by name and the engine is imported after a sys.path change: list them explicitly
    hiddenimports=collect_submodules("pixelagent") + ENGINE_MODULES + ["openai"],
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "notebook", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PixelAgent",
    icon=os.path.join(SPECPATH, "PixelAgent.ico"),
    console=False,         # an app, not a terminal window
    upx=False,
    version=os.path.join(SPECPATH, "version_info.txt"),
)
