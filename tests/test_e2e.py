"""test_e2e.py — end-to-end integration: scan → configure → rotate pipeline.

These tests exercise the full real-world user flow without mocking any kdam
internals (except D-Bus and the system notification daemon, which require
hardware that is not available in CI):

  1. scan() discovers images in a real temp directory
  2. ImageDB reflects them as unconfigured entries
  3. configure (simulated by directly writing CropInfo) registers them
  4. rotate() selects and "applies" the next image (D-Bus mocked)
  5. State, log, and DB are all verified after rotation

A second class tests the dirs suspend → resume → rotate flow, which exercises
the interaction between cli_dirs.py and the rotator.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from helpers import _patch_paths


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_png(path: Path, size: tuple = (128, 128), color: tuple = (100, 150, 200)) -> Path:
    Image.new("RGB", size, color=color).save(str(path), "PNG")
    return path


def _make_config(dirs, tmp_path):
    from kdam.config import Config, RotationConfig, PanicConfig, State
    return Config(
        rotation=RotationConfig(mode="daily", order="random", interval_hours=24),
        panic=PanicConfig(),
        state=State(enabled=True),
        directories=dirs,
    )


def _configure_entry(db, img_path: str) -> None:
    """Simulate 'kdam add' by writing a CropInfo into the DB for img_path."""
    from kdam.db import ImageEntry, CropInfo
    entry = ImageEntry(
        configured=True,
        enabled=True,
        weight=1.0,
        crop=CropInfo(x=0, y=0, size=64),
    )
    db.set(img_path, entry)
    db.save()


# ── End-to-end: scan → configure → rotate ─────────────────────────────────────

class TestScanConfigureRotate:

    def test_scan_discovers_images(self, tmp_path, monkeypatch):
        """scanner.scan() must add new image files to the DB as unconfigured entries."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner
        from kdam.db import ImageDB
        from kdam.config import Directory

        img_dir = tmp_path / "photos"
        img_dir.mkdir()
        _make_png(img_dir / "a.png")
        _make_png(img_dir / "b.png")

        dirs = [Directory(path=str(img_dir), recursive=False)]
        config = _make_config(dirs, tmp_path)
        db = ImageDB()
        new_images, stale = scanner.scan(config, db)

        assert len(new_images) == 2
        assert stale == []
        entries = db.all()
        assert len(entries) == 2
        for e in entries.values():
            assert not e.configured   # scan registers but does NOT configure
            assert not e.enabled

    def test_scan_recursive(self, tmp_path, monkeypatch):
        """Recursive scan discovers images in subdirectories."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner
        from kdam.db import ImageDB
        from kdam.config import Directory

        img_dir = tmp_path / "photos"
        sub = img_dir / "sub"
        sub.mkdir(parents=True)
        _make_png(img_dir / "top.png")
        _make_png(sub / "nested.png")

        dirs = [Directory(path=str(img_dir), recursive=True)]
        config = _make_config(dirs, tmp_path)
        db = ImageDB()
        new_images, _ = scanner.scan(config, db)
        assert len(new_images) == 2

    def test_scan_non_recursive_skips_subdirs(self, tmp_path, monkeypatch):
        """Non-recursive scan must not descend into subdirectories."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner
        from kdam.db import ImageDB
        from kdam.config import Directory

        img_dir = tmp_path / "photos"
        sub = img_dir / "sub"
        sub.mkdir(parents=True)
        _make_png(img_dir / "top.png")
        _make_png(sub / "nested.png")

        dirs = [Directory(path=str(img_dir), recursive=False)]
        config = _make_config(dirs, tmp_path)
        db = ImageDB()
        new_images, _ = scanner.scan(config, db)
        assert len(new_images) == 1

    def test_scan_skips_invalid_images(self, tmp_path, monkeypatch):
        """Files that are not valid images must be silently skipped."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner
        from kdam.db import ImageDB
        from kdam.config import Directory

        img_dir = tmp_path / "photos"
        img_dir.mkdir()
        _make_png(img_dir / "good.png")
        (img_dir / "bad.txt").write_text("not an image")
        (img_dir / "corrupt.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        dirs = [Directory(path=str(img_dir), recursive=False)]
        config = _make_config(dirs, tmp_path)
        db = ImageDB()
        new_images, _ = scanner.scan(config, db)
        assert len(new_images) == 1

    def test_full_pipeline_rotate_sets_avatar(self, tmp_path, monkeypatch):
        """Full scan→configure→rotate pipeline: avatar is set and state updated."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner, rotator
        from kdam import kde as kde_mod
        from kdam.db import ImageDB
        from kdam.config import Directory

        img_dir = tmp_path / "photos"
        img_dir.mkdir()
        img = _make_png(img_dir / "photo.png")

        dirs = [Directory(path=str(img_dir), recursive=False)]
        config = _make_config(dirs, tmp_path)
        db = ImageDB()
        scanner.scan(config, db)
        _configure_entry(db, str(img))

        mock_iface = MagicMock()
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            result = rotator.rotate(config, db, quiet=True)

        assert result is True
        mock_iface.SetIconFile.assert_called_once()
        assert config.state.last_image_path == str(img)

        log = tmp_path / "logs" / "rotation.log"
        assert log.exists()
        assert str(img) in log.read_text()

    def test_full_pipeline_multiple_images_rotates_all(self, tmp_path, monkeypatch):
        """With N images configured, N repeated rotations should each succeed."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner, rotator
        from kdam import kde as kde_mod
        from kdam.db import ImageDB
        from kdam.config import Directory

        img_dir = tmp_path / "photos"
        img_dir.mkdir()
        imgs = [_make_png(img_dir / f"p{i}.png", color=(i*30, i*20, 100)) for i in range(1, 5)]

        dirs = [Directory(path=str(img_dir), recursive=False)]
        config = _make_config(dirs, tmp_path)
        db = ImageDB()
        scanner.scan(config, db)
        for img in imgs:
            _configure_entry(db, str(img))

        mock_iface = MagicMock()
        successes = 0
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            for _ in range(4):
                r = rotator.rotate(config, db, quiet=True)
                if r is True:
                    successes += 1
        assert successes == 4
        assert mock_iface.SetIconFile.call_count == 4

    def test_state_persisted_after_rotation(self, tmp_path, monkeypatch):
        """Config state (last_image_path, last_rotation) must survive save/reload."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner, rotator
        from kdam import kde as kde_mod
        from kdam.db import ImageDB
        from kdam.config import Directory, load_config

        img_dir = tmp_path / "photos"
        img_dir.mkdir()
        img = _make_png(img_dir / "photo.png")

        dirs = [Directory(path=str(img_dir), recursive=False)]
        config = _make_config(dirs, tmp_path)
        db = ImageDB()
        scanner.scan(config, db)
        _configure_entry(db, str(img))

        mock_iface = MagicMock()
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            rotator.rotate(config, db, quiet=True)

        reloaded = load_config()
        assert reloaded.state.last_image_path == str(img)
        assert reloaded.state.last_rotation is not None

    def test_scan_idempotent_on_rescan(self, tmp_path, monkeypatch):
        """Scanning the same directory twice must not add duplicate DB entries."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner
        from kdam.db import ImageDB
        from kdam.config import Directory

        img_dir = tmp_path / "photos"
        img_dir.mkdir()
        _make_png(img_dir / "a.png")

        dirs = [Directory(path=str(img_dir), recursive=False)]
        config = _make_config(dirs, tmp_path)
        db = ImageDB()
        scanner.scan(config, db)
        first_count = len(db.all())
        scanner.scan(config, db)  # second scan
        assert len(db.all()) == first_count  # no duplicates


# ── End-to-end: dirs suspend → resume → rotate ────────────────────────────────

class TestDirsSuspendResumeRotate:

    def test_suspend_pauses_images(self, tmp_path, monkeypatch):
        """After dirs_suspend, all images from that dir should be disabled."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner
        from kdam.db import ImageDB
        from kdam.config import Directory, save_config
        from kdam.cli_dirs import dirs_suspend
        from click.testing import CliRunner

        img_dir = tmp_path / "photos"
        img_dir.mkdir()
        img = _make_png(img_dir / "photo.png")

        dirs = [Directory(path=str(img_dir), recursive=False)]
        config = _make_config(dirs, tmp_path)
        save_config(config)

        db = ImageDB()
        scanner.scan(config, db)
        _configure_entry(db, str(img))

        runner = CliRunner()
        result = runner.invoke(dirs_suspend, [str(img_dir)])
        assert result.exit_code == 0, result.output

        db2 = ImageDB()
        entry = db2.get(str(img))
        assert entry is not None
        assert entry.enabled is False
        assert entry.suspended_by_dir is True

    def test_resume_restores_images(self, tmp_path, monkeypatch):
        """After dirs_resume, images suspended by dirs_suspend must be re-enabled."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner
        from kdam.db import ImageDB
        from kdam.config import Directory, save_config
        from kdam.cli_dirs import dirs_suspend, dirs_resume
        from click.testing import CliRunner

        img_dir = tmp_path / "photos"
        img_dir.mkdir()
        img = _make_png(img_dir / "photo.png")

        dirs = [Directory(path=str(img_dir), recursive=False)]
        config = _make_config(dirs, tmp_path)
        save_config(config)

        db = ImageDB()
        scanner.scan(config, db)
        _configure_entry(db, str(img))

        runner = CliRunner()
        runner.invoke(dirs_suspend, [str(img_dir)])
        result = runner.invoke(dirs_resume, [str(img_dir)])
        assert result.exit_code == 0

        db2 = ImageDB()
        entry = db2.get(str(img))
        assert entry is not None
        assert entry.enabled is True
        assert entry.suspended_by_dir is False

    def test_rotate_skips_suspended_images(self, tmp_path, monkeypatch):
        """Images suspended by dirs_suspend must not appear in rotation."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import scanner, rotator
        from kdam import kde as kde_mod
        from kdam.db import ImageDB
        from kdam.config import Directory, save_config
        from kdam.cli_dirs import dirs_suspend
        from click.testing import CliRunner

        img_dir = tmp_path / "photos"
        img_dir.mkdir()
        img = _make_png(img_dir / "photo.png")

        dirs = [Directory(path=str(img_dir), recursive=False)]
        config = _make_config(dirs, tmp_path)
        save_config(config)

        db = ImageDB()
        scanner.scan(config, db)
        _configure_entry(db, str(img))

        runner = CliRunner()
        runner.invoke(dirs_suspend, [str(img_dir)])

        db2 = ImageDB()
        mock_iface = MagicMock()
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            result = rotator.rotate(config, db2, quiet=True)

        assert result is None   # pool empty after suspend
        mock_iface.SetIconFile.assert_not_called()
