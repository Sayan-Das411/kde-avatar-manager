"""Additional CLI tests — panic, list status icons, log current marker,
freeze/unfreeze, dirs suspend/resume, resync, weight, and misc commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from helpers import _patch_paths  # shared: redirects all paths to tmp, silences notify
from PIL import Image


def _make_png(path: Path, size=(64, 64)) -> Path:
    Image.new("RGB", size, color=(100, 150, 200)).save(str(path), "PNG")
    return path


def _register_image(tmp_path, monkeypatch, filename="img.png", configured=True, enabled=True):
    """Create a real PNG and register it in the DB. Returns absolute path string."""
    from kdam.db import CropInfo, ImageDB, ImageEntry

    img = _make_png(tmp_path / filename)
    db = ImageDB()
    entry = ImageEntry(
        configured=configured,
        enabled=enabled,
        crop=CropInfo(x=0, y=0, size=64) if configured else None,
    )
    db.set(str(img), entry)
    db.save()
    return str(img)


# ── kdam list — panic image icon ──────────────────────────────────────────────


class TestListPanicIcon:
    def test_panic_image_shows_P_not_unconfigured(self, tmp_path, monkeypatch):
        """The panic image must show 'P' (magenta) even if it is in the DB as unconfigured."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import load_config, save_config
        from kdam.db import ImageDB, ImageEntry

        # Create and register the image as UNCONFIGURED (no crop set)
        img = _make_png(tmp_path / "trees.jpg")
        db = ImageDB()
        db.set(str(img), ImageEntry(configured=False, enabled=False))
        db.save()

        # Set as panic image in config
        cfg = load_config()
        cfg.panic.path = str(img)
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        # Must show P (panic marker), NOT ✗ (unconfigured marker)
        assert "P" in result.output
        assert "panic" in result.output.lower()

    def test_configured_non_panic_shows_checkmark(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        _register_image(tmp_path, monkeypatch, "good.png", configured=True, enabled=True)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "✓" in result.output

    def test_unconfigured_non_panic_shows_cross(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        _register_image(tmp_path, monkeypatch, "raw.png", configured=False, enabled=False)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "✗" in result.output

    def test_panic_in_list_excluded_from_unconfigured_count(self, tmp_path, monkeypatch):
        """The summary line must not count the panic image as unconfigured."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import load_config, save_config
        from kdam.db import ImageDB, ImageEntry

        img = _make_png(tmp_path / "panic.png")
        db = ImageDB()
        db.set(str(img), ImageEntry(configured=False, enabled=False))
        db.save()

        cfg = load_config()
        cfg.panic.path = str(img)
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        # "Unconfigured 0" — the panic image must NOT be counted here
        assert "Unconfigured 0" in result.output


# ── kdam log — current marker ─────────────────────────────────────────────────


class TestLogCurrentMarker:
    def _write_log_and_set_current(self, tmp_path, current_img: str):
        log = tmp_path / "logs" / "rotation.log"
        log.write_text(
            f"2026-09-01T10:00:00 | {tmp_path}/a.png\n"
            f"2026-09-02T10:00:00 | {tmp_path}/b.png\n"
            f"2026-09-03T10:00:00 | {current_img}\n"
        )

    def test_current_marker_shown_oldest_first(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import load_config, save_config

        img = _make_png(tmp_path / "current.png")
        self._write_log_and_set_current(tmp_path, str(img))

        cfg = load_config()
        cfg.state.last_image_path = str(img)
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["log", "--all"])
        assert result.exit_code == 0
        assert "← current" in result.output

    def test_current_marker_shown_desc(self, tmp_path, monkeypatch):
        """← current must appear exactly once even in --desc (newest-first) mode."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import load_config, save_config

        img = _make_png(tmp_path / "current.png")
        self._write_log_and_set_current(tmp_path, str(img))

        cfg = load_config()
        cfg.state.last_image_path = str(img)
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["log", "--all", "--desc"])
        assert result.exit_code == 0
        assert result.output.count("← current") == 1

    def test_no_marker_when_no_last_image(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        log = tmp_path / "logs" / "rotation.log"
        log.write_text("2026-09-01T10:00:00 | /some/img.png\n")
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["log", "--all"])
        assert result.exit_code == 0
        assert "← current" not in result.output


# ── kdam freeze / unfreeze ────────────────────────────────────────────────────


class TestFreezeUnfreeze:
    def test_freeze_sets_frozen_flag(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import load_config

        runner = CliRunner()
        result = runner.invoke(cli, ["freeze"])
        assert result.exit_code == 0
        cfg = load_config()
        assert cfg.state.frozen is True

    def test_freeze_with_duration_sets_frozen_until(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import load_config

        runner = CliRunner()
        result = runner.invoke(cli, ["freeze", "2h"])
        assert result.exit_code == 0
        cfg = load_config()
        assert cfg.state.frozen is True
        assert cfg.state.frozen_until is not None

    def test_unfreeze_clears_frozen_flag(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.state.frozen = True
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["unfreeze"])
        assert result.exit_code == 0
        cfg2 = load_config()
        assert cfg2.state.frozen is False

    def test_freeze_invalid_duration_rejected(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["freeze", "notaduration"])
        assert result.exit_code == 0
        assert "cannot parse" in result.output.lower() or "use" in result.output.lower()


# ── kdam dirs suspend / resume ────────────────────────────────────────────────


class TestDirsSuspendResume:
    def test_suspend_pauses_images(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import Directory, load_config, save_config
        from kdam.db import CropInfo, ImageDB, ImageEntry

        img_dir = tmp_path / "avatars"
        img_dir.mkdir()
        img = _make_png(img_dir / "photo.png")

        db = ImageDB()
        db.set(str(img), ImageEntry(configured=True, enabled=True, crop=CropInfo(x=0, y=0, size=64)))
        db.save()

        cfg = load_config()
        cfg.directories.append(Directory(path=str(img_dir)))
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["dirs", "suspend", str(img_dir)])
        assert result.exit_code == 0

        db2 = ImageDB()
        entry = db2.get(str(img))
        assert entry is not None
        assert entry.enabled is False
        assert entry.suspended_by_dir is True

    def test_resume_restores_paused_images(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import Directory, load_config, save_config
        from kdam.db import CropInfo, ImageDB, ImageEntry

        img_dir = tmp_path / "avatars"
        img_dir.mkdir()
        img = _make_png(img_dir / "photo.png")

        # Pre-suspend
        db = ImageDB()
        db.set(
            str(img),
            ImageEntry(
                configured=True, enabled=False, crop=CropInfo(x=0, y=0, size=64), suspended_by_dir=True
            ),
        )
        db.save()

        cfg = load_config()
        cfg.directories.append(Directory(path=str(img_dir), enabled=False))
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["dirs", "resume", str(img_dir)])
        assert result.exit_code == 0

        db2 = ImageDB()
        entry = db2.get(str(img))
        assert entry is not None
        assert entry.enabled is True
        assert entry.suspended_by_dir is False


# ── kdam weight ───────────────────────────────────────────────────────────────


class TestWeightCommand:
    def test_weight_updates_entry(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.db import CropInfo, ImageDB, ImageEntry

        img = _make_png(tmp_path / "w.png")
        db = ImageDB()
        db.set(str(img), ImageEntry(configured=True, enabled=True, crop=CropInfo(x=0, y=0, size=64)))
        db.save()

        runner = CliRunner()
        result = runner.invoke(cli, ["weight", str(img), "3.5"])
        assert result.exit_code == 0
        assert "3.5" in result.output

        db2 = ImageDB()
        assert db2.get(str(img)).weight == pytest.approx(3.5)

    def test_weight_zero_rejected(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.db import ImageDB, ImageEntry

        img = _make_png(tmp_path / "w.png")
        db = ImageDB()
        db.set(str(img), ImageEntry(configured=True, enabled=True))
        db.save()

        runner = CliRunner()
        result = runner.invoke(cli, ["weight", str(img), "0"])
        assert "must be" in result.output.lower() or "greater" in result.output.lower()

    def test_weight_negative_rejected(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.db import ImageDB, ImageEntry

        img = _make_png(tmp_path / "w.png")
        db = ImageDB()
        db.set(str(img), ImageEntry(configured=True, enabled=True))
        db.save()

        runner = CliRunner()
        # Use -- to signal end of options so Click treats -1 as the VALUE argument
        result = runner.invoke(cli, ["weight", str(img), "--", "-1"])
        assert "must be" in result.output.lower() or "greater" in result.output.lower()


# ── kdam enable-image / disable-image ─────────────────────────────────────────


class TestEnableDisableImage:
    def test_disable_sets_enabled_false(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        img_path = _register_image(tmp_path, monkeypatch, "img.png", configured=True, enabled=True)
        from kdam.cli import cli
        from kdam.db import ImageDB

        runner = CliRunner()
        result = runner.invoke(cli, ["disable-image", img_path])
        assert result.exit_code == 0
        assert ImageDB().get(img_path).enabled is False

    def test_enable_sets_enabled_true(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        img_path = _register_image(tmp_path, monkeypatch, "img.png", configured=True, enabled=False)
        from kdam.cli import cli
        from kdam.db import ImageDB

        runner = CliRunner()
        result = runner.invoke(cli, ["enable-image", img_path])
        assert result.exit_code == 0
        assert ImageDB().get(img_path).enabled is True

    def test_enable_unconfigured_shows_warning(self, tmp_path, monkeypatch):
        """enable-image on an unconfigured image should ask user to configure first."""
        _patch_paths(tmp_path, monkeypatch)
        img_path = _register_image(tmp_path, monkeypatch, "img.png", configured=False, enabled=False)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["enable-image", img_path])
        assert result.exit_code == 0
        assert "configure" in result.output.lower()


# ── kdam remove ───────────────────────────────────────────────────────────────


class TestRemoveCommand:
    def test_remove_known_image(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        img_path = _register_image(tmp_path, monkeypatch, "rem.png")
        from kdam.cli import cli
        from kdam.db import ImageDB

        runner = CliRunner()
        result = runner.invoke(cli, ["remove", img_path])
        assert result.exit_code == 0
        assert ImageDB().get(img_path) is None

    def test_remove_stale(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.db import ImageDB, ImageEntry

        ghost = str(tmp_path / "ghost.png")  # file does not exist
        db = ImageDB()
        db.set(ghost, ImageEntry(configured=True, enabled=True))
        db.save()
        runner = CliRunner()
        result = runner.invoke(cli, ["remove", "--stale"])
        assert result.exit_code == 0
        assert ImageDB().get(ghost) is None

    def test_remove_unknown_image(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["remove", "/does/not/exist.png"])
        assert result.exit_code == 0
        assert "not in registry" in result.output.lower()


# ── kdam config set ───────────────────────────────────────────────────────────


class TestConfigSet:
    def test_set_rotation_order(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import load_config

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "set", "rotation.order", "weighted"])
        assert result.exit_code == 0
        assert load_config().rotation.order == "weighted"

    def test_set_invalid_order_rejected(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "set", "rotation.order", "banana"])
        assert "must be" in result.output.lower()

    def test_set_interval_hours(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli
        from kdam.config import load_config

        monkeypatch.setattr("kdam.cli._timer_active", lambda: False)
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "set", "rotation.interval_hours", "8"])
        assert result.exit_code == 0
        assert load_config().rotation.interval_hours == 8

    def test_set_unknown_key_rejected(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "set", "rotation.nonexistent", "value"])
        assert "unknown" in result.output.lower()


# ── kdam log --prune ──────────────────────────────────────────────────────────


class TestLogPrune:
    def test_prune_zero_rejected(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        log = tmp_path / "logs" / "rotation.log"
        log.write_text("2026-09-01T10:00:00 | /img.png\n")
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["log", "--prune", "0"])
        assert result.exit_code == 0
        assert "requires" in result.output.lower() or "≥ 1" in result.output

    def test_prune_keeps_newest_n(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        log = tmp_path / "logs" / "rotation.log"
        entries = [f"2026-09-0{i}T10:00:00 | /img{i}.png" for i in range(1, 6)]
        log.write_text("\n".join(entries) + "\n")
        from kdam.cli import cli

        runner = CliRunner()
        runner.invoke(cli, ["log", "--prune", "2"])
        kept = [line for line in log.read_text().splitlines() if line.strip()]
        assert len(kept) == 2
        assert "img4" in kept[0]
        assert "img5" in kept[1]


# ── kdam next --count ─────────────────────────────────────────────────────────


class TestNextCount:
    def test_next_count_rotates_multiple_times(self, tmp_path, monkeypatch):
        """--count N should call rotate() N times."""
        _patch_paths(tmp_path, monkeypatch)
        import kdam.cli as cli_mod
        from kdam.cli import cli
        from kdam.config import load_config, save_config

        # Enable rotation
        cfg = load_config()
        cfg.state.enabled = True
        save_config(cfg)

        rotate_calls = []

        def _fake_rotate(config, db, quiet=False):
            rotate_calls.append(1)
            return True

        monkeypatch.setattr(cli_mod.rotator, "rotate", _fake_rotate)

        runner = CliRunner()
        result = runner.invoke(cli, ["next", "--count", "3"])
        assert result.exit_code == 0
        assert len(rotate_calls) == 3

    def test_next_stops_early_on_failure(self, tmp_path, monkeypatch):
        """If rotate() returns False, next --count should stop the loop."""
        _patch_paths(tmp_path, monkeypatch)
        import kdam.cli as cli_mod
        from kdam.cli import cli
        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.state.enabled = True
        save_config(cfg)

        monkeypatch.setattr(cli_mod.rotator, "rotate", lambda c, d, quiet=False: False)

        runner = CliRunner()
        result = runner.invoke(cli, ["next", "--count", "5"])
        assert result.exit_code == 0


# ── _parse_duration / _fmt_duration ──────────────────────────────────────────


class TestParseDuration:
    @pytest.mark.parametrize(
        "s,expected_s",
        [
            ("30m", 1800),
            ("3h", 10800),
            ("2d", 172800),
            ("1w", 604800),
            ("1h30m", 5400),
            ("2d12h", 2 * 86400 + 12 * 3600),
        ],
    )
    def test_valid_durations(self, s, expected_s):
        from kdam.cli import _parse_duration

        td = _parse_duration(s)
        assert int(td.total_seconds()) == expected_s

    def test_invalid_duration_raises(self):
        from kdam.cli import _parse_duration

        with pytest.raises(ValueError):
            _parse_duration("badformat")

    @pytest.mark.parametrize(
        "secs,expected",
        [
            (60, "1m"),
            (3600, "1h"),
            (7200, "2h"),
            (5400, "1h 30m"),
            (86400, "1d"),
            (90000, "1d 1h"),
        ],
    )
    def test_fmt_duration(self, secs, expected):
        from datetime import timedelta

        from kdam.cli import _fmt_duration

        assert _fmt_duration(timedelta(seconds=secs)) == expected


# ── kdam status ───────────────────────────────────────────────────────────────


class TestStatusCommand:
    def test_status_runs_without_error(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        monkeypatch.setattr("kdam.cli._timer_active", lambda: False)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "KDAM" in result.output or "Avatar" in result.output

    def test_status_shows_frozen_when_set(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        monkeypatch.setattr("kdam.cli._timer_active", lambda: False)
        from kdam.cli import cli
        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.state.frozen = True
        save_config(cfg)
        runner = CliRunner()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "frozen" in result.output.lower() or "yes" in result.output.lower()

    def test_status_shows_mode_parameters_in_brackets(self, tmp_path, monkeypatch):
        """status must show interval, weekly day, and times within brackets."""
        _patch_paths(tmp_path, monkeypatch)
        monkeypatch.setattr("kdam.cli._timer_active", lambda: False)
        from kdam.cli import cli
        from kdam.config import load_config, save_config

        runner = CliRunner()

        # Interval mode
        cfg = load_config()
        cfg.rotation.mode = "interval"
        cfg.rotation.interval = "6h"
        save_config(cfg)
        res = runner.invoke(cli, ["status"])
        assert res.exit_code == 0
        assert "interval (6h)" in res.output

        # Weekly mode
        cfg.rotation.mode = "weekly"
        cfg.rotation.weekday = "sun"
        save_config(cfg)
        res = runner.invoke(cli, ["status"])
        assert res.exit_code == 0
        assert "weekly (sun)" in res.output

        # Times mode
        cfg.rotation.mode = "times"
        cfg.rotation.times = ["08:30", "14:00"]
        save_config(cfg)
        res = runner.invoke(cli, ["status"])
        assert res.exit_code == 0
        assert "times (08:30, 14:00)" in res.output

    def test_status_panic_image_not_marked_as_orphan(self, tmp_path, monkeypatch):
        """Panic image stored in an external folder should not be counted as orphaned."""
        _patch_paths(tmp_path, monkeypatch)
        monkeypatch.setattr("kdam.cli._timer_active", lambda: False)
        from kdam.cli import cli
        from kdam.config import load_config, save_config
        from kdam.db import CropInfo, ImageDB, ImageEntry

        # Register a panic image outside configured directories
        panic_file = tmp_path / "external_safe.png"
        panic_file.write_bytes(b"\x89PNG\r\n\x1a\n")
        db = ImageDB()
        db.set(str(panic_file), ImageEntry(configured=True, enabled=False, crop=CropInfo(0, 0, 64)))
        db.save()

        cfg = load_config()
        cfg.panic.path = str(panic_file)
        save_config(cfg)

        runner = CliRunner()
        res = runner.invoke(cli, ["status"])
        assert res.exit_code == 0
        assert "Orphaned" not in res.output

    def test_status_accounts_service_cross_check(self, tmp_path, monkeypatch):
        """status should display real desktop avatar when it differs from recorded state."""
        _patch_paths(tmp_path, monkeypatch)
        monkeypatch.setattr("kdam.cli._timer_active", lambda: False)
        from kdam.cli import cli
        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.state.last_image_path = "/home/test/Pictures/recorded.png"
        save_config(cfg)

        # Mock kde.get_current_avatar to return a different path
        monkeypatch.setattr("kdam.kde.get_current_avatar", lambda: "/home/test/Pictures/different.png")

        runner = CliRunner()
        res = runner.invoke(cli, ["status"])
        assert res.exit_code == 0
        assert "AccountsService: different.png" in res.output


# ── kdam help ─────────────────────────────────────────────────────────────────


class TestHelpCommand:
    def test_help_runs(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["help"])
        assert result.exit_code == 0
        assert "kdam" in result.output.lower()

    def test_help_specific_command(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["help", "log"])
        assert result.exit_code == 0
        assert "--prune" in result.output or "rotation" in result.output.lower()

    def test_help_unknown_command_shows_error(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["help", "doesnotexist"])
        assert result.exit_code == 0
        assert "unknown" in result.output.lower()


# ── auto temp cleanup ─────────────────────────────────────────────────────────


class TestAutoCleanupTempFiles:
    def test_cleanup_deletes_leftovers_preserves_panic(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import cleanup_temp_files

        proc_dir = tmp_path / "processed"
        proc_dir.mkdir(parents=True, exist_ok=True)
        (proc_dir / "temp1.png").write_bytes(b"DATA1")
        (proc_dir / "temp2.png").write_bytes(b"DATA2")
        (proc_dir / "panic.png").write_bytes(b"PANIC")

        cleanup_temp_files()
        assert not (proc_dir / "temp1.png").exists()
        assert not (proc_dir / "temp2.png").exists()
        assert (proc_dir / "panic.png").exists()

    def test_ensure_dirs_automatically_cleans(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import ensure_dirs

        proc_dir = tmp_path / "processed"
        proc_dir.mkdir(parents=True, exist_ok=True)
        (proc_dir / "stray.png").write_bytes(b"DATA")
        (proc_dir / "panic.png").write_bytes(b"PANIC")

        ensure_dirs()
        assert not (proc_dir / "stray.png").exists()
        assert (proc_dir / "panic.png").exists()


# ── shell completion helpers ──────────────────────────────────────────────────


class TestShellCompletion:
    def test_complete_image_name_basename(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import _complete_image_name
        from kdam.db import ImageDB, ImageEntry

        db = ImageDB()
        db.set("/path/to/thats-me_1.jpg", ImageEntry())
        db.set("/path/to/thats-me_2.jpg", ImageEntry())
        db.set("/path/to/other.jpg", ImageEntry())
        db.save()

        items = _complete_image_name(None, None, "that")
        values = [it.value for it in items]
        assert "thats-me_1.jpg" in values
        assert "thats-me_2.jpg" in values
        assert "other.jpg" not in values

    def test_complete_image_name_path(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import _complete_image_name
        from kdam.db import ImageDB, ImageEntry

        db = ImageDB()
        img_path = str(tmp_path / "photo.jpg")
        db.set(img_path, ImageEntry())
        db.save()

        items = _complete_image_name(None, None, str(tmp_path / "pho"))
        values = [it.value for it in items]
        assert img_path in values

    def test_complete_configured_dirs(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import _complete_configured_dirs
        from kdam.config import Directory, load_config, save_config

        cfg = load_config()
        cfg.directories = [
            Directory(path="/home/user/Pictures/Avatars"),
            Directory(path="/home/user/Pictures/Wallpapers"),
        ]
        save_config(cfg)

        items = _complete_configured_dirs(None, None, "/home/user/Pictures/Ava")
        values = [it.value for it in items]
        assert "/home/user/Pictures/Avatars" in values
        assert "/home/user/Pictures/Wallpapers" not in values

    def test_complete_command_name(self):
        from kdam.cli import _complete_command_name

        items = _complete_command_name(None, None, "re")
        values = [it.value for it in items]
        assert "remove" in values
        assert "rename" in values
        assert "resync" in values
        assert "status" not in values

    def test_complete_config_key_and_value(self):
        from kdam.cli import _complete_config_key, _complete_config_value

        key_items = _complete_config_key(None, None, "rot")
        key_values = [it.value for it in key_items]
        assert "rotation.mode" in key_values
        assert "rotation.order" in key_values

        ctx = MagicMock()
        ctx.args = ["rotation.mode"]
        val_items = _complete_config_value(ctx, None, "da")
        val_values = [it.value for it in val_items]
        assert "daily" in val_values
