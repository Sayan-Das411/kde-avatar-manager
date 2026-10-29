"""test_option_audit.py — Exhaustive cartesian-product tests for every kdam command × option.

Each test method corresponds to one or more rows in the option audit table.
Tests run via the Click CliRunner (no real D-Bus / systemd calls needed).
All tests are isolated via _patch_paths(tmp_path, monkeypatch).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from helpers import _patch_paths

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_png(path: Path) -> Path:
    """Write a minimal valid 1×1 PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
        b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
        b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return path


def _seeded_db(tmp_path, monkeypatch, *, configured=1, unconfigured=1, disabled=0):
    """Set up a patched DB with the given number of each image type. Returns dict of paths."""
    from kdam.db import ImageDB, ImageEntry

    _patch_paths(tmp_path, monkeypatch)
    imgs = {}
    db = ImageDB()
    for i in range(configured):
        p = _make_png(tmp_path / f"cfg_{i}.png")
        db.set(str(p), ImageEntry(configured=True, enabled=True, weight=1.0))
        imgs[f"cfg_{i}"] = p
    for i in range(unconfigured):
        p = _make_png(tmp_path / f"uncfg_{i}.png")
        db.set(str(p), ImageEntry(configured=False, enabled=False))
        imgs[f"uncfg_{i}"] = p
    for i in range(disabled):
        p = _make_png(tmp_path / f"dis_{i}.png")
        db.set(str(p), ImageEntry(configured=True, enabled=False, weight=1.0))
        imgs[f"dis_{i}"] = p
    db.save()
    return imgs


# ── kdam --version / --help ────────────────────────────────────────────────────


class TestGlobal:
    def test_version(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["--version"])
        assert r.exit_code == 0
        assert any(c.isdigit() for c in r.output)

    def test_help(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["--help"])
        assert r.exit_code == 0
        assert "kdam" in r.output.lower()


# ── kdam add ──────────────────────────────────────────────────────────────────


