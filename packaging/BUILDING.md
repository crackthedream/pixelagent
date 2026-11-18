# Build the Windows preview

The published Windows ZIP includes Python, the drawing engine, third-party license notices, and first-run instructions. An automatic GitHub source archive is a developer checkout and does not contain that runtime.

The 18 November 2026 build used 64-bit CPython 3.13.5 on Windows. Use the full Python installation, including its documentation: notice collection reads `LICENSE.txt` and `Doc/html/license.html` under the Python installation directory. It stops if either document is missing. `requirements-lock.txt` records the installed runtime packages found in the actual PyInstaller build table, plus build dependencies.

1. Obtain an authorized copy of the separately maintained drawing engine. The public application source does not grant a license to extract or independently redistribute that engine. `engine-lock.json` pins the seven files used by this release; the build checks their contents before bundling them.
2. In a dedicated Python environment, install `requirements-lock.txt` and set `PIXELLINER_ENGINE` to the authorized engine directory if it is not `~/pixelliner-mcp`.
3. Run `powershell -ExecutionPolicy Bypass -File packaging\build.ps1` from the repository. The recipe builds the executable, collects notices from its build table and the installed distributions, then creates the ZIP and checksum.
4. Verify the EXE extracted from the resulting ZIP in Test run before publishing. Keep the ZIP, checksum and test evidence together. A rebuild can have a different hash even when application behavior is unchanged.

`collect_notices.py` does not import model providers or make network requests. It maps bundled files to installed package metadata, copies their original license texts, and records the executable hash in a manifest. The package step rejects missing or mismatched license text and includes only files in that manifest, so notices left by an older build cannot enter the ZIP. It also includes the repository's `LICENSE` and `DISTRIBUTION.md`.

The SDK's optional audio helpers are not an application feature. `PixelAgent.spec` excludes `sounddevice` and `_sounddevice_data`, and notice collection rejects a build containing their PortAudio libraries. The SDK's lazy audio proxy can remain; no audio library is shipped. The application still retains the SDK's image and Responses clients.

This recipe records build inputs and verifies the engine and notice contents. It does not claim byte-for-byte reproducible executables or grant rights beyond the relevant component licenses.
