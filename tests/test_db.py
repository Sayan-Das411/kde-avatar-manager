"""Tests for kdam.db — ImageEntry and ImageDB."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

def _patch_paths(tmp: Path, monkeypatch):
    """Redirect all config/data paths to a temp directory."""
    import kdam.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR",   tmp)
    monkeypatch.setattr(cfg, "CONFIG_FILE",  tmp / "config.json")
    monkeypatch.setattr(cfg, "IMAGES_FILE",  tmp / "images.json")
    monkeypatch.setattr(cfg, "PROCESSED_DIR", tmp / "processed")
    monkeypatch.setattr(cfg, "BACKUP_FILE",  tmp / "backup.png")
    monkeypatch.setattr(cfg, "LOGS_DIR",     tmp / "logs")
    monkeypatch.setattr(cfg, "LOG_FILE",     tmp / "logs" / "rotation.log")
    monkeypatch.setattr(cfg, "_LOCK_FILE",   tmp / ".lock")
    import kdam.db as db
    monkeypatch.setattr(db, "IMAGES_FILE",   tmp / "images.json")
    cfg.ensure_dirs()


# ── ImageEntry ────────────────────────────────────────────────────────────────

class TestImageEntry:
    def test_defaults(self):
        from kdam.db import ImageEntry
        e = ImageEntry()
        assert e.configured is False
        assert e.enabled is False
        assert e.weight == 1.0
        assert e.crop is None
        assert e.suspended_by_dir is False

    def test_round_trip_no_crop(self):
        from kdam.db import ImageEntry
        e = ImageEntry(configured=True, enabled=True, weight=2.5)
        d = e.to_dict()
        e2 = ImageEntry.from_dict(d)
        assert e2.configured is True
        assert e2.enabled is True
        assert e2.weight == 2.5
        assert e2.crop is None

    def test_round_trip_with_crop(self):
        from kdam.db import ImageEntry, CropInfo
        e = ImageEntry(configured=True, enabled=True,
                       crop=CropInfo(x=10, y=20, size=300))
        d = e.to_dict()
        e2 = ImageEntry.from_dict(d)
        assert e2.crop is not None
        assert e2.crop.x == 10
        assert e2.crop.y == 20
        assert e2.crop.size == 300

    def test_suspended_by_dir_backwards_compat(self):
        """Old JSON without the field should deserialise fine."""
        from kdam.db import ImageEntry
        old = {"configured": True, "enabled": True, "weight": 1.0,
               "crop": None, "processed_path": None, "source_mtime": None,
               "last_used": None}
        e = ImageEntry.from_dict(old)
        assert e.suspended_by_dir is False


# ── CropInfo ──────────────────────────────────────────────────────────────────

class TestCropInfo:
    def test_round_trip(self):
        from kdam.db import CropInfo
        c = CropInfo(x=5, y=15, size=256)
        c2 = CropInfo.from_dict(c.to_dict())
        assert c2.x == 5 and c2.y == 15 and c2.size == 256

    def test_coerces_float_to_int(self):
        from kdam.db import CropInfo
        c = CropInfo.from_dict({"x": 1.9, "y": 2.7, "size": 100.0})
        assert isinstance(c.x, int) and isinstance(c.size, int)


# ── ImageDB ───────────────────────────────────────────────────────────────────

class TestImageDB:
    def test_empty_load(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.db import ImageDB
        db = ImageDB()
        assert len(db) == 0

    def test_set_and_get(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.db import ImageDB, ImageEntry
        db = ImageDB()
        path = str(tmp_path / "img.jpg")
        e = ImageEntry(configured=True, enabled=True)
        db.set(path, e)
        got = db.get(path)
        assert got is not None
        assert got.configured is True

    def test_save_and_reload(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.db import ImageDB, ImageEntry
        db = ImageDB()
        path = str(tmp_path / "avatar.png")
        db.set(path, ImageEntry(configured=True, enabled=True, weight=3.0))
        db.save()

        db2 = ImageDB()
        entry = db2.get(path)
        assert entry is not None
        assert entry.weight == 3.0
        assert entry.configured is True

    def test_remove(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.db import ImageDB, ImageEntry
        db = ImageDB()
        path = str(tmp_path / "img.jpg")
        db.set(path, ImageEntry())
        db.remove(path)
        assert db.get(path) is None
        assert len(db) == 0

    def test_active_view(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.db import ImageDB, ImageEntry
        db = ImageDB()
        db.set("/a.jpg", ImageEntry(configured=True, enabled=True))
        db.set("/b.jpg", ImageEntry(configured=True, enabled=False))
        db.set("/c.jpg", ImageEntry(configured=False, enabled=False))
        active = db.active()
        assert "/a.jpg" in active
        assert "/b.jpg" not in active
        assert "/c.jpg" not in active

    def test_unconfigured_view(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.db import ImageDB, ImageEntry
        db = ImageDB()
        db.set("/a.jpg", ImageEntry(configured=True))
        db.set("/b.jpg", ImageEntry(configured=False))
        unc = db.unconfigured()
        assert "/b.jpg" in unc
        assert "/a.jpg" not in unc

    def test_corrupt_file_gives_empty(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        (tmp_path / "images.json").write_text("not-json{{{")
        from kdam.db import ImageDB
        db = ImageDB()
        assert len(db) == 0

    def test_key_normalises_path(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.db import ImageDB, ImageEntry
        db = ImageDB()
        # Use a real path so resolve() works
        p = tmp_path / "img.jpg"
        p.touch()
        db.set(str(p) + "/./", ImageEntry(configured=True))
        assert db.get(str(p)) is not None
