# Contributing to PixelAgent

PixelAgent currently focuses on pixel-art characters and expressions. The Soft look uses image generation and native-grid recovery; Classic uses the PixelLiner MCP drawing agent. Objects are available but frozen, and animations, scenes, and kits are experimental and disabled by default. Please discuss changes that expand this scope before starting a large implementation.

For ordinary bugs and feature proposals, use this repository's Issues. Follow [SECURITY.md](SECURITY.md) for potential vulnerabilities; do not post sensitive details publicly.

## Development setup

The tested desktop/build environment is Windows x64 with Python 3.13. The package declares Python 3.11 or newer, but that declaration is not evidence that every supported Python version or operating system has been tested.

From a checkout of this repository, create a virtual environment and install the tested dependencies:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

The lock includes the OpenAI SDK and PyInstaller. Run the commands below from the repository root; installing PixelAgent itself is not required for this workflow. Keep the virtual environment out of commits.

The source application and several offline tests also need the PixelLiner MCP engine. That engine repository is currently private: source development and builds require authorized access. Public users can run the packaged Windows executable, which includes the engine; source availability alone does not grant access to the separate engine repository.

If you have engine access, obtain the repository identified by `packaging/engine-lock.json` and use the exact commit recorded there. The default source configuration expects that checkout at `~/pixelliner-mcp`:

```powershell
$engineDir = Join-Path $env:USERPROFILE 'pixelliner-mcp'
$engineLock = Get-Content packaging\engine-lock.json -Raw | ConvertFrom-Json
git clone https://github.com/crackthedream/pixelliner-mcp.git $engineDir
git -C $engineDir checkout --detach $engineLock.commit
```

These commands assume that destination does not already exist. Preserve existing engine work; use another checkout if necessary. For a different location, set `[engine] path` in your local `config.toml` for source runs/tests, and set `PIXELLINER_ENGINE` for packaging. Do not commit machine-specific paths. Engine access and redistribution are subject to that repository's own terms.

## Tests and local runs

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The automated suite uses procedural fixtures, stand-in models, or injected fake clients. It should not need a real API key or paid API requests. Some tests start a loopback HTTP server or the local MCP subprocess, so an environment that blocks local sockets or subprocesses may need an appropriate test environment.

Run a focused module while developing:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_model_metering.py -v
```

To inspect the desktop UI:

```powershell
.\.venv\Scripts\python.exe -m pixelagent.app
```

Fresh desktop installations default to **Test run**. Existing settings are preserved, so confirm that mode before creating or running development work. Test-run images are placeholders and are not evidence of paid-model output quality. Keep test projects separate from work you want to preserve.

Paid-model experiments are separate from automated validation. Use only an account and reference material you are authorized to use, agree on a spending allowance with the account holder, and report the model, settings, measured usage, and limitations. Local spending thresholds are estimates checked between requests, not a hard provider-side cap.

## Building the Windows release

Follow the [Windows build guide](packaging/BUILDING.md) for Python runtime notices and prerequisites. From the environment used for tests, run:

```powershell
.\.venv\Scripts\Activate.ps1
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

If activation is unavailable, ensure the virtual environment's Python is first on `PATH` before invoking the build script. The build verifies bundled engine files against `packaging/engine-lock.json`, then creates the executable and Windows release ZIP in `dist/`. It refuses an engine mismatch; review engine changes before intentionally updating the lock. Do not bypass verification to make a build pass.

When preparing a release, check the ZIP's accompanying notices and license files as well as the executable. Regenerate release checksums after repacking; do not reuse checksums from an earlier bundle.

Run the packaged application separately after a successful build. A source test pass does not establish packaged UI behavior, clean-machine compatibility, or successful imports into game engines. The current executable is unsigned.

## Pull requests

- Describe the concrete problem and resulting behavior, with a small reproduction when useful.
- Keep changes focused. Preserve existing projects, revision history, cancel/resume behavior, and recoverable deletion.
- Add a regression test for a bug where it exercises meaningful behavior. Use fake clients for provider behavior and synthetic or redistributable assets for image tests.
- Report the checks you ran and clearly separate automated tests, manual desktop checks, and paid-model experiments.
- Update user-facing documentation when behavior or setup changes. Keep model choices and pricing assumptions explicit.
- Include only files intended for source distribution. Review the staged diff for credentials, personal paths, private prompts, reference images, generated outputs, and local settings.

Do not include third-party game art or private reference images without redistribution permission. For any contributed asset, identify its origin and license. Benchmark ratings and historical measurements should retain their provenance and should not be presented as general quality guarantees.

