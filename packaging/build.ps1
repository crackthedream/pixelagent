# Build dist\PixelAgent.exe. Needs PyInstaller in the Python that has pixelagent's dependencies:
#   python -m pip install pyinstaller
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
Push-Location $root
try {
    python -c "import PyInstaller" 2>$null
    if (-not $?) { throw "PyInstaller is not installed. Run: python -m pip install pyinstaller" }
    python "$here\make_icon.py" "$here\PixelAgent.ico"
    python -m PyInstaller --noconfirm --clean --distpath "$root\dist" --workpath "$root\build" "$here\PixelAgent.spec"
    if (-not $?) { throw "PyInstaller failed" }
    python "$here\collect_notices.py"
    if (-not $?) { throw "Third-party notice collection failed" }
    python "$here\release_zip.py"
    if (-not $?) { throw "Windows release packaging failed" }
    Write-Host "Built $root\dist\PixelAgent.exe"
} finally {
    Pop-Location
}
