"""Tests for newly fixed edge-cases identified in the healthcheck audit."""

from __future__ import annotations

import json
import os
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from helpers import _patch_paths  # shared: redirects all paths to tmp, silences notify
from PIL import Image

# ── shared helpers ────────────────────────────────────────────────────────────


def _make_png(path: Path, size=(64, 64)) -> Path:
    Image.new("RGB", size, color=(100, 150, 200)).save(str(path), "PNG")
    return path


# ── H3: corrupt config recovery ──────────────────────────────────────────────


class TestCorruptConfigRecovery:
    def test_corrupt_json_renames_to_dot_corrupt(self, tmp_path, monkeypatch):
        """A corrupt config.json should be renamed .json.corrupt, not silently lost."""
        import kdam.config as cfg

        monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(cfg, "_LOCK_FILE", tmp_path / ".lock")

        # Write a corrupt file
        (tmp_path / "config.json").write_text("{ invalid json !!!")
        result = cfg.load_config()

        # Should return defaults without crashing
        assert result is not None
        # Corrupt file should be preserved, not silently overwritten
        assert (tmp_path / "config.json.corrupt").exists()
        # config.json itself should no longer exist (renamed away)
        assert not (tmp_path / "config.json").exists()

    def test_valid_config_loads_normally(self, tmp_path, monkeypatch):
        import kdam.config as cfg

        monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(cfg, "_LOCK_FILE", tmp_path / ".lock")

        original = cfg.Config()
        original.rotation.interval_hours = 4
        cfg.save_config(original)
        loaded = cfg.load_config()
        assert loaded.rotation.interval_hours == 4


class TestCorruptDBRecovery:
    def test_corrupt_images_json_renames_to_dot_corrupt(self, tmp_path, monkeypatch):
        """A corrupt images.json should be renamed .json.corrupt, not silently lost."""
        import kdam.config as cfg
        import kdam.db as db_mod

        monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg, "_LOCK_FILE", tmp_path / ".lock")
        monkeypatch.setattr(db_mod, "IMAGES_FILE", tmp_path / "images.json")

        (tmp_path / "images.json").write_text("not valid json at all !!!")
        db = db_mod.ImageDB()  # load() is called in __init__

        assert len(db) == 0  # graceful default
        assert (tmp_path / "images.json.corrupt").exists()


# ── H2: sequential rotation is deterministic ─────────────────────────────────


