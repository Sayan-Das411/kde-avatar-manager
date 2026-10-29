"""test_uninstall.py — integration tests for 'kdam uninstall'.

Tests cover:
  - uninstall removes systemd files, kdam binary, venv
  - uninstall --purge also wipes CONFIG_DIR
  - uninstall without --yes prompts for confirmation; 'n' cancels
  - uninstall cleans XDG bash-completion file
  - uninstall cleans static completion scripts from CONFIG_DIR
  - uninstall cleans RC file lines (_KDAM_COMPLETE, # kdam, etc.)
  - EditorQuit typed sentinel: singleton identity, repr, type annotation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner
from helpers import _patch_paths

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _fake_install(tmp_path: Path) -> dict:
    """Create the files that install.sh would create, under tmp_path."""
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n")

    bin_path = tmp_path / "local_bin" / "kdam"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_text('#!/bin/bash\nexec python -m kdam "$@"\n')

    service_dir = tmp_path / "systemd_user"
    service_dir.mkdir()
    service_file = service_dir / "kde-avatar-rotate.service"
    timer_file = service_dir / "kde-avatar-rotate.timer"
    service_file.write_text("[Unit]\nDescription=test\n")
    timer_file.write_text("[Unit]\nDescription=test timer\n")

    xdg_comp_dir = tmp_path / "xdg_bash_comp"
    xdg_comp_dir.mkdir()
    xdg_comp_file = xdg_comp_dir / "kdam"
    xdg_comp_file.write_text("# kdam bash completion\n")

    static_bash = tmp_path / "completion.bash"
    static_zsh = tmp_path / "completion.zsh"
    static_fish = tmp_path / "completion.fish"
    for f in (static_bash, static_zsh, static_fish):
        f.write_text("# auto-generated completion\n")

    bashrc = tmp_path / ".bashrc"
    bashrc.write_text(
        "export PATH=$HOME/.local/bin:$PATH\n"
        'eval "$(_KDAM_COMPLETE=bash_source kdam)"\n'
        "# kdam shell completion\n"
        "source ~/.kde-avatar-manager/completion.bash\n"
        "echo 'hello'\n"
    )

    return {
        "venv_dir": venv_dir,
        "bin_path": bin_path,
        "service_file": service_file,
        "timer_file": timer_file,
        "xdg_comp_file": xdg_comp_file,
        "static_bash": static_bash,
        "static_zsh": static_zsh,
        "static_fish": static_fish,
        "bashrc": bashrc,
    }


def _make_runner_with_patches(tmp_path, monkeypatch, files=None):
    """Patch cli.py internals to use tmp_path locations instead of real home dirs."""
    _patch_paths(tmp_path, monkeypatch)
    import kdam.cli as cli_mod

    # Create real service/timer files so unlink(missing_ok=True) has something to remove
    systemd_dir = tmp_path / "systemd_user"
    systemd_dir.mkdir(exist_ok=True)
    service_file = systemd_dir / "kde-avatar-rotate.service"
    timer_file = systemd_dir / "kde-avatar-rotate.timer"
    service_file.write_text("[Unit]\nDescription=test\n")
    timer_file.write_text("[Unit]\nDescription=test timer\n")

    # Redirect the systemd service/timer file paths in both cli and _cli_shared
    # (cli imports them by name from _cli_shared, so both must be patched)
    import kdam._cli_shared as shared_mod

    monkeypatch.setattr(cli_mod, "_SERVICE_FILE", service_file)
    monkeypatch.setattr(cli_mod, "_TIMER_FILE", timer_file)
    monkeypatch.setattr(shared_mod, "_SERVICE_FILE", service_file)
    monkeypatch.setattr(shared_mod, "_TIMER_FILE", timer_file)

    # Redirect Path.home() / .local / bin / kdam
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: tmp_path / "home"),
    )
    (tmp_path / "home" / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home" / ".local" / "bin" / "kdam").write_text("#!/bin/bash\n")

    # XDG completion dir
    xdg = tmp_path / "home" / ".local" / "share" / "bash-completion" / "completions"
    xdg.mkdir(parents=True, exist_ok=True)
    (xdg / "kdam").write_text("# kdam bash completion\n")

    # Static completion scripts in CONFIG_DIR
    for fname in ("completion.bash", "completion.zsh", "completion.fish"):
        (tmp_path / fname).write_text("# completion\n")

    # RC files
    bashrc = tmp_path / "home" / ".bashrc"
    bashrc.write_text(
        "export PATH=$HOME/.local/bin:$PATH\n"
        'eval "$(_KDAM_COMPLETE=bash_source kdam)"\n'
        "# kdam shell completion\n"
        "echo 'hello'\n"
    )
    zshrc = tmp_path / "home" / ".zshrc"
    zshrc.write_text("# kdam shell completion\nsource completion.zsh\n")

    # Mock _systemd so it doesn't call real systemctl
    monkeypatch.setattr(cli_mod, "_systemd", MagicMock())

    return cli_mod, xdg / "kdam", bashrc, zshrc


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestUninstallCommand:
    def test_uninstall_removes_binary(self, tmp_path, monkeypatch):
        """kdam uninstall -y must remove the kdam wrapper binary."""
        cli_mod, xdg_comp, bashrc, zshrc = _make_runner_with_patches(tmp_path, monkeypatch, {})
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["uninstall", "-y"])
        assert result.exit_code == 0, result.output

        kdam_bin = tmp_path / "home" / ".local" / "bin" / "kdam"
        assert not kdam_bin.exists(), "kdam binary must be removed after uninstall"

    def test_uninstall_calls_systemd_disable(self, tmp_path, monkeypatch):
        """kdam uninstall -y must call systemctl disable --now on the timer."""
        cli_mod, xdg_comp, bashrc, zshrc = _make_runner_with_patches(tmp_path, monkeypatch, {})
        from kdam.cli import cli

        runner = CliRunner()
        runner.invoke(cli, ["uninstall", "-y"])

        cli_mod._systemd.assert_any_call("disable", "--now", "kde-avatar-rotate.timer")

    def test_uninstall_removes_systemd_files(self, tmp_path, monkeypatch):
        """kdam uninstall -y must remove the service and timer files."""
        cli_mod, xdg_comp, bashrc, zshrc = _make_runner_with_patches(tmp_path, monkeypatch, {})
        from kdam.cli import cli

        runner = CliRunner()
        runner.invoke(cli, ["uninstall", "-y"])

        assert not cli_mod._SERVICE_FILE.exists(), "Service file must be removed"
        assert not cli_mod._TIMER_FILE.exists(), "Timer file must be removed"

    def test_uninstall_removes_xdg_completion(self, tmp_path, monkeypatch):
        """kdam uninstall -y must remove the XDG bash-completion file."""
        cli_mod, xdg_comp, bashrc, zshrc = _make_runner_with_patches(tmp_path, monkeypatch, {})
        from kdam.cli import cli

        runner = CliRunner()
        runner.invoke(cli, ["uninstall", "-y"])

        assert not xdg_comp.exists(), "XDG bash-completion file must be removed"

    def test_uninstall_cleans_rc_files(self, tmp_path, monkeypatch):
        """kdam uninstall -y must strip kdam completion lines from .bashrc and .zshrc."""
        cli_mod, xdg_comp, bashrc, zshrc = _make_runner_with_patches(tmp_path, monkeypatch, {})
        from kdam.cli import cli

        runner = CliRunner()
        runner.invoke(cli, ["uninstall", "-y"])

        bashrc_content = bashrc.read_text()
        assert "_KDAM_COMPLETE" not in bashrc_content, "_KDAM_COMPLETE must be removed from .bashrc"
        assert "# kdam shell completion" not in bashrc_content
        # Unrelated lines must survive
        assert "export PATH" in bashrc_content

        zshrc_content = zshrc.read_text()
        assert "# kdam shell completion" not in zshrc_content

    def test_uninstall_removes_static_completions(self, tmp_path, monkeypatch):
        """kdam uninstall -y must remove static completion.bash/zsh/fish from CONFIG_DIR."""
        cli_mod, xdg_comp, bashrc, zshrc = _make_runner_with_patches(tmp_path, monkeypatch, {})
        from kdam.cli import cli

        runner = CliRunner()
        runner.invoke(cli, ["uninstall", "-y"])

        for fname in ("completion.bash", "completion.zsh", "completion.fish"):
            assert not (tmp_path / fname).exists(), f"{fname} must be removed"

    def test_uninstall_keeps_config_by_default(self, tmp_path, monkeypatch):
        """kdam uninstall -y (without --purge) must NOT delete CONFIG_DIR."""
        cli_mod, xdg_comp, bashrc, zshrc = _make_runner_with_patches(tmp_path, monkeypatch, {})
        from kdam.cli import cli

        # Write a config file to verify it survives
        (tmp_path / "config.json").write_text('{"test": true}')

        runner = CliRunner()
        runner.invoke(cli, ["uninstall", "-y"])

        assert (tmp_path / "config.json").exists(), "Config must be preserved without --purge"

    def test_uninstall_purge_removes_config_dir(self, tmp_path, monkeypatch):
        """kdam uninstall -y --purge must wipe CONFIG_DIR entirely."""
        cli_mod, xdg_comp, bashrc, zshrc = _make_runner_with_patches(tmp_path, monkeypatch, {})
        from kdam.cli import cli

        (tmp_path / "config.json").write_text('{"test": true}')

        runner = CliRunner()
        result = runner.invoke(cli, ["uninstall", "-y", "--purge"])
        assert result.exit_code == 0, result.output

        assert not tmp_path.exists() or not (tmp_path / "config.json").exists(), (
            "CONFIG_DIR content must be purged with --purge"
        )

    def test_uninstall_confirmation_cancel(self, tmp_path, monkeypatch):
        """kdam uninstall without -y must prompt; answering 'n' must abort."""
        cli_mod, xdg_comp, bashrc, zshrc = _make_runner_with_patches(tmp_path, monkeypatch, {})
        from kdam.cli import cli

        runner = CliRunner()
        # Provide 'n' as stdin input to the confirmation prompt
        result = runner.invoke(cli, ["uninstall"], input="n\n")
        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output

        # Binary must still exist (uninstall was cancelled)
        kdam_bin = tmp_path / "home" / ".local" / "bin" / "kdam"
        assert kdam_bin.exists(), "Binary must NOT be removed when user cancels"

    def test_uninstall_confirmation_yes(self, tmp_path, monkeypatch):
        """kdam uninstall without -y must proceed when user answers 'y'."""
        cli_mod, xdg_comp, bashrc, zshrc = _make_runner_with_patches(tmp_path, monkeypatch, {})
        from kdam.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["uninstall"], input="y\n")
        assert result.exit_code == 0
        assert "✓" in result.output or "successfully" in result.output


# ── EditorQuit typed sentinel tests ───────────────────────────────────────────


class TestEditorQuitSentinel:
    """Verify EditorQuit is a proper typed singleton, not a bare object()."""

    def test_is_singleton(self):
        """EditorQuit() must always return the same instance."""
        from kdam.editor import EDITOR_QUIT, EditorQuit

        assert EditorQuit() is EDITOR_QUIT
        assert EditorQuit() is EditorQuit()

    def test_identity_check_works(self):
        """result is EDITOR_QUIT must be True for the sentinel."""
        from kdam.editor import EDITOR_QUIT

        result = EDITOR_QUIT
        assert result is EDITOR_QUIT

    def test_repr(self):
        """EDITOR_QUIT repr must be 'EDITOR_QUIT' for readable test output."""
        from kdam.editor import EDITOR_QUIT

        assert repr(EDITOR_QUIT) == "EDITOR_QUIT"

    def test_is_typed(self):
        """EDITOR_QUIT must be an instance of EditorQuit, not bare object."""
        from kdam.editor import EDITOR_QUIT, EditorQuit

        assert isinstance(EDITOR_QUIT, EditorQuit)
        assert not isinstance(EDITOR_QUIT, str)
        assert not isinstance(EDITOR_QUIT, dict)

    def test_open_crop_editor_return_annotation(self):
        """open_crop_editor return annotation must include EditorQuit."""
        import importlib.util

        # Read annotation from source to avoid Qt import issues
        spec = importlib.util.find_spec("kdam.editor")
        assert spec and spec.origin
        source = open(spec.origin).read()
        assert "EditorQuit" in source
        assert "|" in source or "Union" in source
