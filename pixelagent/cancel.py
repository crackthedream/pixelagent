"""Cancelling running work: the app asks, the pipeline (between steps) and the model loop (between rounds) stop.

A model call that is already running finishes first (it cannot be interrupted), so a cancel takes effect within
one round. Whatever was finished is kept: the cancelled step goes back to 'pending' and Continue resumes it."""
import os
import threading

_lock = threading.Lock()
_requested = set()


class Cancelled(Exception):
    """Raised when the user cancelled; not an error."""


def _key(project_root, asset_id):
    return os.path.normcase(os.path.abspath(project_root)), asset_id


def request(project_root, asset_id):
    with _lock:
        _requested.add(_key(project_root, asset_id))


def requested(project_root, asset_id):
    with _lock:
        return _key(project_root, asset_id) in _requested


def clear(project_root, asset_id):
    with _lock:
        _requested.discard(_key(project_root, asset_id))
