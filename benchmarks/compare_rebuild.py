"""Compare shading quality (noise, fidelity, fallback pixels) of rebuilt sprites between two project folders
holding the same assets. Usage: python benchmarks/compare_rebuild.py OLD_PROJECT NEW_PROJECT OUT.png [step]"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pixelagent import imaging  # noqa: E402
from pixelagent.config import load_config, load_engine  # noqa: E402

load_engine(load_config())
import art  # noqa: E402


def state(asset_dir):
    with open(os.path.join(asset_dir, "state.json"), encoding="utf-8") as f:
        return json.load(f)["steps"]


def metrics(im, ref):
    d = imaging.to_bytes(im)
    n = art.shade_noise(im.width, im.height, [tuple(d[i:i + 4]) for i in range(0, len(d), 4)])
    fid = art.fidelity(im.width, im.height, d, imaging.to_bytes(ref))
    return n["shade_changes"], n["singletons"], fid["delta_e"]


def main(old_root, new_root, out, step="rebuild"):
    key = "preview" if step != "export" else "png"
    tiles, labels = [], []
    for new_dir in sorted(glob.glob(os.path.join(new_root, "assets", "*"))):
        aid = os.path.basename(new_dir)
        old_dir = os.path.join(old_root, "assets", aid)
        if not os.path.isdir(old_dir):
            continue
        so, sn = state(old_dir), state(new_dir)
        base = imaging.load_rgba(os.path.join(new_dir, sn["pixelize"]["outputs"]["pixel_base"]))
        old = imaging.load_rgba(os.path.join(old_dir, so[step]["outputs"][key]))
        new = imaging.load_rgba(os.path.join(new_dir, sn[step]["outputs"][key]))
        (oc, osg, ofi), (nc, nsg, nfi) = metrics(old, base), metrics(new, base)
        fams = sn.get("materials", {}).get("info", {}).get("families", {})
        print(f"{aid.rsplit('-', 2)[0]:18s} shade_changes {oc:.2f} -> {nc:.2f} | singletons {osg:4d} -> {nsg:4d} | "
              f"fidelity dE {ofi:.3f} -> {nfi:.3f} | families {len(fams)} steps "
              f"{sorted((v['steps'] for v in fams.values()), reverse=True)}")
        tiles += [base, old, new]
        labels += [aid.rsplit("-", 2)[0][:14] + " base", f"old {step}", f"new {step}"]
    imaging.contact_sheet(tiles, labels, zoom_factor=3, cols=6).save(out)
    print("sheet:", out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(*sys.argv[1:])
