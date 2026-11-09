"""Deleting the user's work never destroys it: folders go to the Windows Recycle Bin (restorable from there).

PIXELAGENT_TRASH_DIR (tests, other systems) moves them into that folder instead."""
import os
import shutil
import time


def send_to_trash(path):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    alt = os.environ.get("PIXELAGENT_TRASH_DIR")
    if alt or os.name != "nt":
        dest_root = alt or os.path.join(os.path.expanduser("~"), ".pixelagent", "trash")
        os.makedirs(dest_root, exist_ok=True)
        dest = os.path.join(dest_root, f"{os.path.basename(path)}-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.move(path, dest)
        return dest
    _recycle_windows(path)
    return None


def _recycle_windows(path):
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, path + "\0", None,  # pFrom is a double-null-terminated list
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted or os.path.exists(path):
        raise OSError(f"could not move {path} to the Recycle Bin (code {rc})")