class TestAdd:
    def test_no_paths(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["add"])
        # Should print usage / error, not traceback
        assert r.exit_code != 0 or "path" in r.output.lower()
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_missing_file(self, tmp_path, monkeypatch):
        """kdam add --no-editor <missing> should give a clean Click error (exit 2), not crash."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["add", "--no-editor", str(tmp_path / "ghost.png")])
        # Click's Path(exists=True) validator rejects missing files with exit 2 — correct behaviour
        assert r.exit_code == 2
        assert "does not exist" in r.output.lower() or "invalid" in r.output.lower()
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_with_no_editor_flag(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        img = _make_png(tmp_path / "x.png")
        r = CliRunner().invoke(cli, ["add", "--no-editor", str(img)])
        assert r.exit_code == 0
        assert "Traceback" not in (r.output + str(r.exception or ""))
        from kdam.db import ImageDB

        db = ImageDB()
        assert str(img) in db.all()

    def test_multiple_paths_no_editor(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        imgs = [_make_png(tmp_path / f"m{i}.png") for i in range(3)]
        r = CliRunner().invoke(cli, ["add", "--no-editor"] + [str(p) for p in imgs])
        assert r.exit_code == 0
        from kdam.db import ImageDB

        db = ImageDB()
        for img in imgs:
            assert str(img) in db.all()


# ── kdam backup create ─────────────────────────────────────────────────────────


class TestBackupCreate:
    def _create(self, tmp_path, monkeypatch, *extra_flags, output=None):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        args = ["backup", "create"] + list(extra_flags)
        if output:
            args.append(str(output))
        return CliRunner().invoke(cli, args)

    def test_default(self, tmp_path, monkeypatch):
        r = self._create(tmp_path, monkeypatch, output=tmp_path / "backup_default.tar.gz")
        assert r.exit_code == 0
        assert (tmp_path / "backup_default.tar.gz").exists()
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_include_images(self, tmp_path, monkeypatch):
        r = self._create(tmp_path, monkeypatch, "--include-images", output=tmp_path / "backup_imgs.tar.gz")
        assert r.exit_code == 0
        assert (tmp_path / "backup_imgs.tar.gz").exists()

    def test_no_images(self, tmp_path, monkeypatch):
        r = self._create(tmp_path, monkeypatch, "--no-images", output=tmp_path / "backup_noimgs.tar.gz")
        assert r.exit_code == 0

    def test_config_only(self, tmp_path, monkeypatch):
        r = self._create(tmp_path, monkeypatch, "--config-only", output=tmp_path / "backup_cfgonly.tar.gz")
        assert r.exit_code == 0
        assert (tmp_path / "backup_cfgonly.tar.gz").exists()

    def test_include_images_and_config_only_conflict(self, tmp_path, monkeypatch):
        # Both set: config-only wins or error — must not crash
        r = self._create(
            tmp_path,
            monkeypatch,
            "--include-images",
            "--config-only",
            output=tmp_path / "backup_conflict.tar.gz",
        )
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_with_output_path(self, tmp_path, monkeypatch):
        out = tmp_path / "my_backup.tar.gz"
        r = self._create(tmp_path, monkeypatch, output=out)
        assert r.exit_code == 0
        assert out.exists()

    def test_include_images_with_output(self, tmp_path, monkeypatch):
        out = tmp_path / "with_imgs.tar.gz"
        r = self._create(tmp_path, monkeypatch, "--include-images", output=out)
        assert r.exit_code == 0
        assert out.exists()

    def test_config_only_with_output(self, tmp_path, monkeypatch):
        out = tmp_path / "cfg_only.tar.gz"
        r = self._create(tmp_path, monkeypatch, "--config-only", output=out)
        assert r.exit_code == 0
        assert out.exists()


# ── kdam backup inspect ────────────────────────────────────────────────────────


class TestBackupInspect:
    def test_valid_archive(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        out = tmp_path / "test.tar.gz"
        runner.invoke(cli, ["backup", "create", str(out)])
        r = runner.invoke(cli, ["backup", "info", str(out)])
        assert r.exit_code == 0
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_invalid_path(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["backup", "info", str(tmp_path / "nonexistent.tar.gz")])
        assert r.exit_code != 0 or "error" in r.output.lower() or "not found" in r.output.lower()
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam backup restore ────────────────────────────────────────────────────────


class TestBackupRestore:
    def _make_archive(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        out = tmp_path / "backup.tar.gz"
        CliRunner().invoke(cli, ["backup", "create", str(out)])
        return out

    def test_restore_basic(self, tmp_path, monkeypatch):
        archive = self._make_archive(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["backup", "restore", str(archive)], input="y\n")
        assert r.exit_code == 0
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_restore_with_force(self, tmp_path, monkeypatch):
        archive = self._make_archive(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["backup", "restore", "--force", str(archive)])
        assert r.exit_code == 0

    def test_restore_with_images_dir(self, tmp_path, monkeypatch):
        archive = self._make_archive(tmp_path, monkeypatch)
        dest = tmp_path / "restored_imgs"
        from kdam.cli import cli

        r = CliRunner().invoke(
            cli, ["backup", "restore", "--images-dir", str(dest), str(archive)], input="y\n"
        )
        assert r.exit_code == 0

    def test_restore_force_with_images_dir(self, tmp_path, monkeypatch):
        archive = self._make_archive(tmp_path, monkeypatch)
        dest = tmp_path / "forced_dest"
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["backup", "restore", "--force", "--images-dir", str(dest), str(archive)])
        assert r.exit_code == 0

    def test_restore_missing_archive(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["backup", "restore", str(tmp_path / "ghost.tar.gz")])
        assert r.exit_code != 0 or "not found" in r.output.lower() or "error" in r.output.lower()
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_restore_does_not_create_avatars_dir_without_images(self, tmp_path, monkeypatch):
        """Regression: restore must NOT create ~/Pictures/Avatars when no images in archive.

        Path.home() is patched by _patch_paths → tmp_path/fake_home, so if any
        code incorrectly creates ~/Pictures/Avatars it will appear at
        tmp_path/fake_home/Pictures/Avatars (not the real home dir).
        """
        archive = self._make_archive(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["backup", "restore", "--force", str(archive)])
        assert r.exit_code == 0, r.output
        assert "Traceback" not in (r.output + str(r.exception or ""))
        # Path.home() is redirected to tmp/fake_home — Avatars must not appear there
        fake_avatars = tmp_path / "fake_home" / "Pictures" / "Avatars"
        assert not fake_avatars.exists(), f"restore_backup created {fake_avatars} for a config-only archive"


# ── kdam completion ────────────────────────────────────────────────────────────


class TestCompletion:
    @pytest.mark.parametrize("shell", [None, "bash", "zsh", "fish"])
    def test_shell_variants(self, tmp_path, monkeypatch, shell):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        args = ["completion"]
        if shell:
            args += ["--shell", shell]
        r = CliRunner().invoke(cli, args)
        assert r.exit_code == 0
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_invalid_shell(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["completion", "--shell", "powershell"])
        assert r.exit_code != 0
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam config ────────────────────────────────────────────────────────────────


class TestConfig:
    def test_list(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["config", "show"])
        assert r.exit_code == 0

    @pytest.mark.parametrize("key", ["interval_hours", "mode", "order"])
    def test_get_valid_keys_via_show(self, tmp_path, monkeypatch, key):
        """config show prints all settings; individual 'get <key>' doesn't exist."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["config", "show"])
        assert r.exit_code == 0
        assert key in r.output

    def test_set_unknown_subcommand(self, tmp_path, monkeypatch):
        """Unknown config subcommand should give usage error, not traceback."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["config", "blorp"])
        assert r.exit_code != 0
        assert "Traceback" not in (r.output + str(r.exception or ""))

    @pytest.mark.parametrize(
        "key,value",
        [
            ("rotation.interval_hours", "6"),
            ("rotation.interval", "6h"),
            ("rotation.interval", "12d 13h 52m"),
            ("rotation.start_hour", "2"),
            ("rotation.mode", "daily"),
            ("rotation.mode", "interval"),
            ("rotation.mode", "times"),
            ("rotation.times", "08:30, 14:00, 21:15"),
            ("rotation.mode", "weekly"),
            ("rotation.weekday", "sun"),
            ("rotation.mode", "monthly"),
            ("rotation.order", "sequential"),
            ("rotation.order", "random"),
            ("rotation.order", "weighted"),
            ("rotation.order", "lru"),
            ("rotation.order", "least-recent"),
            ("rotation.order", "deck"),
            ("rotation.lru_pool_size", "default"),
            ("rotation.lru_pool_size", "15"),
        ],
    )
    def test_set_valid(self, tmp_path, monkeypatch, key, value):
        _patch_paths(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        import kdam.cli_config as cc

        monkeypatch.setattr(cc, "_systemd", MagicMock())
        monkeypatch.setattr(cc, "_timer_active", lambda: False)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["config", "set", key, value])
        assert r.exit_code == 0, f"config set {key} {value} failed: {r.output}"

    def test_set_interval_hours_zero_rejected(self, tmp_path, monkeypatch):
        """rotation.interval_hours 0 must be rejected (min is 1)."""
        _patch_paths(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        import kdam.cli_config as cc

        monkeypatch.setattr(cc, "_systemd", MagicMock())
        monkeypatch.setattr(cc, "_timer_active", lambda: False)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["config", "set", "rotation.interval_hours", "0"])
        assert r.exit_code != 0 or "≥ 1" in r.output or ">= 1" in r.output
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_set_interval_hours_invalid_format(self, tmp_path, monkeypatch):
        """'config set interval_hours' missing dot notation should exit non-zero with hint."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        # Wrong: flat key. Correct is rotation.interval_hours
        r = CliRunner().invoke(cli, ["config", "set", "interval_hours", "abc"])
        assert r.exit_code != 0
        assert "section" in r.output.lower() or "rotation" in r.output.lower()

    def test_set_interval_hours_non_integer(self, tmp_path, monkeypatch):
        """'config set rotation.interval_hours abc' should exit non-zero."""
        _patch_paths(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        import kdam.cli_config as cc

        monkeypatch.setattr(cc, "_systemd", MagicMock())
        monkeypatch.setattr(cc, "_timer_active", lambda: False)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["config", "set", "rotation.interval_hours", "abc"])
        assert r.exit_code != 0 or "integer" in r.output.lower()
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_set_mode_wrong_format(self, tmp_path, monkeypatch):
        """'config set mode lunar' (flat key, wrong section) → non-zero exit with hint."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["config", "set", "mode", "lunar"])
        assert r.exit_code != 0
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_set_mode_invalid_value(self, tmp_path, monkeypatch):
        """'config set rotation.mode lunar' → non-zero, invalid mode name."""
        _patch_paths(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        import kdam.cli_config as cc

        monkeypatch.setattr(cc, "_systemd", MagicMock())
        monkeypatch.setattr(cc, "_timer_active", lambda: False)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["config", "set", "rotation.mode", "lunar"])
        assert r.exit_code != 0 or "must be" in r.output.lower()
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_set_invalid_key(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        import kdam.cli_config as cc

        monkeypatch.setattr(cc, "_systemd", MagicMock())
        monkeypatch.setattr(cc, "_timer_active", lambda: False)
        from kdam.cli import cli

        # Valid section, invalid field
        r = CliRunner().invoke(cli, ["config", "set", "rotation.bogus", "foo"])
        assert r.exit_code != 0
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam dirs ─────────────────────────────────────────────────────────────────


class TestDirs:
    def test_list_empty(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["dirs", "list"])
        assert r.exit_code == 0

    def test_add_valid(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        r = CliRunner().invoke(cli, ["dirs", "add", str(d)])
        assert r.exit_code == 0

    def test_add_recursive(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        r = CliRunner().invoke(cli, ["dirs", "add", "--recursive", str(d)])
        assert r.exit_code == 0

    def test_add_missing_dir(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["dirs", "add", str(tmp_path / "nonexistent")])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_add_duplicate(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        runner = CliRunner()
        runner.invoke(cli, ["dirs", "add", str(d)])
        r = runner.invoke(cli, ["dirs", "add", str(d)])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_remove_configured(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        runner = CliRunner()
        runner.invoke(cli, ["dirs", "add", str(d)])
        r = runner.invoke(cli, ["dirs", "remove", str(d)])
        assert r.exit_code == 0

    def test_remove_not_configured(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["dirs", "remove", str(tmp_path / "never_added")])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_edit_recursive(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        runner = CliRunner()
        runner.invoke(cli, ["dirs", "add", str(d)])
        r = runner.invoke(cli, ["dirs", "edit", "--recursive", str(d)])
        assert r.exit_code == 0

    def test_edit_no_recursive(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        runner = CliRunner()
        runner.invoke(cli, ["dirs", "add", "--recursive", str(d)])
        r = runner.invoke(cli, ["dirs", "edit", "--no-recursive", str(d)])
        assert r.exit_code == 0

    def test_edit_no_flag(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        runner = CliRunner()
        runner.invoke(cli, ["dirs", "add", str(d)])
        r = runner.invoke(cli, ["dirs", "edit", str(d)])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_edit_unknown_dir(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["dirs", "edit", "--recursive", str(tmp_path / "unknown")])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_suspend_configured(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        runner = CliRunner()
        runner.invoke(cli, ["dirs", "add", str(d)])
        r = runner.invoke(cli, ["dirs", "suspend", str(d)])
        assert r.exit_code == 0

    def test_suspend_not_configured(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["dirs", "suspend", str(tmp_path / "nope")])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_suspend_already_suspended(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        runner = CliRunner()
        runner.invoke(cli, ["dirs", "add", str(d)])
        runner.invoke(cli, ["dirs", "suspend", str(d)])
        r = runner.invoke(cli, ["dirs", "suspend", str(d)])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_resume_suspended(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        runner = CliRunner()
        runner.invoke(cli, ["dirs", "add", str(d)])
        runner.invoke(cli, ["dirs", "suspend", str(d)])
        r = runner.invoke(cli, ["dirs", "resume", str(d)])
        assert r.exit_code == 0

    def test_resume_not_suspended(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "mydir"
        d.mkdir()
        runner = CliRunner()
        runner.invoke(cli, ["dirs", "add", str(d)])
        r = runner.invoke(cli, ["dirs", "resume", str(d)])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_resume_not_configured(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["dirs", "resume", str(tmp_path / "nope")])
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam disable / enable (timer) ─────────────────────────────────────────────


class TestEnableDisable:
    def test_disable(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        import kdam.cli as cli_mod
        from kdam.cli import cli

        monkeypatch.setattr(cli_mod, "_systemd", MagicMock())
        r = CliRunner().invoke(cli, ["disable"])
        assert r.exit_code == 0

    def test_enable(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        import kdam.cli as cli_mod
        from kdam.cli import cli

        monkeypatch.setattr(cli_mod, "_systemd", MagicMock())
        r = CliRunner().invoke(cli, ["enable"])
        assert r.exit_code == 0

    def test_enable_twice(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        import kdam.cli as cli_mod
        from kdam.cli import cli

        monkeypatch.setattr(cli_mod, "_systemd", MagicMock())
        runner = CliRunner()
        runner.invoke(cli, ["enable"])
        r = runner.invoke(cli, ["enable"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_disable_twice(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from unittest.mock import MagicMock

        import kdam.cli as cli_mod
        from kdam.cli import cli

        monkeypatch.setattr(cli_mod, "_systemd", MagicMock())
        runner = CliRunner()
        runner.invoke(cli, ["disable"])
        r = runner.invoke(cli, ["disable"])
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam disable-image / enable-image ─────────────────────────────────────────


class TestEnableDisableImage:
    def test_disable_enabled_image(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["disable-image", str(imgs["cfg_0"])])
        assert r.exit_code == 0

    def test_disable_already_disabled(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=0, unconfigured=0, disabled=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["disable-image", str(imgs["dis_0"])])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_disable_not_in_registry(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["disable-image", str(tmp_path / "ghost.png")])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_enable_disabled_image(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=0, unconfigured=0, disabled=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["enable-image", str(imgs["dis_0"])])
        assert r.exit_code == 0

    def test_enable_already_enabled(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["enable-image", str(imgs["cfg_0"])])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_enable_not_in_registry(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["enable-image", str(tmp_path / "ghost.png")])
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam freeze / unfreeze ────────────────────────────────────────────────────


class TestFreezeUnfreeze:
    def _mock_kde(self, monkeypatch):
        from unittest.mock import MagicMock

        import kdam.cli as cli_mod

        monkeypatch.setattr(cli_mod, "kde", MagicMock())

    def test_freeze_no_args(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["freeze"])
        assert r.exit_code == 0

    def test_freeze_with_duration(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        for dur in ["2h", "1d", "1w"]:
            r = CliRunner().invoke(cli, ["freeze", dur])
            assert r.exit_code == 0, f"freeze {dur} failed: {r.output}"

    def test_freeze_invalid_duration(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["freeze", "invalid"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_freeze_already_frozen(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        runner.invoke(cli, ["freeze"])
        r = runner.invoke(cli, ["freeze"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_freeze_with_image(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["freeze", "--image", imgs["cfg_0"].name])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_freeze_duration_and_image(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["freeze", "2h", "--image", imgs["cfg_0"].name])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_unfreeze_when_frozen(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        runner.invoke(cli, ["freeze"])
        r = runner.invoke(cli, ["unfreeze"])
        assert r.exit_code == 0

    def test_unfreeze_with_rotate(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        runner = CliRunner()
        runner.invoke(cli, ["freeze"])
        r = runner.invoke(cli, ["unfreeze", "--rotate"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_unfreeze_when_not_frozen(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["unfreeze"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_unfreeze_rotate_when_not_frozen(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["unfreeze", "--rotate"])
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam help ─────────────────────────────────────────────────────────────────


class TestHelp:
    @pytest.mark.parametrize("arg", [None, "next", "remove", "backup", "invalid_cmd"])
    def test_help_variants(self, tmp_path, monkeypatch, arg):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        args = ["help"] + ([arg] if arg else [])
        r = CliRunner().invoke(cli, args)
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam list ─────────────────────────────────────────────────────────────────


class TestList:
    def test_list_default(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=2, unconfigured=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["list"])
        assert r.exit_code == 0

    def test_list_paths(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["list", "--paths"])
        assert r.exit_code == 0

    def test_list_empty(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["list"])
        assert r.exit_code == 0


# ── kdam log ──────────────────────────────────────────────────────────────────


class TestLog:
    def _with_log(self, tmp_path, monkeypatch, n_entries=5):
        _patch_paths(tmp_path, monkeypatch)
        from kdam._cli_shared import _write_log_entry

        for i in range(n_entries):
            _write_log_entry(f"/fake/path/img_{i}.png")

    def test_default(self, tmp_path, monkeypatch):
        self._with_log(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["log"])
        assert r.exit_code == 0

    def test_n_flag(self, tmp_path, monkeypatch):
        self._with_log(tmp_path, monkeypatch, 10)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["log", "-n", "3"])
        assert r.exit_code == 0

    def test_all_flag(self, tmp_path, monkeypatch):
        self._with_log(tmp_path, monkeypatch, 25)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["log", "--all"])
        assert r.exit_code == 0

    def test_desc_flag(self, tmp_path, monkeypatch):
        self._with_log(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["log", "--desc"])
        assert r.exit_code == 0

    def test_all_and_desc(self, tmp_path, monkeypatch):
        self._with_log(tmp_path, monkeypatch, 25)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["log", "--all", "--desc"])
        assert r.exit_code == 0

    def test_prune(self, tmp_path, monkeypatch):
        self._with_log(tmp_path, monkeypatch, 10)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["log", "--prune", "5"])
        assert r.exit_code == 0

    def test_n_and_desc(self, tmp_path, monkeypatch):
        self._with_log(tmp_path, monkeypatch, 10)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["log", "-n", "3", "--desc"])
        assert r.exit_code == 0

    def test_n_zero(self, tmp_path, monkeypatch):
        self._with_log(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["log", "-n", "0"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_prune_zero(self, tmp_path, monkeypatch):
        self._with_log(tmp_path, monkeypatch, 5)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["log", "--prune", "0"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_empty_log(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["log"])
        assert r.exit_code == 0
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam next ─────────────────────────────────────────────────────────────────


class TestNext:
    def _mock_rotate(self, monkeypatch, return_val=True):
        from unittest.mock import MagicMock

        import kdam.cli as cli_mod

        mock_rot = MagicMock(return_value=return_val)
        monkeypatch.setattr(cli_mod.rotator, "rotate", mock_rot)
        monkeypatch.setattr(cli_mod, "kde", MagicMock())
        return mock_rot

    def test_default(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        self._mock_rotate(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["next"])
        assert r.exit_code == 0

    def test_quiet(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        self._mock_rotate(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["next", "--quiet"])
        assert r.exit_code == 0
        assert r.output.strip() == ""

    def test_count(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        mock = self._mock_rotate(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["next", "--count", "3"])
        assert r.exit_code == 0
        assert mock.call_count == 3

    def test_dry_run(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["next", "--dry-run"])
        assert r.exit_code == 0
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_quiet_and_dry_run(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["next", "--quiet", "--dry-run"])
        assert r.exit_code == 0

    def test_count_and_dry_run(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["next", "--count", "3", "--dry-run"])
        assert r.exit_code == 0

    def test_count_zero(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=0)
        self._mock_rotate(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["next", "--count", "0"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_empty_pool(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["next"])
        assert r.exit_code == 0
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam remove ───────────────────────────────────────────────────────────────


class TestRemove:
    def test_single_path(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["remove", str(imgs["cfg_0"])], input="y\n")
        assert r.exit_code == 0

    def test_single_path_delete_file(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1)
        from kdam.cli import cli

        img = imgs["cfg_0"]
        r = CliRunner().invoke(cli, ["remove", "--delete-file", str(img)])
        assert r.exit_code == 0
        assert not img.exists()

    def test_multiple_paths(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=2)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["remove", str(imgs["cfg_0"]), str(imgs["cfg_1"])])
        assert r.exit_code == 0

    def test_multiple_paths_delete_file(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=2)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["remove", "--delete-file", str(imgs["cfg_0"]), str(imgs["cfg_1"])])
        assert r.exit_code == 0
        assert not imgs["cfg_0"].exists()
        assert not imgs["cfg_1"].exists()

    def test_stale(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.db import ImageDB, ImageEntry

        db = ImageDB()
        db.set("/nonexistent/ghost.png", ImageEntry(configured=False, enabled=False))
        db.save()
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["remove", "--stale"])
        assert r.exit_code == 0
        from kdam.db import ImageDB as DB2

        assert "/nonexistent/ghost.png" not in DB2().all()

    def test_stale_with_delete_file(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.db import ImageDB, ImageEntry

        db = ImageDB()
        db.set("/nonexistent/ghost.png", ImageEntry(configured=False, enabled=False))
        db.save()
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["remove", "--stale", "--delete-file"])
        assert r.exit_code == 0
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_all_unconfigured_no_delete(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=2)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["remove", "--all-unconfigured"], input="y\n")
        assert r.exit_code == 0
        assert imgs["uncfg_0"].exists()  # file stays on disk

    def test_all_unconfigured_with_delete_file(self, tmp_path, monkeypatch):
        """Regression test for the bug reported by user."""
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=2)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["remove", "--all-unconfigured", "--delete-file"], input="y\n")
        assert r.exit_code == 0
        assert not imgs["uncfg_0"].exists(), "File must be deleted with --delete-file"
        assert not imgs["uncfg_1"].exists()
        assert imgs["cfg_0"].exists(), "Configured file must survive"

    def test_no_paths_no_flags(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["remove"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_not_in_registry(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["remove", str(tmp_path / "ghost.png")])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_stale_delete_unconfigured_combined(self, tmp_path, monkeypatch):
        _seeded_db(tmp_path, monkeypatch, configured=1, unconfigured=1)
        from kdam.db import ImageDB, ImageEntry

        db = ImageDB()
        db.set("/nonexistent/ghost.png", ImageEntry(configured=False, enabled=False))
        db.save()
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["remove", "--stale", "--delete-file", "--all-unconfigured"], input="y\n")
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam rename ───────────────────────────────────────────────────────────────


class TestRename:
    def test_rename_registered(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["rename", str(imgs["cfg_0"]), "renamed.png"])
        assert r.exit_code == 0 or "Traceback" not in (r.output + str(r.exception or ""))

    def test_rename_not_registered(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["rename", str(tmp_path / "ghost.png"), "new.png"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_rename_no_extension(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["rename", str(imgs["cfg_0"]), "no_ext"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_rename_to_existing_name(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=2)
        from kdam.cli import cli

        existing_name = imgs["cfg_1"].name
        r = CliRunner().invoke(cli, ["rename", str(imgs["cfg_0"]), existing_name])
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam resync ───────────────────────────────────────────────────────────────


class TestResync:
    def test_resync(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["resync"])
        assert r.exit_code == 0
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam scan ─────────────────────────────────────────────────────────────────


class TestScan:
    def test_scan_no_dirs(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["scan"])
        assert r.exit_code == 0

    def test_scan_with_dir(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        d = tmp_path / "imgs"
        d.mkdir()
        _make_png(d / "a.png")
        runner = CliRunner()
        runner.invoke(cli, ["dirs", "add", str(d)])
        r = runner.invoke(cli, ["scan"])
        assert r.exit_code == 0


# ── kdam set ──────────────────────────────────────────────────────────────────


class TestSet:
    def _mock_kde(self, monkeypatch):
        from unittest.mock import MagicMock

        import kdam.cli as cli_mod

        monkeypatch.setattr(cli_mod, "kde", MagicMock())

    def test_set_configured_image(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["set", str(imgs["cfg_0"])])
        assert r.exit_code == 0

    def test_set_unconfigured_image(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, unconfigured=1)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["set", str(imgs["uncfg_0"])])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_set_not_in_registry(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_kde(monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["set", str(tmp_path / "ghost.png")])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_set_missing_file(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_kde(monkeypatch)
        from kdam.db import ImageDB, ImageEntry

        db = ImageDB()
        db.set("/ghost/img.png", ImageEntry(configured=True, enabled=True, weight=1.0))
        db.save()
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["set", "/ghost/img.png"])
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam status ───────────────────────────────────────────────────────────────


class TestStatus:
    def test_status(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["status"])
        assert r.exit_code == 0


# ── kdam toggle ───────────────────────────────────────────────────────────────


class TestToggle:
    def _mock_systemd(self, monkeypatch):
        from unittest.mock import MagicMock

        import kdam.cli as cli_mod

        monkeypatch.setattr(cli_mod, "_systemd", MagicMock())

    def test_toggle_on_to_off(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_systemd(monkeypatch)
        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.state.rotation_enabled = True
        save_config(cfg)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["toggle"])
        assert r.exit_code == 0

    def test_toggle_off_to_on(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_systemd(monkeypatch)
        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.state.rotation_enabled = False
        save_config(cfg)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["toggle"])
        assert r.exit_code == 0

    def test_toggle_when_frozen(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        self._mock_systemd(monkeypatch)
        from datetime import datetime, timedelta

        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.state.frozen_until = (datetime.now() + timedelta(hours=1)).isoformat()
        save_config(cfg)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["toggle"])
        assert "Traceback" not in (r.output + str(r.exception or ""))


# ── kdam unconfigure ──────────────────────────────────────────────────────────


class TestUnconfigure:
    def test_unconfigure_configured(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["unconfigure", str(imgs["cfg_0"])])
        assert r.exit_code == 0

    def test_unconfigure_already_unconfigured(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, unconfigured=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["unconfigure", str(imgs["uncfg_0"])])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_unconfigure_not_in_registry(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["unconfigure", str(tmp_path / "ghost.png")])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_unconfigure_multiple(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=2)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["unconfigure", str(imgs["cfg_0"]), str(imgs["cfg_1"])])
        assert r.exit_code == 0


# ── kdam weight ───────────────────────────────────────────────────────────────


class TestWeight:
    def test_set_weight_valid(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1)
        from kdam.cli import cli

        for val in ["1.0", "5.0", "0.5"]:
            r = CliRunner().invoke(cli, ["weight", str(imgs["cfg_0"]), val])
            assert r.exit_code == 0, f"weight {val} failed: {r.output}"

    def test_weight_zero(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["weight", str(imgs["cfg_0"]), "0.0"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_weight_negative(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["weight", str(imgs["cfg_0"]), "-1"])
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_weight_invalid_string(self, tmp_path, monkeypatch):
        imgs = _seeded_db(tmp_path, monkeypatch, configured=1)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["weight", str(imgs["cfg_0"]), "abc"])
        assert r.exit_code != 0 or "invalid" in r.output.lower()
        assert "Traceback" not in (r.output + str(r.exception or ""))

    def test_weight_not_in_registry(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.cli import cli

        r = CliRunner().invoke(cli, ["weight", str(tmp_path / "ghost.png"), "1.0"])
        assert "Traceback" not in (r.output + str(r.exception or ""))
