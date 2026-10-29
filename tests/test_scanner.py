"""Tests for kdam.scanner — directory walking and image discovery."""
from __future__ import annotations

from pathlib import Path
from PIL import Image

import pytest


def _patch_paths(tmp: Path, monkeypatch):
    import kdam.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_DIR",    tmp)
    monkeypatch.setattr(cfg, "CONFIG_FILE",   tmp / "config.json")
    monkeypatch.setattr(cfg, "IMAGES_FILE",   tmp / "images.json")
    monkeypatch.setattr(cfg, "PROCESSED_DIR", tmp / "processed")
    monkeypatch.setattr(cfg, "BACKUP_FILE",   tmp / "backup.png")
    monkeypatch.setattr(cfg, "LOGS_DIR",      tmp / "logs")
    monkeypatch.setattr(cfg, "LOG_FILE",      tmp / "logs" / "rotation.log")
    monkeypatch.setattr(cfg, "_LOCK_FILE",    tmp / ".lock")
    import kdam.db as db
    monkeypatch.setattr(db, "IMAGES_FILE",    tmp / "images.json")
    cfg.ensure_dirs()


def _make_png(path: Path, size: tuple = (64, 64)) -> Path:
    """Create a minimal valid PNG at *path*."""
    img = Image.new("RGB", size, color=(100, 150, 200))
    img.save(str(path), "PNG")
    return path


# ── _is_valid_image ────────────────────────────────────────────────────────────

class TestIsValidImage:
    def test_valid_png(self, tmp_path):
        from kdam.scanner import _is_valid_image
        p = tmp_path / "img.png"
        _make_png(p)
        assert _is_valid_image(p) is True

    def test_unsupported_extension_rejected(self, tmp_path):
        from kdam.scanner import _is_valid_image
        p = tmp_path / "file.txt"
        p.write_text("not an image")
        assert _is_valid_image(p) is False

    def test_corrupt_file_rejected(self, tmp_path):
        from kdam.scanner import _is_valid_image
        p = tmp_path / "bad.jpg"
        p.write_bytes(b"\xff\xd8\xff garbage not a real jpeg")
        # verify() raises; should return False
        assert _is_valid_image(p) is False

    def test_nonexistent_rejected(self, tmp_path):
        from kdam.scanner import _is_valid_image
        assert _is_valid_image(tmp_path / "ghost.png") is False


# ── _walk_directory ────────────────────────────────────────────────────────────

class TestWalkDirectory:
    def test_non_recursive_finds_top_level_only(self, tmp_path):
        from kdam.scanner import _walk_directory
        _make_png(tmp_path / "top.png")
        sub = tmp_path / "sub"
        sub.mkdir()
        _make_png(sub / "nested.png")
        found = _walk_directory(tmp_path, recursive=False)
        names = [p.name for p in found]
        assert "top.png" in names
        assert "nested.png" not in names

    def test_recursive_finds_nested(self, tmp_path):
        from kdam.scanner import _walk_directory
        sub = tmp_path / "deep" / "dir"
        sub.mkdir(parents=True)
        _make_png(sub / "inner.png")
        found = _walk_directory(tmp_path, recursive=True)
        names = [p.name for p in found]
        assert "inner.png" in names

    def test_missing_dir_returns_empty(self, tmp_path):
        from kdam.scanner import _walk_directory
        result = _walk_directory(tmp_path / "nonexistent", recursive=False)
        assert result == []

    def test_skips_non_image_files(self, tmp_path):
        from kdam.scanner import _walk_directory
        (tmp_path / "readme.txt").write_text("hello")
        _make_png(tmp_path / "real.png")
        found = _walk_directory(tmp_path, recursive=False)
        names = [p.name for p in found]
        assert "real.png" in names
        assert "readme.txt" not in names


# ── scan ──────────────────────────────────────────────────────────────────────

class TestScan:
    def test_discovers_new_images(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import Config, Directory, load_config, save_config
        from kdam.db import ImageDB
        from kdam.scanner import scan

        img_dir = tmp_path / "imgs"
        img_dir.mkdir()
        _make_png(img_dir / "a.png")
        _make_png(img_dir / "b.png")

        cfg = Config()
        cfg.directories.append(Directory(path=str(img_dir)))
        db = ImageDB()

        new_imgs, stale = scan(cfg, db)
        assert len(new_imgs) == 2
        assert stale == []
        assert len(db) == 2

    def test_does_not_re_add_existing(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import Config, Directory
        from kdam.db import ImageDB, ImageEntry
        from kdam.scanner import scan

        img_dir = tmp_path / "imgs"
        img_dir.mkdir()
        p = _make_png(img_dir / "a.png")

        db = ImageDB()
        db.set(str(p), ImageEntry(configured=True, enabled=True))

        cfg = Config()
        cfg.directories.append(Directory(path=str(img_dir)))

        new_imgs, _ = scan(cfg, db)
        assert new_imgs == []   # already registered, not re-added

    def test_detects_stale_entries(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import Config
        from kdam.db import ImageDB, ImageEntry
        from kdam.scanner import scan

        ghost = str(tmp_path / "ghost.png")
        db = ImageDB()
        db.set(ghost, ImageEntry(configured=True, enabled=True))

        cfg = Config()   # no directories configured
        _, stale = scan(cfg, db)
        assert ghost in stale

    def test_disabled_directory_skipped(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import Config, Directory
        from kdam.db import ImageDB
        from kdam.scanner import scan

        img_dir = tmp_path / "imgs"
        img_dir.mkdir()
        _make_png(img_dir / "x.png")

        cfg = Config()
        cfg.directories.append(Directory(path=str(img_dir), enabled=False))
        db = ImageDB()

        new_imgs, _ = scan(cfg, db)
        assert new_imgs == []   # disabled — should not scan
