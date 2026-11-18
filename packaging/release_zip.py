"""Package the tested Windows x64 executable and its first-run instructions."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def package(dist):
    exe = dist / "PixelAgent.exe"
    with exe.open("rb") as src:
        if src.read(2) != b"MZ":
            raise ValueError("Expected a Windows executable")
        src.seek(60)
        src.seek(int.from_bytes(src.read(4), "little"))
        if src.read(6) != b"PE\0\0\x64\x86":
            raise ValueError("Expected a Windows x64 executable")
    notices = dist / "THIRD-PARTY-NOTICES"
    manifest = json.loads((notices / "manifest.json").read_text(encoding="utf-8"))
    with exe.open("rb") as src:
        if manifest["executable_sha256"] != hashlib.file_digest(src, "sha256").hexdigest():
            raise ValueError("Third-party notices do not match the executable being packaged")
    for entry in manifest["license_files"]:
        with (notices / entry["file"]).open("rb") as src:
            if entry["sha256"] != hashlib.file_digest(src, "sha256").hexdigest():
                raise ValueError("Third-party notice text changed after collection")
    archive = dist / "PixelAgent-Windows-x64.zip"
    repo = Path(__file__).resolve().parents[1]
    for name in ("LICENSE", "DISTRIBUTION.md"):
        if not (repo / name).is_file():
            raise ValueError(f"Release terms are missing: {name}")
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
        output.write(exe, "PixelAgent.exe")
        output.write(Path(__file__).with_name("WINDOWS-QUICKSTART.txt"), "START-HERE.txt")
        output.write(repo / "LICENSE", "LICENSE")
        output.write(repo / "DISTRIBUTION.md", "DISTRIBUTION.md")
        for name in sorted({"README.md", "manifest.json", *(entry["file"] for entry in manifest["license_files"])}):
            output.write(notices / name, "THIRD-PARTY-NOTICES/" + name)
    with archive.open("rb") as src:
        digest = hashlib.file_digest(src, "sha256").hexdigest()
    archive.with_suffix(".sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive


if __name__ == "__main__":
    print(package(Path(__file__).resolve().parents[1] / "dist"))