class TestSequentialRotationSort:
    def test_sequential_order_is_alphabetical(self, tmp_path, monkeypatch):
        """Sequential rotation must follow alphabetical order, not insertion order."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import rotator
        from kdam.config import Config
        from kdam.db import ImageDB, ImageEntry

        db = ImageDB()
        # Insert in reverse alphabetical order
        for name in ["c.png", "a.png", "b.png"]:
            p = _make_png(tmp_path / name)
            db.set(str(p), ImageEntry(configured=True, enabled=True))

        config = Config()
        config.rotation.order = "sequential"
        config.state.current_index = 0

        first = rotator.get_next_image(config, db)
        second = rotator.get_next_image(config, db)
        third = rotator.get_next_image(config, db)

        names = [Path(p).name for p in [first, second, third] if p]
        assert names == sorted(names), f"Expected alphabetical order, got: {names}"


# ── H1: zip-slip prevention ───────────────────────────────────────────────────


class TestZipSlipPrevention:
    def test_traversal_path_in_archive_is_blocked(self, tmp_path, monkeypatch):
        """Malicious tar entries with ../ should not escape the target directory."""
        import kdam.backup as bk
        import kdam.config as cfg

        monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(cfg, "IMAGES_FILE", tmp_path / "images.json")
        monkeypatch.setattr(cfg, "BACKUP_FILE", tmp_path / "backup.png")
        monkeypatch.setattr(cfg, "_LOCK_FILE", tmp_path / ".lock")
        monkeypatch.setattr(bk, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(bk, "CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(bk, "IMAGES_FILE", tmp_path / "images.json")
        monkeypatch.setattr(bk, "BACKUP_FILE", tmp_path / "backup.png")

        # Build a malicious archive with a traversal entry
        evil_archive = tmp_path / "evil.tar.gz"
        victim_file = tmp_path / "victim.txt"
        victim_file.write_text("original")

        import io

        with tarfile.open(str(evil_archive), "w:gz") as tf:
            # Write a minimal manifest
            manifest = json.dumps(
                {
                    "version": 1,
                    "created_at": "2024-01-01",
                    "original_home": str(Path.home()),
                    "include_images": True,
                    "image_count": 1,
                    "directories": [],
                    "panic_path": None,
                    "config_only": False,
                }
            ).encode()
            ti = tarfile.TarInfo(name="kdam_manifest.json")
            ti.size = len(manifest)
            tf.addfile(ti, io.BytesIO(manifest))

            # Malicious image entry attempting directory traversal
            payload = b"pwned"
            rel = os.path.relpath(str(victim_file), str(tmp_path / "images"))
            ti2 = tarfile.TarInfo(name=f"images/{rel}")
            ti2.size = len(payload)
            tf.addfile(ti2, io.BytesIO(payload))

        img_dest = tmp_path / "images"
        img_dest.mkdir()
        ok, _ = bk.restore_backup(evil_archive, target_images_dir=str(img_dest))

        # The victim file must be untouched
        assert victim_file.read_text() == "original", "Zip-slip traversal was NOT blocked!"


# ── Scanner: symlink safety ───────────────────────────────────────────────────


class TestSymlinkSafety:
    def test_recursive_scan_does_not_follow_symlinks(self, tmp_path):
        """Recursive walk with followlinks=False must not follow directory symlinks."""
        from kdam.scanner import _walk_directory

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        _make_png(real_dir / "img.png")

        # Symlink pointing back to parent — would cause infinite loop if followed
        link = tmp_path / "link_to_parent"
        link.symlink_to(tmp_path)

        # Should not raise RecursionError or return duplicates
        found = _walk_directory(tmp_path, recursive=True)
        names = [p.name for p in found]
        assert names.count("img.png") == 1


# ── Dirs commands ─────────────────────────────────────────────────────────────


class TestDirsEditCommand:
    def test_dirs_edit_sets_recursive(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import Directory, load_config, save_config

        d = tmp_path / "pics"
        d.mkdir()
        cfg = load_config()
        cfg.directories.append(Directory(path=str(d), recursive=False))
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["dirs", "edit", str(d), "--recursive"])
        assert result.exit_code == 0
        updated = load_config()
        assert updated.directories[0].recursive is True

    def test_dirs_remove_clears_directory(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import Directory, load_config, save_config

        d = tmp_path / "pics"
        d.mkdir()
        cfg = load_config()
        cfg.directories.append(Directory(path=str(d)))
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["dirs", "remove", str(d)])
        assert result.exit_code == 0
        updated = load_config()
        assert len(updated.directories) == 0


# ── Panic operations ──────────────────────────────────────────────────────────


class TestPanicCommands:
    def test_panic_show_no_panic_set(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["panic", "show"])
        assert result.exit_code == 0
        assert "none" in result.output.lower() or "backup" in result.output.lower()

    def test_panic_show_with_missing_file_reports_missing(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.panic.path = str(tmp_path / "nonexistent.jpg")
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["panic", "show"])
        assert result.exit_code == 0
        assert "MISSING" in result.output or "missing" in result.output.lower()

    def test_panic_snapshot_fallback_used_in_reset(self, tmp_path, monkeypatch):
        """panic reset falls back to backup.png when no explicit panic image is set."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        # Provide a real backup.png but no panic.path
        backup = tmp_path / "backup.png"
        _make_png(backup, size=(256, 256))

        with (
            patch("kdam.kde.set_avatar", return_value=True),
            patch("kdam.kde.backup_current_avatar", return_value=True),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["panic", "reset"])

        # Should succeed (backup.png used as fallback)
        assert "failed" not in result.output.lower(), result.output


# ── Config corruption guard: save_config cleans up .tmp on error ──────────────


