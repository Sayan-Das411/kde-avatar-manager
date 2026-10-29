"""tests/helpers.py — shared test utilities.

Import _patch_paths from here in test files instead of defining it locally.
This eliminates the 5-way duplication that was in the original code.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


def _patch_paths(tmp: Path, monkeypatch) -> None:
    """Redirect all kdam module-level path constants to a temporary directory.

    Call this at the top of any test that exercises save/load behaviour to
    guarantee the real ~/.kde-avatar-manager/ is never touched.  This is the
    comprehensive superset used by all test files — patching every module that
    caches these paths at import time.

    Also patches Path.home() so any kdam code that calls Path.home() at
    runtime (e.g. backup default output path, Avatars fallback dir) is
    redirected to tmp/fake_home — preventing writes to the real home directory
    during testing.

    Also suppresses QMessageBox dialogs (upscale warning for small images).
    """
    import kdam.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR",    tmp)
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE",   tmp / "config.json")
    monkeypatch.setattr(cfg_mod, "IMAGES_FILE",   tmp / "images.json")
    monkeypatch.setattr(cfg_mod, "PROCESSED_DIR", tmp / "processed")
    monkeypatch.setattr(cfg_mod, "BACKUP_FILE",   tmp / "backup.png")
    monkeypatch.setattr(cfg_mod, "LOGS_DIR",      tmp / "logs")
    monkeypatch.setattr(cfg_mod, "LOG_FILE",      tmp / "logs" / "rotation.log")
    monkeypatch.setattr(cfg_mod, "_LOCK_FILE",    tmp / ".lock")

    import kdam.db as db_mod
    monkeypatch.setattr(db_mod, "IMAGES_FILE",    tmp / "images.json")

    import kdam.rotator as rot_mod
    monkeypatch.setattr(rot_mod, "PROCESSED_DIR", tmp / "processed")
    monkeypatch.setattr(rot_mod, "LOGS_DIR",      tmp / "logs")
    monkeypatch.setattr(rot_mod, "LOG_FILE",      tmp / "logs" / "rotation.log")

    import kdam.backup as bk_mod
    monkeypatch.setattr(bk_mod, "CONFIG_DIR",     tmp)
    monkeypatch.setattr(bk_mod, "CONFIG_FILE",    tmp / "config.json")
    monkeypatch.setattr(bk_mod, "IMAGES_FILE",    tmp / "images.json")
    monkeypatch.setattr(bk_mod, "BACKUP_FILE",    tmp / "backup.png")

    import kdam.cli as cli_mod
    monkeypatch.setattr(cli_mod, "BACKUP_FILE",   tmp / "backup.png")
    monkeypatch.setattr(cli_mod, "PROCESSED_DIR", tmp / "processed")
    monkeypatch.setattr(cli_mod, "LOG_FILE",      tmp / "logs" / "rotation.log")
    monkeypatch.setattr(cli_mod, "LOGS_DIR",      tmp / "logs")
    monkeypatch.setattr(cli_mod, "CONFIG_DIR",    tmp)

    # _cli_shared imports LOG_FILE/LOGS_DIR by name at module level — patch separately
    import kdam._cli_shared as shared_mod
    monkeypatch.setattr(shared_mod, "LOG_FILE",   tmp / "logs" / "rotation.log")
    monkeypatch.setattr(shared_mod, "LOGS_DIR",   tmp / "logs")

    # Silence desktop notifications — tests must never fire notify-send
    import kdam.notify as notify_mod
    monkeypatch.setattr(notify_mod, "send", MagicMock())

    # Redirect Path.home() → tmp/fake_home so backup default output, Avatars
    # fallback dir, and any other runtime Path.home() calls never touch ~/
    fake_home = tmp / "fake_home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    # Suppress QMessageBox dialogs (e.g. upscale warning for small test images)
    try:
        from PyQt5.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "warning",  staticmethod(lambda *a, **kw: QMessageBox.Ok))
        monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **kw: QMessageBox.Ok))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **kw: QMessageBox.Ok))
    except ImportError:
        pass  # PyQt5 not available — no dialogs possible

    cfg_mod.ensure_dirs()
