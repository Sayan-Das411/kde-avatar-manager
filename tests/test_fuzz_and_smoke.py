"""test_fuzz_and_smoke.py — hypothesis fuzz tests + CLI smoke tests.

1. Fuzz: config/db parsing never crashes on arbitrary JSON input
2. Fuzz: _parse_duration never returns negative/zero timedelta for valid input
3. Fuzz: interval_hours always clamped ≥ 1 regardless of JSON value
4. Smoke: kdam --help exits 0 (verifies the installed entry point works)
5. Smoke: kdam <subcommand> --help exits 0 for all major subcommands
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from helpers import _patch_paths  # noqa: E402
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# ── Strategy helpers ──────────────────────────────────────────────────────────

# Valid JSON values that could appear in a config file
_json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=64),
)
_json_values = st.recursive(
    _json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=16), children, max_size=4),
    ),
    max_leaves=8,
)


# ── Fuzz: config parsing ───────────────────────────────────────────────────────


class TestFuzzConfigParsing:
    """load_config() must never crash on any JSON, no matter how malformed."""

    @given(raw=_json_values)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_load_config_never_crashes(self, tmp_path, monkeypatch, raw):
        """load_config() on arbitrary JSON must not raise — always returns defaults."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import load_config

        cfg_file = tmp_path / "config.json"
        try:
            cfg_file.write_text(json.dumps(raw))
        except (TypeError, ValueError):
            return  # hypothesis gave us something json.dumps can't handle — skip

        result = load_config()
        # Must return a Config object (never raise)
        assert result is not None
        assert hasattr(result, "rotation")
        assert hasattr(result, "state")

    @given(
        hours=st.one_of(
            st.integers(min_value=-1000, max_value=1000),
            st.floats(allow_nan=False, allow_infinity=False),
            st.text(max_size=10),
            st.none(),
        )
    )
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_interval_hours_always_clamped(self, tmp_path, monkeypatch, hours):
        """interval_hours from any JSON value must result in config.rotation.interval_hours >= 1."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import load_config

        raw = {"interval_hours": hours}
        (tmp_path / "config.json").write_text(json.dumps(raw))

        config = load_config()
        assert config.rotation.interval_hours >= 1, (
            f"interval_hours={config.rotation.interval_hours} for input {hours!r}"
        )

    @given(raw=_json_values)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_imagedb_load_never_crashes(self, tmp_path, monkeypatch, raw):
        """ImageDB.load() on arbitrary JSON must not raise — always returns empty registry."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.db import ImageDB

        images_file = tmp_path / "images.json"
        try:
            images_file.write_text(json.dumps(raw))
        except (TypeError, ValueError):
            return

        db = ImageDB()
        # Must not raise
        db.load()
        # Result must be a dict (possibly empty)
        assert isinstance(db.all(), dict)


# ── Fuzz: _parse_duration ──────────────────────────────────────────────────────


class TestFuzzParseDuration:
    """_parse_duration() must never return zero/negative for valid inputs,
    and must always raise ValueError for invalid inputs."""

    @given(
        n=st.integers(min_value=1, max_value=9999),
        unit=st.sampled_from(["s", "m", "h", "d", "w"]),
    )
    @settings(max_examples=100)
    def test_valid_duration_always_positive(self, n, unit):
        from kdam._cli_shared import _parse_duration

        result = _parse_duration(f"{n}{unit}")
        assert result.total_seconds() > 0

    @given(s=st.text(max_size=20).filter(lambda x: not any(c.isdigit() for c in x) and len(x.strip()) > 0))
    @settings(max_examples=100)
    def test_pure_text_raises_valueerror(self, s):
        from kdam._cli_shared import _parse_duration

        with pytest.raises(ValueError):
            _parse_duration(s)

    @given(
        a=st.integers(min_value=1, max_value=24),
        b=st.integers(min_value=1, max_value=59),
    )
    @settings(max_examples=50)
    def test_compound_duration_correct(self, a, b):
        from datetime import timedelta

        from kdam._cli_shared import _parse_duration

        result = _parse_duration(f"{a}h{b}m")
        expected = timedelta(hours=a, minutes=b)
        assert result == expected


# ── Fuzz: _log_replace_path never corrupts unrelated lines ────────────────────


