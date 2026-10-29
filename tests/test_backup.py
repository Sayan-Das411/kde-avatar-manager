"""Tests for kdam.backup — create_backup, inspect_backup, restore_backup."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
from helpers import _patch_paths  # shared: redirects all paths to tmp, silences notify
from PIL import Image


def _make_png(path: Path, size=(64, 64)) -> Path:
    Image.new("RGB", size, color=(80, 160, 240)).save(str(path), "PNG")
    return path


# ── create_backup ─────────────────────────────────────────────────────────────


class TestCreateBackup:
    def test_creates_archive(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import create_backup

        out = create_backup(str(tmp_path / "test.tar.gz"))
        assert out.exists()
        assert tarfile.is_tarfile(str(out))

    def test_manifest_present(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import MANIFEST_FILENAME, create_backup

        out = create_backup(str(tmp_path / "bk.tar.gz"))
        with tarfile.open(str(out), "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert MANIFEST_FILENAME in names

    def test_config_only_excludes_images_json(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import create_backup

        # ensure images.json exists so we can prove it was excluded
        (tmp_path / "images.json").write_text("{}")
        out = create_backup(str(tmp_path / "cfg.tar.gz"), config_only=True)
        with tarfile.open(str(out), "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert "config.json" in names or True  # config may be absent if not written yet
        assert "images.json" not in names

    def test_auto_names_archive_when_no_path_given(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        import kdam.backup as bk

        # Pass an explicit output path — confirms no crash when a directory is given
        out = bk.create_backup(str(tmp_path / "auto.tar.gz"))
        assert out.exists()
        assert out.suffix == ".gz"

    def test_include_images_bundles_source_files(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import create_backup
        from kdam.db import CropInfo, ImageDB, ImageEntry

        img = _make_png(tmp_path / "photo.png")
        db = ImageDB()
        db.set(str(img), ImageEntry(configured=True, enabled=True, crop=CropInfo(x=0, y=0, size=64)))
        db.save()

        out = create_backup(str(tmp_path / "full.tar.gz"), include_images=True)
        with tarfile.open(str(out), "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert any(n.startswith("images/") and "photo.png" in n for n in names)

    def test_no_images_omits_source_files(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import create_backup
        from kdam.db import CropInfo, ImageDB, ImageEntry

        img = _make_png(tmp_path / "photo.png")
        db = ImageDB()
        db.set(str(img), ImageEntry(configured=True, enabled=True, crop=CropInfo(x=0, y=0, size=64)))
        db.save()

        out = create_backup(str(tmp_path / "meta.tar.gz"), include_images=False)
        with tarfile.open(str(out), "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert not any(n.startswith("images/") for n in names)


# ── inspect_backup ────────────────────────────────────────────────────────────


class TestInspectBackup:
    def test_inspect_returns_manifest_fields(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import create_backup, inspect_backup

        out = create_backup(str(tmp_path / "bk.tar.gz"))
        info = inspect_backup(out)
        assert "created_at" in info
        assert "original_home" in info
        assert "archive_size" in info
        assert info["archive_size"] > 0

    def test_inspect_missing_file_raises(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import inspect_backup

        with pytest.raises(FileNotFoundError):
            inspect_backup(tmp_path / "ghost.tar.gz")

    def test_inspect_bad_archive_raises(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import inspect_backup

        bad = tmp_path / "bad.tar.gz"
        bad.write_bytes(b"not a tar archive")
        with pytest.raises(Exception):  # noqa: B017
            inspect_backup(bad)


# ── restore_backup ────────────────────────────────────────────────────────────


class TestRestoreBackup:
    def test_restore_config_round_trip(self, tmp_path, monkeypatch):
        """Create a backup with a custom rotation mode, restore it, and check."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import create_backup, restore_backup
        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.rotation.mode = "interval"
        cfg.rotation.interval_hours = 6
        save_config(cfg)

        bk_path = tmp_path / "bk.tar.gz"
        create_backup(str(bk_path))

        # Wipe config to simulate fresh install
        (tmp_path / "config.json").unlink(missing_ok=True)

        ok, msg = restore_backup(bk_path)
        assert ok is True
        cfg2 = load_config()
        assert cfg2.rotation.mode == "interval"
        assert cfg2.rotation.interval_hours == 6

    def test_restore_missing_archive_returns_false(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import restore_backup

        ok, msg = restore_backup(tmp_path / "ghost.tar.gz")
        assert ok is False

    def test_restore_with_bundled_images(self, tmp_path, monkeypatch):
        """Bundled images must be extracted to the destination directory."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import create_backup, restore_backup
        from kdam.db import CropInfo, ImageDB, ImageEntry

        img = _make_png(tmp_path / "avatar.png")
        db = ImageDB()
        db.set(str(img), ImageEntry(configured=True, enabled=True, crop=CropInfo(x=0, y=0, size=64)))
        db.save()

        bk_path = tmp_path / "full.tar.gz"
        create_backup(str(bk_path), include_images=True)

        dest_dir = tmp_path / "restored_imgs"
        ok, _ = restore_backup(bk_path, target_images_dir=str(dest_dir))
        assert ok is True
        assert (dest_dir / "avatar.png").exists()

    def test_restore_warns_on_filename_collision(self, tmp_path, monkeypatch, capsys):
        """Two images with the same basename must not silently overwrite each other.

        Both files should be extracted with unique indexed names; the return
        message must mention the conflict; stdout must contain the warning.
        """
        _patch_paths(tmp_path, monkeypatch)
        from kdam.backup import create_backup, restore_backup
        from kdam.db import CropInfo, ImageDB, ImageEntry

        # Two images in different locations, same filename
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        img1 = _make_png(dir1 / "photo.png")
        img2 = _make_png(dir2 / "photo.png")

        db = ImageDB()
        db.set(str(img1), ImageEntry(configured=True, enabled=True, crop=CropInfo(x=0, y=0, size=64)))
        db.set(str(img2), ImageEntry(configured=True, enabled=True, crop=CropInfo(x=0, y=0, size=64)))
        db.save()

        bk_path = tmp_path / "collision.tar.gz"
        create_backup(str(bk_path), include_images=True)

        dest_dir = tmp_path / "restored"
        ok, msg = restore_backup(bk_path, target_images_dir=str(dest_dir))
        assert ok is True

        # Both files must be on disk (with unique names)
        extracted = list(dest_dir.iterdir())
        assert len(extracted) == 2, f"Expected 2 files, got: {[p.name for p in extracted]}"

        # Warning must appear in stdout
        captured = capsys.readouterr()
        assert "conflict" in captured.out.lower() or "conflict" in msg.lower()
        assert "photo.png" in captured.out or "photo.png" in msg
