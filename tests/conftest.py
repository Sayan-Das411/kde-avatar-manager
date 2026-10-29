"""
conftest.py — session-level guards to keep tests from corrupting live user data.

The real ~/.kde-avatar-manager/ critical files are snapshot-and-restored
around every test session.  This is the last line of defence: even if a test
leaks a save_config() or log-write call to the real data directory, the
restore step at session teardown puts everything back exactly as it was.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_REAL_DATA_DIR = Path.home() / ".kde-avatar-manager"
_REAL_CONFIG = _REAL_DATA_DIR / "config.json"
_REAL_LOG = _REAL_DATA_DIR / "logs" / "rotation.log"
_REAL_IMAGES = _REAL_DATA_DIR / "images.json"
_REAL_BACKUP = _REAL_DATA_DIR / "backup.png"
_REAL_PROC = _REAL_DATA_DIR / "processed"


@pytest.fixture(scope="session", autouse=True)
def _guard_live_data(tmp_path_factory):
    """Snapshot real user data before the test session, restore it afterward.

    This is intentionally session-scoped (not function-scoped) so the overhead
    of copying is paid only once.  Individual tests that write to the real data
    directory will have those writes reverted when the session tears down.
    """
    snap = tmp_path_factory.mktemp("_live_data_snapshot")

    # ── snapshot ──────────────────────────────────────────────────────────────
    snap_config = snap / "config.json"
    snap_log = snap / "rotation.log"
    snap_images = snap / "images.json"
    snap_backup = snap / "backup.png"
    snap_proc = snap / "processed"

    if _REAL_CONFIG.exists():
        shutil.copy2(_REAL_CONFIG, snap_config)
    if _REAL_LOG.exists():
        shutil.copy2(_REAL_LOG, snap_log)
    if _REAL_IMAGES.exists():
        shutil.copy2(_REAL_IMAGES, snap_images)
    if _REAL_BACKUP.exists():
        shutil.copy2(_REAL_BACKUP, snap_backup)
    if _REAL_PROC.exists():
        shutil.copytree(_REAL_PROC, snap_proc)

    yield  # ← tests run here

    # ── restore ───────────────────────────────────────────────────────────────
    if snap_config.exists():
        shutil.copy2(snap_config, _REAL_CONFIG)
    elif _REAL_CONFIG.exists():
        _REAL_CONFIG.unlink()  # was absent before tests; remove if tests created it

    if snap_log.exists():
        shutil.copy2(snap_log, _REAL_LOG)
    elif _REAL_LOG.exists():
        _REAL_LOG.unlink()

    if snap_images.exists():
        shutil.copy2(snap_images, _REAL_IMAGES)
    elif _REAL_IMAGES.exists():
        _REAL_IMAGES.unlink()

    if snap_backup.exists():
        shutil.copy2(snap_backup, _REAL_BACKUP)
    elif _REAL_BACKUP.exists():
        _REAL_BACKUP.unlink()

    if snap_proc.exists():
        if _REAL_PROC.exists():
            shutil.rmtree(_REAL_PROC)
        shutil.copytree(snap_proc, _REAL_PROC)
    elif _REAL_PROC.exists():
        shutil.rmtree(_REAL_PROC)
