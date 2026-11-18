"""Recoverable deletion, with metadata written before any user's files move.

Each directory move is atomic on the projects volume. A multi-item delete or restore may
be interrupted between moves; its journal and remaining payload stay available to Restore.
Only explicit Delete Now / Empty actions permanently remove a payload.
"""
import json
import os
import re
import shutil
import threading
import time
import uuid

from .store import ID_RE, _atomic_json, ensure_asset_identity

FOLDER = ".Recently Deleted"
_LOCK = threading.Lock()


def _move(src, dst):
    # Both paths are within the same projects root. Never fall back to copy-and-delete:
    # interruption during that fallback could leave a partial directory on each side.
    if os.path.exists(dst):
        raise FileExistsError(dst)
    os.rename(src, dst)


def _plain_name(value):
    return isinstance(value, str) and value not in ("", ".", "..") and not any(c in value for c in "/\\:")


class RecentlyDeleted:
    def __init__(self, projects_dir):
        self.projects_dir = os.path.abspath(projects_dir)
        self.dir = os.path.join(self.projects_dir, FOLDER)

    def _new_entry(self, meta):
        os.makedirs(self.dir, exist_ok=True)
        eid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        edir = os.path.join(self.dir, eid)
        os.makedirs(os.path.join(edir, "payload"))
        return eid, edir

    def _write_meta(self, edir, meta):
        _atomic_json(os.path.join(edir, "entry.json"), meta)

    def _read_meta(self, edir):
        try:
            with open(os.path.join(edir, "entry.json"), encoding="utf-8") as f:
                meta = json.load(f)
            if (meta.get("kind") in ("items", "project") and _plain_name(meta.get("project"))
                    and (meta["kind"] == "project" or (meta.get("ids") and all(
                        isinstance(aid, str) and ID_RE.fullmatch(aid) for aid in meta["ids"])))):
                return meta
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        # Older versions moved files before writing metadata. Surface orphaned files;
        # an item's original project name cannot be inferred from its payload alone.
        payload = os.path.join(edir, "payload")
        adir = os.path.join(payload, "assets")
        if os.path.isdir(adir):
            ids = sorted(n for n in os.listdir(adir) if ID_RE.fullmatch(n)
                         and os.path.isdir(os.path.join(adir, n)))
            if ids:
                return {"kind": "items", "project": "Recovered items", "ids": ids,
                        "name": "Recovered items", "phase": "deleting", "deleted_at": os.path.getmtime(edir),
                        "recovery_note": "Original deletion metadata was missing; restored into Recovered items."}
        projects = [n for n in os.listdir(payload) if _plain_name(n) and os.path.isdir(os.path.join(payload, n))]
        if len(projects) == 1:
            return {"kind": "project", "project": projects[0], "name": projects[0], "phase": "deleting",
                    "deleted_at": os.path.getmtime(edir), "recovery_note": "Recovered missing deletion metadata."}
        raise ValueError("no recoverable deletion metadata")

    @staticmethod
    def _identity(project_dir, aid, state_path=None):
        path = state_path or os.path.join(project_dir, "assets", aid, "state.json")
        if os.path.isfile(path):
            return ensure_asset_identity(path, os.path.join(project_dir, "costs.jsonl"), aid).asset_uuid
        return None

    def _prepare(self, project_dir, meta, thumb):
        project_dir = os.path.abspath(project_dir)
        if os.path.dirname(project_dir) != self.projects_dir or not _plain_name(os.path.basename(project_dir)):
            raise ValueError("project must be inside the projects folder")
        eid, edir = self._new_entry(meta)
        if thumb and os.path.isfile(thumb):
            shutil.copyfile(thumb, os.path.join(edir, "thumb" + os.path.splitext(thumb)[1].lower()))
        meta = {**meta, "project": os.path.basename(project_dir), "deleted_at": time.time(), "phase": "deleting"}
        self._write_meta(edir, meta)  # journal first, then move
        return eid, edir, meta

    def delete_items(self, project_dir, ids, meta, thumb=None):
        """A failed/interrupted batch remains available in Recently Deleted."""
        ids = list(ids)
        if not ids or len(ids) != len(set(ids)) or any(not isinstance(aid, str) or not ID_RE.fullmatch(aid) for aid in ids):
            raise ValueError("invalid item ids")
        with _LOCK:
            for aid in ids:
                if not os.path.isdir(os.path.join(project_dir, "assets", aid)):
                    raise FileNotFoundError(aid)
                self._identity(project_dir, aid)
            eid, edir, meta = self._prepare(project_dir, {**meta, "kind": "items", "ids": ids}, thumb)
            os.makedirs(os.path.join(edir, "payload", "assets"))
            for aid in ids:
                _move(os.path.join(project_dir, "assets", aid), os.path.join(edir, "payload", "assets", aid))
            self._write_meta(edir, {**meta, "phase": "deleted"})
            return eid

    def delete_project(self, project_dir, meta, thumb=None):
        with _LOCK:
            adir = os.path.join(project_dir, "assets")
            for aid in os.listdir(adir) if os.path.isdir(adir) else []:
                if ID_RE.fullmatch(aid):
                    self._identity(project_dir, aid)
            eid, edir, meta = self._prepare(project_dir, {**meta, "kind": "project"}, thumb)
            _move(project_dir, os.path.join(edir, "payload", meta["project"]))
            self._write_meta(edir, {**meta, "phase": "deleted"})
            return eid

    def entries(self):
        out = []
        if not os.path.isdir(self.dir):
            return out
        for eid in sorted(os.listdir(self.dir), reverse=True):
            try:
                edir = self.path(eid)
                meta = self._read_meta(edir)
                thumb = next((n for n in os.listdir(edir) if n.startswith("thumb.")), None)
            except (OSError, ValueError):
                continue
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

    def restore(self, eid):
        """Resume a journaled restore; never overwrite an existing directory."""
        with _LOCK:
            edir = self.path(eid)
            meta = self._read_meta(edir)
            if meta["kind"] == "project":
                return self._restore_project(edir, meta)
            return self._restore_items(edir, meta)

    def _restore_project(self, edir, meta):
        src = os.path.join(edir, "payload", meta["project"])
        name = meta.get("restore_project")
        if name is None:
            if not os.path.exists(src):
                if not os.path.isdir(os.path.join(self.projects_dir, meta["project"])):
                    raise ValueError("project payload is missing")
                name = meta["project"]  # interrupted delete never moved the project
            else:
                name = _free_name(self.projects_dir, meta["project"], lambda n: f"{meta['project']} ({n})")
            meta = {**meta, "phase": "restoring", "restore_project": name}
            self._write_meta(edir, meta)
        if not _plain_name(name):
            raise ValueError("invalid restore destination")
        dst = os.path.join(self.projects_dir, name)
        if os.path.exists(src):
            _move(src, dst)
        elif not os.path.isdir(dst):
            raise ValueError("restored project is missing")
        self._finish_restore(edir)
        return {"kind": "project", "project": name}

    def _restore_items(self, edir, meta):
        pdir = os.path.join(self.projects_dir, meta["project"])
        adir = os.path.join(pdir, "assets")
        payload = os.path.join(edir, "payload", "assets")
        os.makedirs(adir, exist_ok=True)
        plan = meta.get("restore_items")
        if plan is None:
            plan = {}
            reserved = set(os.listdir(adir))
            for aid in meta["ids"]:
                src = os.path.join(payload, aid)
                if not os.path.isdir(src):
                    if not os.path.isdir(os.path.join(adir, aid)):
                        raise ValueError(f"item payload is missing: {aid}")
                    continue  # interrupted delete left this item in place
                ident = self._identity(pdir, aid, os.path.join(src, "state.json"))
                new, n = aid, 2
                while new in reserved:
                    suffix = f"-{n}"
                    new = aid[:120 - len(suffix)] + suffix
                    n += 1
                reserved.add(new)
                plan[aid] = {"id": new, "asset_uuid": ident}
            meta = {**meta, "phase": "restoring", "restore_items": plan}
            self._write_meta(edir, meta)
        renamed = {aid: v["id"] for aid, v in plan.items()}
        for aid, dest in plan.items():
            new = dest["id"]
            if not ID_RE.fullmatch(aid) or not ID_RE.fullmatch(new):
                raise ValueError("invalid restore item")
            src, dst = os.path.join(payload, aid), os.path.join(adir, new)
            if os.path.isdir(src):
                _fix_state(os.path.join(src, "state.json"), new, renamed)
                _move(src, dst)
            elif not os.path.isdir(dst):
                raise ValueError(f"restored item is missing: {new}")
            elif dest.get("asset_uuid") and self._identity(pdir, new) != dest["asset_uuid"]:
                raise ValueError(f"restore destination belongs to another item: {new}")
        # An unmoved child may still refer to a renamed parent after a partial delete.
        for aid in meta["ids"]:
            if aid not in plan:
                _fix_state(os.path.join(adir, aid, "state.json"), aid, renamed)
        self._finish_restore(edir)
        return {"kind": "items", "project": meta["project"], "id": renamed.get(meta["ids"][0], meta["ids"][0])}

    @staticmethod
    def _finish_restore(edir):
        # Never clean up a payload not accounted for by the restore plan.
        for _, dirs, files in os.walk(os.path.join(edir, "payload")):
            if files or any(n != "assets" for n in dirs):
                raise ValueError("some deleted files still need recovery")
        shutil.rmtree(edir)

    def delete_now(self, eid):
        """Remove one entry for good (only on the user's explicit request)."""
        with _LOCK:
            shutil.rmtree(self.path(eid))

    def empty(self):
        """Remove every listed entry for good (only on the user's explicit request)."""
        with _LOCK:
            n = 0
            for e in self.entries():
                shutil.rmtree(os.path.join(self.dir, e["entry"]))
                n += 1
            return n


def _free_name(parent, name, variant):
    if not os.path.exists(os.path.join(parent, name)):
        return name
    n = 2
    while os.path.exists(os.path.join(parent, variant(n))):
        n += 1
    return variant(n)


def _fix_state(path, new, renamed):
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    st["id"] = new
    if st.get("inputs", {}).get("character") in renamed:
        st["inputs"]["character"] = renamed[st["inputs"]["character"]]
    _atomic_json(path, st)
