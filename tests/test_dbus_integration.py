"""test_dbus_integration.py — integration tests for the D-Bus / KDE avatar path.

These tests exercise the full rotate() → kde.set_avatar() call chain with a
mocked D-Bus interface, verifying that:
  - A successful D-Bus call results in True and correct state updates
  - A DBusException is converted to RuntimeError and rotate() returns False
  - apply_specific() follows the same contract
  - set_avatar errors are surfaced (not swallowed) by rotate()

No real D-Bus connection is made — dbus itself is mocked at the module level.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from datetime import datetime

import pytest
from PIL import Image

from helpers import _patch_paths


def _make_png(path: Path, size=(64, 64)) -> Path:
    Image.new("RGB", size, color=(120, 80, 200)).save(str(path), "PNG")
    return path


def _make_config(tmp: Path):
    from kdam.config import Config, RotationConfig, PanicConfig, State
    return Config(
        rotation=RotationConfig(mode="daily", order="random", interval_hours=24),
        panic=PanicConfig(),
        state=State(enabled=True),
        directories=[],
    )


def _make_full_db(img_path: str):
    """Return a real ImageDB with one configured, enabled, cropped entry."""
    from kdam.db import ImageDB, ImageEntry, CropInfo
    db = ImageDB()
    db.set(img_path, ImageEntry(
        configured=True,
        enabled=True,
        weight=1.0,
        crop=CropInfo(x=0, y=0, size=64),
    ))
    return db


class TestSetAvatar:
    """Unit tests for kde.set_avatar — verify D-Bus call and error wrapping."""

    def test_set_avatar_calls_dbus(self, tmp_path):
        """set_avatar must call SetIconFile with the resolved absolute path."""
        import kdam.kde as kde_mod

        mock_iface = MagicMock()
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            icon = tmp_path / "avatar.png"
            icon.write_bytes(b"fake")
            kde_mod.set_avatar(icon)

        mock_iface.SetIconFile.assert_called_once_with(str(icon.resolve()))

    def test_set_avatar_wraps_dbus_exception(self, tmp_path):
        """DBusException from SetIconFile must become RuntimeError, not leak raw dbus type."""
        import dbus
        import kdam.kde as kde_mod

        mock_iface = MagicMock()
        mock_iface.SetIconFile.side_effect = dbus.exceptions.DBusException("no accounts service")

        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            with pytest.raises(RuntimeError, match="D-Bus SetIconFile failed"):
                kde_mod.set_avatar(tmp_path / "avatar.png")

    def test_set_avatar_receives_absolute_path(self, tmp_path):
        """Relative-ish paths must be resolved to absolute before calling D-Bus."""
        import kdam.kde as kde_mod

        mock_iface = MagicMock()
        icon = tmp_path / "sub" / "avatar.png"
        icon.parent.mkdir()
        icon.write_bytes(b"fake")

        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            kde_mod.set_avatar(icon)

        passed = mock_iface.SetIconFile.call_args[0][0]
        assert os.path.isabs(passed), f"Expected absolute path, got: {passed}"


class TestRotateDBusIntegration:
    """Full rotate() path with mocked D-Bus — exercises process_image + set_avatar together."""

    def test_rotate_true_on_dbus_success(self, tmp_path, monkeypatch):
        """rotate() returns True and updates state when D-Bus call succeeds."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import rotator, kde as kde_mod

        img_path = str(_make_png(tmp_path / "photo.png"))
        config = _make_config(tmp_path)
        db = _make_full_db(img_path)

        mock_iface = MagicMock()
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            result = rotator.rotate(config, db, quiet=True)

        assert result is True
        mock_iface.SetIconFile.assert_called_once()
        assert config.state.last_image_path == img_path

    def test_rotate_false_on_dbus_exception(self, tmp_path, monkeypatch):
        """rotate() returns False and sends notification when D-Bus raises."""
        _patch_paths(tmp_path, monkeypatch)
        import dbus
        from kdam import rotator, kde as kde_mod
        import kdam.notify as notify_mod

        img_path = str(_make_png(tmp_path / "photo.png"))
        config = _make_config(tmp_path)
        db = _make_full_db(img_path)

        mock_iface = MagicMock()
        mock_iface.SetIconFile.side_effect = dbus.exceptions.DBusException("session bus gone")

        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            result = rotator.rotate(config, db, quiet=False)

        assert result is False
        notify_mod.send.assert_called()
        # State must NOT have advanced on failure
        assert config.state.last_image_path != img_path or config.state.last_image_path == ""

    def test_rotate_logs_path_on_success(self, tmp_path, monkeypatch):
        """On a successful rotation that changes the avatar, the path is written to the log."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import rotator, kde as kde_mod
        import kdam.config as cfg_mod

        img_path = str(_make_png(tmp_path / "photo.png"))
        config = _make_config(tmp_path)
        config.state.last_image_path = ""  # force image_changed = True
        db = _make_full_db(img_path)

        mock_iface = MagicMock()
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            result = rotator.rotate(config, db, quiet=True)

        assert result is True
        log_file = tmp_path / "logs" / "rotation.log"
        assert log_file.exists(), "Log file should have been created after successful rotation"
        log_content = log_file.read_text()
        assert img_path in log_content, "Image path should appear in log"

    def test_rotate_no_log_when_same_image(self, tmp_path, monkeypatch):
        """If the selected image is the same as last time, no log entry is written."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import rotator, kde as kde_mod

        img_path = str(_make_png(tmp_path / "photo.png"))
        config = _make_config(tmp_path)
        config.state.last_image_path = img_path  # same — should NOT log
        db = _make_full_db(img_path)

        mock_iface = MagicMock()
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            result = rotator.rotate(config, db, quiet=True)

        assert result is True
        log_file = tmp_path / "logs" / "rotation.log"
        assert not log_file.exists() or img_path not in log_file.read_text()

    def test_rotate_cleans_up_temp_file_on_success(self, tmp_path, monkeypatch):
        """The processed temp PNG must be deleted after a successful rotation."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import rotator, kde as kde_mod

        img_path = str(_make_png(tmp_path / "photo.png"))
        config = _make_config(tmp_path)
        db = _make_full_db(img_path)

        captured_processed: list[Path] = []
        original_process = rotator.process_image

        def capturing_process(src, crop):
            p = original_process(src, crop)
            captured_processed.append(p)
            return p

        mock_iface = MagicMock()
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            with patch.object(rotator, "process_image", side_effect=capturing_process):
                rotator.rotate(config, db, quiet=True)

        for p in captured_processed:
            assert not p.exists(), f"Temp file not cleaned up: {p}"

    def test_rotate_cleans_up_temp_file_on_dbus_failure(self, tmp_path, monkeypatch):
        """The processed temp PNG must be deleted even when D-Bus fails."""
        _patch_paths(tmp_path, monkeypatch)
        import dbus
        from kdam import rotator, kde as kde_mod

        img_path = str(_make_png(tmp_path / "photo.png"))
        config = _make_config(tmp_path)
        db = _make_full_db(img_path)

        captured_processed: list[Path] = []
        original_process = rotator.process_image

        def capturing_process(src, crop):
            p = original_process(src, crop)
            captured_processed.append(p)
            return p

        mock_iface = MagicMock()
        mock_iface.SetIconFile.side_effect = dbus.exceptions.DBusException("bus gone")

        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            with patch.object(rotator, "process_image", side_effect=capturing_process):
                result = rotator.rotate(config, db, quiet=True)

        assert result is False
        for p in captured_processed:
            assert not p.exists(), f"Temp file not cleaned up after D-Bus failure: {p}"


class TestApplySpecificDBusIntegration:
    """apply_specific() — same contract as rotate() for the D-Bus path."""

    def test_apply_specific_returns_true_on_success(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam import rotator, kde as kde_mod

        img_path = str(_make_png(tmp_path / "photo.png"))
        db = _make_full_db(img_path)
        config = _make_config(tmp_path)

        mock_iface = MagicMock()
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            result = rotator.apply_specific(img_path, db, config)

        assert result is True
        mock_iface.SetIconFile.assert_called_once()

    def test_apply_specific_returns_false_on_dbus_failure(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        import dbus
        from kdam import rotator, kde as kde_mod

        img_path = str(_make_png(tmp_path / "photo.png"))
        db = _make_full_db(img_path)

        mock_iface = MagicMock()
        mock_iface.SetIconFile.side_effect = dbus.exceptions.DBusException("gone")

        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            result = rotator.apply_specific(img_path, db)

        assert result is False

    def test_apply_specific_updates_config_state(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam import rotator, kde as kde_mod

        img_path = str(_make_png(tmp_path / "photo.png"))
        db = _make_full_db(img_path)
        config = _make_config(tmp_path)

        mock_iface = MagicMock()
        with patch.object(kde_mod, "_user_iface", return_value=mock_iface):
            rotator.apply_specific(img_path, db, config)

        assert config.state.last_image_path == img_path
        assert config.state.last_rotation is not None
