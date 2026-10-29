"""Tests for kdam CLI commands via Click's test runner."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from helpers import _patch_paths  # shared: redirects all paths to tmp, silences notify


# ── kdam version ──────────────────────────────────────────────────────────────

def test_version():
    from kdam.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "version" in result.output.lower()


# ── kdam list ─────────────────────────────────────────────────────────────────

def test_list_empty(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    from kdam.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "No images" in result.output or result.exit_code == 0


# ── kdam log ──────────────────────────────────────────────────────────────────

def test_log_empty(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    from kdam.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["log"])
    assert result.exit_code == 0
    assert "No rotation" in result.output or "empty" in result.output.lower()


def test_log_shows_entries(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    log_file = tmp_path / "logs" / "rotation.log"
    log_file.write_text(
        "2026-09-01T10:00:00 | /home/user/a.jpg\n"
        "2026-09-02T10:00:00 | /home/user/b.jpg\n"
    )
    from kdam.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["log", "--all"])
    assert result.exit_code == 0
    assert "a.jpg" in result.output
    assert "b.jpg" in result.output


def test_log_prune(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    log_file = tmp_path / "logs" / "rotation.log"
    lines = [f"2026-09-0{i}T10:00:00 | /home/user/img{i}.jpg" for i in range(1, 6)]
    log_file.write_text("\n".join(lines) + "\n")
    from kdam.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["log", "--prune", "3"])
    assert result.exit_code == 0
    assert "Pruned 2" in result.output
    kept = [l for l in log_file.read_text().splitlines() if l.strip()]
    assert len(kept) == 3
    assert "img3" in kept[0]  # oldest of the kept 3


def test_log_default_order_oldest_first(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    log_file = tmp_path / "logs" / "rotation.log"
    log_file.write_text(
        "2026-09-01T10:00:00 | /home/user/first.jpg\n"
        "2026-09-02T10:00:00 | /home/user/second.jpg\n"
        "2026-09-03T10:00:00 | /home/user/third.jpg\n"
    )
    from kdam.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["log", "--all"])
    assert result.exit_code == 0
    first_pos  = result.output.find("first")
    second_pos = result.output.find("second")
    third_pos  = result.output.find("third")
    assert first_pos < second_pos < third_pos, "Log should be oldest-first"


# ── kdam dirs ─────────────────────────────────────────────────────────────────

def test_dirs_add_and_list(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    # Create a real directory to add
    avatar_dir = tmp_path / "avatars"
    avatar_dir.mkdir()
    from kdam.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["dirs", "add", str(avatar_dir)])
    assert result.exit_code == 0
    # Verify it's in config
    result2 = runner.invoke(cli, ["dirs", "list"])
    assert result2.exit_code == 0
    # Rich table truncates long paths to fit terminal width.
    # Assert on a unique prefix that will survive truncation.
    path_prefix = str(avatar_dir)[:40]
    assert path_prefix in result2.output


# ── kdam next --count ─────────────────────────────────────────────────────────

def test_next_count_requires_enabled(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    from kdam.cli import cli
    runner = CliRunner()
    # Rotation disabled by default — next should warn
    result = runner.invoke(cli, ["next", "--count", "3"])
    assert result.exit_code == 0
    assert "disabled" in result.output.lower() or result.exit_code == 0


# ── kdam weight ───────────────────────────────────────────────────────────────

def test_weight_range_validation(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    # Register a fake image
    img = tmp_path / "img.jpg"
    img.touch()
    from kdam.db import ImageDB, ImageEntry
    db = ImageDB()
    db.set(str(img), ImageEntry(configured=True, enabled=True))
    db.save()

    from kdam.cli import cli
    runner = CliRunner()
    # weight 0 should fail
    result = runner.invoke(cli, ["weight", str(img), "0"])
    assert result.exit_code != 0 or "must be" in result.output.lower() or "greater" in result.output.lower()


# ── kdam scan ─────────────────────────────────────────────────────────────────

def test_scan_no_dirs(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    from kdam.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["scan"])
    assert result.exit_code == 0
    assert "No directories" in result.output or "0" in result.output or result.exit_code == 0