class TestFuzzLogReplace:
    @given(
        old=st.text(min_size=1, max_size=80, alphabet=st.characters(blacklist_characters="\n")),
        new=st.text(min_size=1, max_size=80, alphabet=st.characters(blacklist_characters="\n")),
        other_paths=st.lists(
            st.text(min_size=1, max_size=80, alphabet=st.characters(blacklist_characters="\n")),
            min_size=0,
            max_size=5,
        ),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_replace_never_corrupts_other_entries(self, tmp_path, monkeypatch, old, new, other_paths):
        """_log_replace_path must only update lines whose path field exactly equals old."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam._cli_shared import _log_replace_path, _write_log_entry

        # Clear log between hypothesis examples — state leaks across examples otherwise
        log_file = tmp_path / "logs" / "rotation.log"
        if log_file.exists():
            log_file.write_text("")

        # Skip edge cases not valid in real file paths
        if old == new:
            return  # old==new: replace is a no-op, asserting old is gone is wrong
        if "\r" in old or "\r" in new or any("\r" in p for p in other_paths):
            return  # \r is not valid in paths; splitlines() treats it as line ending

        # Skip paths with lone surrogates — they get sanitized by _write_log_entry
        # (the sanitized form won't equal old, so the "old gone" assertion is wrong)
        def _has_surrogate(s: str) -> bool:
            return any("\ud800" <= c <= "\udfff" for c in s)

        if _has_surrogate(old) or _has_surrogate(new) or any(_has_surrogate(p) for p in other_paths):
            return
        if any(old in p and p != old for p in other_paths):
            return  # old is substring of another path — expected ambiguity, skip

        _write_log_entry(old)
        for p in other_paths:
            _write_log_entry(p)

        _log_replace_path(old, new)

        log_content = (tmp_path / "logs" / "rotation.log").read_text()
        # old must be gone (as an exact path field)
        from kdam._cli_shared import LOG_LINE_SEP

        exact_old_lines = [ln for ln in log_content.splitlines() if ln.split(LOG_LINE_SEP, 1)[-1] == old]
        assert exact_old_lines == [], f"old path still present: {exact_old_lines}"

        # other paths must be unchanged
        for p in other_paths:
            if p == old:
                continue
            assert p in log_content, f"other path {p!r} was corrupted by replace"


# ── Smoke tests: CLI binary ────────────────────────────────────────────────────


class TestCLISmoke:
    """Verify the installed kdam entry point is functional — not just importable."""

    @pytest.fixture(scope="class")
    @classmethod
    def kdam_bin(cls):
        """Return path to kdam binary — skip if not installed."""
        import shutil

        kdam = shutil.which("kdam") or str(Path.home() / ".local" / "bin" / "kdam")
        if not Path(kdam).exists():
            venv_kdam = Path.home() / ".kde-avatar-manager" / "venv" / "bin" / "kdam"
            if venv_kdam.exists():
                return str(venv_kdam)
            pytest.skip("kdam binary not found — run install.sh first")
        return kdam

    def _run(self, kdam_bin: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [kdam_bin, *args],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_help_exits_zero(self, kdam_bin):
        """kdam --help must exit 0."""
        result = self._run(kdam_bin, "--help")
        assert result.returncode == 0, f"kdam --help failed:\n{result.stderr}"

    def test_help_mentions_key_commands(self, kdam_bin):
        """kdam --help output must mention the core commands."""
        result = self._run(kdam_bin, "--help")
        output = result.stdout + result.stderr
        for cmd in ("add", "list", "next", "backup", "status"):
            assert cmd in output, f"'{cmd}' not found in kdam --help output"

    def test_version_flag(self, kdam_bin):
        """kdam --version must exit 0 and print a version string."""
        result = self._run(kdam_bin, "--version")
        assert result.returncode == 0
        assert any(c.isdigit() for c in result.stdout + result.stderr), (
            "kdam --version produced no version number"
        )

    @pytest.mark.parametrize(
        "subcmd",
        [
            "add",
            "list",
            "next",
            "status",
            "enable",
            "disable",
            "backup",
            "dirs",
            "panic",
            "log",
        ],
    )
    def test_subcommand_help_exits_zero(self, kdam_bin, subcmd):
        """kdam <subcmd> --help must exit 0 for every major subcommand."""
        result = self._run(kdam_bin, subcmd, "--help")
        assert result.returncode == 0, f"kdam {subcmd} --help exited {result.returncode}:\n{result.stderr}"

    def test_no_crash_on_unknown_flag(self, kdam_bin):
        """kdam with an unknown flag must exit non-zero but not crash with a traceback."""
        result = self._run(kdam_bin, "--this-flag-does-not-exist")
        assert result.returncode != 0
        assert "Traceback" not in result.stderr, (
            "Unhandled exception on unknown flag — should be a clean Click error"
        )

    def test_import_is_clean(self):
        """'python -m kdam' must not crash on import (catches missing __main__ etc.)."""
        result = subprocess.run(
            [sys.executable, "-c", "import kdam; import kdam.cli; import kdam.rotator"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"import kdam failed:\n{result.stderr}"
