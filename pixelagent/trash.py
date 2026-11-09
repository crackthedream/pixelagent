"""Recently Deleted: deleting the user's work never destroys it.

Deleted characters, animations and projects move into "<projects folder>/.Recently Deleted/<entry>/" (a plain move,
so it either fully happens or nothing is deleted). The app lists them with Restore, Delete Now and Empty; only those
explicit actions remove files for good.

This replaces moving to the Windows Recycle Bin: SHFileOperation with FOF_ALLOWUNDO silently deletes permanently
when the bin is unavailable for a path (review finding F01), and success could not be told apart from that."""
import json
import os
import re
import shutil
import threading
import time
import uuid

FOLDER = ".Recently Deleted"
_LOCK = threading.Lock()


class RecentlyDeleted:
    def __init__(self, projects_dir):
        self.projects_dir = os.path.abspath(projects_dir)
        self.dir = os.path.join(self.projects_dir, FOLDER)

    # ---------------------------------------------------------------- moving in

    def _new_entry(self, meta):
        os.makedirs(self.dir, exist_ok=True)
        eid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        edir = os.path.join(self.dir, eid)
        os.makedirs(os.path.join(edir, "payload"))
        return eid, edir

    def _write_meta(self, edir, meta):
        with open(os.path.join(edir, "entry.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1, ensure_ascii=False)

    def delete_items(self, project_dir, ids, meta, thumb=None):
        """Move assets (a character and its animations, or one animation) of a project. ids: main id first."""
        with _LOCK:
            eid, edir = self._new_entry(meta)
            if thumb and os.path.exists(thumb):  # copied first: the item's files move away next
                shutil.copyfile(thumb, os.path.join(edir, "thumb" + os.path.splitext(thumb)[1].lower()))
            moved = []
            try:
                os.makedirs(os.path.join(edir, "payload", "assets"))
                for aid in ids:
                    src = os.path.join(project_dir, "assets", aid)
                    shutil.move(src, os.path.join(edir, "payload", "assets", aid))
                    moved.append(aid)
            except Exception:
                for aid in moved:  # put back whatever was already moved: a delete happens fully or not at all
                    shutil.move(os.path.join(edir, "payload", "assets", aid), os.path.join(project_dir, "assets", aid))
                shutil.rmtree(edir, ignore_errors=True)
                raise
            self._write_meta(edir, {**meta, "kind": "items", "ids": list(ids), "project": os.path.basename(project_dir),
                                    "deleted_at": time.time()})
            return eid

    def delete_project(self, project_dir, meta, thumb=None):
        with _LOCK:
            eid, edir = self._new_entry(meta)
            if thumb and os.path.exists(thumb):  # copied first: the project's files move away next
                shutil.copyfile(thumb, os.path.join(edir, "thumb" + os.path.splitext(thumb)[1].lower()))
            try:
                shutil.move(project_dir, os.path.join(edir, "payload", os.path.basename(project_dir)))
            except Exception:
                shutil.rmtree(edir, ignore_errors=True)
                raise
            self._write_meta(edir, {**meta, "kind": "project", "project": os.path.basename(project_dir),
                                    "deleted_at": time.time()})
            return eid

    # ---------------------------------------------------------------- listing

    def entries(self):
        out = []
        if not os.path.isdir(self.dir):
            return out
        for eid in sorted(os.listdir(self.dir), reverse=True):
            p = os.path.join(self.dir, eid, "entry.json")
            try:
                with open(p, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            thumb = next((n for n in os.listdir(os.path.join(self.dir, eid)) if n.startswith("thumb.")), None)
            out.append({**meta, "entry": eid, "thumb": thumb})
        return out

    def path(self, eid, name=None):
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", eid or ""):
            raise ValueError("unknown entry")
        p = os.path.join(self.dir, eid)
        if name is not None:
            p = os.path.normpath(os.path.join(p, name))
            if not p.startswith(os.path.join(self.dir, eid) + os.sep):
                raise ValueError("unknown file")
        if not os.path.exists(p):
            raise ValueError("unknown entry")
        return p

    # ---------------------------------------------------------------- restoring and removing for good

    def restore(self, eid):
        """Move an entry back. Name clashes get a free name instead of overwriting anything. Returns where it went."""
        with _LOCK:
            edir = self.path(eid)
            with open(os.path.join(edir, "entry.json"), encoding="utf-8") as f:
                meta = json.load(f)
            if meta["kind"] == "project":
                name = _free_name(self.projects_dir, meta["project"], lambda n: f"{meta['project']} ({n})")
                shutil.move(os.path.join(edir, "payload", meta["project"]), os.path.join(self.projects_dir, name))
                shutil.rmtree(edir)
                return {"kind": "project", "project": name}
            pdir = os.path.join(self.projects_dir, meta["project"])
            os.makedirs(os.path.join(pdir, "assets"), exist_ok=True)  # the project itself may have been deleted
            renamed = {}
            for aid in meta["ids"]:
                new = _free_name(os.path.join(pdir, "assets"), aid, lambda n: f"{aid}-{n}")
                shutil.move(os.path.join(edir, "payload", "assets", aid), os.path.join(pdir, "assets", new))
                renamed[aid] = new
            if any(k != v for k, v in renamed.items()):
                _fix_ids(pdir, renamed)
            shutil.rmtree(edir)
            return {"kind": "items", "project": meta["project"], "id": renamed[meta["ids"][0]]}

    def delete_now(self, eid):
        """Remove one entry for good (only on the user's explicit request)."""
        with _LOCK:
            shutil.rmtree(self.path(eid))

    def empty(self):
        """Remove every entry for good (only on the user's explicit request). Returns how many were removed."""
        with _LOCK:
            n = 0
            for e in self.entries():
                shutil.rmtree(os.path.join(self.dir, e["entry"]), ignore_errors=True)
                n += 1
            return n


def _free_name(parent, name, variant):
    if not os.path.exists(os.path.join(parent, name)):
        return name
    n = 2
    while os.path.exists(os.path.join(parent, variant(n))):
        n += 1
    return variant(n)


def _fix_ids(project_dir, renamed):
    """A restored asset that had to take a new id: update its state, and its animations' link to it."""
    for new in renamed.values():
        p = os.path.join(project_dir, "assets", new, "state.json")
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
        st["id"] = new
        if st.get("inputs", {}).get("character") in renamed:
            st["inputs"]["character"] = renamed[st["inputs"]["character"]]
        with open(p, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2, ensure_ascii=False)