class TestSaveConfigTempCleanup:
    def test_no_tmp_file_left_on_disk_after_success(self, tmp_path, monkeypatch):
        import kdam.config as cfg

        monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(cfg, "_LOCK_FILE", tmp_path / ".lock")

        cfg.save_config(cfg.Config())
        assert not (tmp_path / "config.tmp").exists()
        assert (tmp_path / "config.json").exists()


# ── New feature: kdam next --dry-run ─────────────────────────────────────────


class TestNextDryRun:
    def test_dry_run_shows_candidate_without_rotating(self, tmp_path, monkeypatch):
        """--dry-run prints the next image without changing state."""
        from kdam.cli import cli

        _patch_paths(tmp_path, monkeypatch)

        # Add a configured image
        from kdam.db import CropInfo, ImageDB, ImageEntry

        img = _make_png(tmp_path / "test.png")
        db = ImageDB()
        db.set(
            str(img), ImageEntry(configured=True, enabled=True, weight=1.0, crop=CropInfo(x=0, y=0, size=64))
        )
        db.save()

        # Mock get_next_image to return our image path
        import kdam.rotator as rot_mod

        monkeypatch.setattr(rot_mod, "get_next_image", lambda cfg, db: str(img))
        # rotate should NOT be called
        rotate_called = []
        monkeypatch.setattr(rot_mod, "rotate", lambda *a, **kw: rotate_called.append(1) or True)

        runner = CliRunner()
        result = runner.invoke(cli, ["next", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "test.png" in result.output
        assert rotate_called == [], "rotate() must not be called on --dry-run"

    def test_dry_run_no_images_shows_warning(self, tmp_path, monkeypatch):
        """--dry-run with empty pool prints a warning."""
        from kdam.cli import cli

        _patch_paths(tmp_path, monkeypatch)

        import kdam.rotator as rot_mod

        monkeypatch.setattr(rot_mod, "get_next_image", lambda cfg, db: None)

        runner = CliRunner()
        result = runner.invoke(cli, ["next", "--dry-run"])
        assert result.exit_code == 0
        assert "No configured" in result.output


# ── New feature: kdam remove --all-unconfigured ───────────────────────────────


class TestRemoveAllUnconfigured:
    def test_removes_all_unconfigured_with_confirm(self, tmp_path, monkeypatch):
        """--all-unconfigured removes all images with configured=False."""
        from kdam.cli import cli
        from kdam.db import ImageDB, ImageEntry

        _patch_paths(tmp_path, monkeypatch)

        img1 = _make_png(tmp_path / "a.png")
        img2 = _make_png(tmp_path / "b.png")
        img3 = _make_png(tmp_path / "c.png")

        db = ImageDB()
        db.set(str(img1), ImageEntry(configured=False, enabled=False))
        db.set(str(img2), ImageEntry(configured=False, enabled=False))
        db.set(str(img3), ImageEntry(configured=True, enabled=True, weight=1.0))
        db.save()

        runner = CliRunner()
        result = runner.invoke(cli, ["remove", "--all-unconfigured"], input="y\n")
        assert result.exit_code == 0, result.output

        db2 = ImageDB()
        remaining = db2.all()
        assert str(img1) not in remaining
        assert str(img2) not in remaining
        assert str(img3) in remaining  # configured image stays

    def test_all_unconfigured_empty_pool(self, tmp_path, monkeypatch):
        """--all-unconfigured with none unconfigured prints green message."""
        from kdam.cli import cli
        from kdam.db import ImageDB, ImageEntry

        _patch_paths(tmp_path, monkeypatch)

        img = _make_png(tmp_path / "configured.png")
        db = ImageDB()
        db.set(str(img), ImageEntry(configured=True, enabled=True, weight=1.0))
        db.save()

        runner = CliRunner()
        result = runner.invoke(cli, ["remove", "--all-unconfigured"])
        assert result.exit_code == 0
        assert "No unconfigured" in result.output

    def test_all_unconfigured_delete_file_removes_from_disk(self, tmp_path, monkeypatch):
        """Regression: --all-unconfigured --delete-file must actually unlink source files.

        Bug: the --all-unconfigured branch ignored --delete-file entirely — it removed
        entries from the DB and printed '✓ Removed' but never called Path.unlink().
        """
        from kdam.cli import cli
        from kdam.db import ImageDB, ImageEntry

        _patch_paths(tmp_path, monkeypatch)

        img1 = _make_png(tmp_path / "del1.png")
        img2 = _make_png(tmp_path / "del2.png")
        keep = _make_png(tmp_path / "keep.png")

        db = ImageDB()
        db.set(str(img1), ImageEntry(configured=False, enabled=False))
        db.set(str(img2), ImageEntry(configured=False, enabled=False))
        db.set(str(keep), ImageEntry(configured=True, enabled=True, weight=1.0))
        db.save()

        runner = CliRunner()
        result = runner.invoke(cli, ["remove", "--all-unconfigured", "--delete-file"], input="y\n")
        assert result.exit_code == 0, result.output

        # Files must be gone from disk
        assert not img1.exists(), f"{img1.name} still on disk after --delete-file"
        assert not img2.exists(), f"{img2.name} still on disk after --delete-file"
        # Configured image must survive
        assert keep.exists(), "Configured image must not be deleted"
        # DB must not contain the deleted entries
        db2 = ImageDB()
        assert str(img1) not in db2.all()
        assert str(img2) not in db2.all()
        assert str(keep) in db2.all()

    def test_all_unconfigured_without_delete_file_keeps_files(self, tmp_path, monkeypatch):
        """Without --delete-file, source files must remain on disk after registry removal."""
        from kdam.cli import cli
        from kdam.db import ImageDB, ImageEntry

        _patch_paths(tmp_path, monkeypatch)

        img = _make_png(tmp_path / "stays.png")
        db = ImageDB()
        db.set(str(img), ImageEntry(configured=False, enabled=False))
        db.save()

        runner = CliRunner()
        result = runner.invoke(cli, ["remove", "--all-unconfigured"], input="y\n")
        assert result.exit_code == 0

        # Removed from registry
        db2 = ImageDB()
        assert str(img) not in db2.all()
        # But still on disk
        assert img.exists(), "Source file must NOT be deleted without --delete-file"


# ── New feature: orphaned images in kdam status ───────────────────────────────


class TestOrphanedStatus:
    def test_orphaned_images_shown_in_status(self, tmp_path, monkeypatch):
        """status shows orphaned count when images exist outside configured dirs."""
        from kdam.cli import cli
        from kdam.db import CropInfo, ImageDB, ImageEntry

        _patch_paths(tmp_path, monkeypatch)

        # Image is NOT under any configured directory → orphaned
        img = _make_png(tmp_path / "orphan.png")
        db = ImageDB()
        db.set(
            str(img), ImageEntry(configured=True, enabled=True, weight=1.0, crop=CropInfo(x=0, y=0, size=64))
        )
        db.save()

        monkeypatch.setattr("kdam.cli._timer_active", lambda: False)

        runner = CliRunner()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0, result.output
        assert "Orphaned" in result.output or "orphaned" in result.output.lower()


# ── New feature: backup restore auto-creates --images-dir ────────────────────


class TestBackupRestoreAutoCreateDir:
    def test_restore_creates_missing_images_dir(self, tmp_path, monkeypatch):
        """backup restore --images-dir creates the target dir if it doesn't exist."""
        from kdam.cli import cli

        _patch_paths(tmp_path, monkeypatch)

        # Create a minimal backup archive
        import kdam.backup as bk

        archive = tmp_path / "test_backup.tar.gz"
        bk.create_backup(str(archive), include_images=False, config_only=True)

        new_dir = tmp_path / "new" / "nested" / "dir"
        assert not new_dir.exists()

        runner = CliRunner()
        result = runner.invoke(
            cli, ["backup", "restore", str(archive), "--images-dir", str(new_dir), "--force"]
        )
        assert result.exit_code == 0, result.output
        assert new_dir.exists(), "restore should auto-create --images-dir"


# ── Integration: enable / disable / toggle with mocked systemctl ─────────────


class TestEnableDisableToggle:
    def _make_systemctl_mock(self, returncode=0):
        """Return a mock CompletedProcess for systemctl calls."""
        mock = MagicMock()
        mock.returncode = returncode
        mock.stdout = ""
        mock.stderr = ""
        return mock

    def test_enable_writes_systemd_files_and_calls_systemctl(self, tmp_path, monkeypatch):
        from kdam.cli import cli

        _patch_paths(tmp_path, monkeypatch)

        systemd_calls = []

        def fake_systemd(*args):
            systemd_calls.append(args)
            return self._make_systemctl_mock(0)

        monkeypatch.setattr("kdam.cli._systemd", fake_systemd)
        monkeypatch.setattr("kdam.cli._write_systemd_files", lambda cfg: None)
        monkeypatch.setattr("kdam.cli._timer_active", lambda: False)

        runner = CliRunner()
        result = runner.invoke(cli, ["enable"])
        assert result.exit_code == 0, result.output
        # Should have called daemon-reload and enable/start
        all_calls = [" ".join(c) for c in systemd_calls]
        assert any("daemon-reload" in c for c in all_calls)
        assert any("enable" in c for c in all_calls)

    def test_disable_stops_timer_and_updates_state(self, tmp_path, monkeypatch):
        import kdam.config as cfg_mod
        from kdam.cli import cli

        _patch_paths(tmp_path, monkeypatch)

        # Set rotation as enabled
        cfg = cfg_mod.load_config()
        cfg.state.enabled = True
        cfg_mod.save_config(cfg)

        systemd_calls = []

        def fake_systemd(*args):
            systemd_calls.append(args)
            return self._make_systemctl_mock(0)

        monkeypatch.setattr("kdam.cli._systemd", fake_systemd)
        monkeypatch.setattr("kdam.cli._timer_active", lambda: True)

        runner = CliRunner()
        result = runner.invoke(cli, ["disable"])
        assert result.exit_code == 0, result.output
        all_calls = [" ".join(c) for c in systemd_calls]
        assert any("stop" in c for c in all_calls) or any("disable" in c for c in all_calls)

        # State should be disabled
        cfg2 = cfg_mod.load_config()
        assert not cfg2.state.enabled

    def test_toggle_enables_when_disabled(self, tmp_path, monkeypatch):
        import kdam.config as cfg_mod
        from kdam.cli import cli

        _patch_paths(tmp_path, monkeypatch)

        cfg = cfg_mod.load_config()
        cfg.state.enabled = False
        cfg.state.frozen = False
        cfg_mod.save_config(cfg)

        monkeypatch.setattr("kdam.cli._systemd", lambda *a: self._make_systemctl_mock(0))
        monkeypatch.setattr("kdam.cli._write_systemd_files", lambda cfg: None)
        monkeypatch.setattr("kdam.cli._timer_active", lambda: False)
        monkeypatch.setattr("kdam.rotator.rotate", lambda *a, **kw: False)

        runner = CliRunner()
        result = runner.invoke(cli, ["toggle"])
        assert result.exit_code == 0, result.output
        cfg2 = cfg_mod.load_config()
        assert cfg2.state.enabled

    def test_toggle_unfreezes_when_frozen(self, tmp_path, monkeypatch):
        import kdam.config as cfg_mod
        from kdam.cli import cli

        _patch_paths(tmp_path, monkeypatch)

        cfg = cfg_mod.load_config()
        cfg.state.enabled = True
        cfg.state.frozen = True
        cfg.state.frozen_until = None
        cfg_mod.save_config(cfg)

        monkeypatch.setattr("kdam.cli._systemd", lambda *a: self._make_systemctl_mock(0))
        monkeypatch.setattr("kdam.cli._timer_active", lambda: True)

        runner = CliRunner()
        result = runner.invoke(cli, ["toggle"])
        assert result.exit_code == 0, result.output
        cfg2 = cfg_mod.load_config()
        assert not cfg2.state.frozen, "toggle on frozen should unfreeze"
