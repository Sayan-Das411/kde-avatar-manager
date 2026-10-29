"""test_systemd_consistency.py — verify the committed systemd/ files match what
_write_systemd_files() generates at runtime.

If this test fails it means someone edited systemd/kde-avatar-rotate.service or
systemd/kde-avatar-rotate.timer without updating _write_systemd_files() (or vice
versa), which would make 'kdam enable' install different unit files than what is
committed to the repo.
"""

from __future__ import annotations

from pathlib import Path

# Project root is two levels up from this file (tests/ → kde-avatar-manager/)
_PROJECT_ROOT = Path(__file__).parent.parent
_SYSTEMD_DIR = _PROJECT_ROOT / "systemd"


def _strip_comments_and_blank(text: str) -> str:
    """Remove comment lines and collapse blank lines for comparison."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "":
            continue
        lines.append(stripped)
    return "\n".join(lines)


def _get_generated_service(kdam_bin: str = "%h/.local/bin/kdam") -> str:
    """Return what _write_systemd_files() writes for the .service file."""
    return (
        "[Unit]\n"
        "Description=KDE Avatar Auto-Rotator\n"
        "After=graphical-session.target\n"
        "Wants=graphical-session.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStartPre=/bin/sleep 5\n"
        f"ExecStart={kdam_bin} next --quiet\n"
    )


def _get_generated_timer_daily() -> str:
    return (
        "[Unit]\nDescription=KDE Avatar Auto-Rotator Timer\n\n"
        "[Timer]\n"
        "OnCalendar=daily\n"
        "Persistent=true\n"
        "RandomizedDelaySec=30s\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )


def _get_generated_timer_interval(hours: int = 3) -> str:
    return (
        "[Unit]\nDescription=KDE Avatar Auto-Rotator Timer\n\n"
        "[Timer]\n"
        f"OnBootSec=1min\n"
        f"OnUnitActiveSec={hours}h\n"
        "Persistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )


class TestSystemdFilesConsistency:
    """The committed systemd/ unit files must match _write_systemd_files() output."""

    def test_service_file_exists(self):
        assert (_SYSTEMD_DIR / "kde-avatar-rotate.service").exists(), (
            "systemd/kde-avatar-rotate.service is missing from the repository"
        )

    def test_timer_file_exists(self):
        assert (_SYSTEMD_DIR / "kde-avatar-rotate.timer").exists(), (
            "systemd/kde-avatar-rotate.timer is missing from the repository"
        )

    def test_service_has_graphical_session_target(self):
        """After=graphical-session.target is required so notify-send works on login.

        (notification daemon not yet ready at dbus.socket time)
        """
        content = (_SYSTEMD_DIR / "kde-avatar-rotate.service").read_text()
        assert "After=graphical-session.target" in content, (
            "kde-avatar-rotate.service is missing 'After=graphical-session.target'.\n"
            "This means the service may start before D-Bus is available."
        )

    def test_service_key_fields_match_generator(self):
        """Core fields of the committed .service must match _write_systemd_files() output."""
        committed = _strip_comments_and_blank((_SYSTEMD_DIR / "kde-avatar-rotate.service").read_text())
        generated = _strip_comments_and_blank(_get_generated_service())

        # Compare field by field so we aren't sensitive to ExecStart path differences
        # (committed uses %h/.local/bin/kdam; runtime uses the actual venv path)
        committed_fields = dict(line.split("=", 1) for line in committed.splitlines() if "=" in line)
        generated_fields = dict(line.split("=", 1) for line in generated.splitlines() if "=" in line)

        for key in ("Description", "Type"):
            assert committed_fields.get(key) == generated_fields.get(key), (
                f"Mismatch on {key!r}: committed={committed_fields.get(key)!r} "
                f"vs generated={generated_fields.get(key)!r}"
            )

        assert "ExecStart" in committed_fields, "ExecStart missing from committed service file"
        assert "next --quiet" in committed_fields["ExecStart"], "ExecStart must end with 'next --quiet'"

    def test_timer_has_persistent(self):
        """Persistent=true ensures the timer fires after missed runs (e.g. laptop was off)."""
        content = (_SYSTEMD_DIR / "kde-avatar-rotate.timer").read_text()
        assert "Persistent=true" in content, (
            "kde-avatar-rotate.timer is missing 'Persistent=true'.\n"
            "Without this, rotations are skipped when the machine is off at fire time."
        )

    def test_timer_has_wantedby_timers_target(self):
        content = (_SYSTEMD_DIR / "kde-avatar-rotate.timer").read_text()
        assert "WantedBy=timers.target" in content, (
            "kde-avatar-rotate.timer is missing 'WantedBy=timers.target' in [Install]"
        )

    def test_service_type_is_oneshot(self):
        content = (_SYSTEMD_DIR / "kde-avatar-rotate.service").read_text()
        assert "Type=oneshot" in content, (
            "Service type must be oneshot (kdam next exits after a single rotation)"
        )


class TestWriteSystemdFilesFunction:
    """_write_systemd_files() must produce files with the expected content."""

    def test_daily_timer_uses_oncalendar(self, tmp_path, monkeypatch):
        import kdam._cli_shared as shared
        from kdam.config import Config, PanicConfig, RotationConfig, State

        monkeypatch.setattr(shared, "SYSTEMD_USER_DIR", tmp_path)
        monkeypatch.setattr(shared, "_SERVICE_FILE", tmp_path / "kdam-rotate.service")
        monkeypatch.setattr(shared, "_TIMER_FILE", tmp_path / "kdam-rotate.timer")

        config = Config(
            rotation=RotationConfig(mode="daily", order="random", interval_hours=24),
            panic=PanicConfig(),
            state=State(enabled=True),
            directories=[],
        )
        shared._write_systemd_files(config)

        timer = (tmp_path / "kdam-rotate.timer").read_text()
        assert "OnCalendar=daily" in timer
        assert "Persistent=true" in timer
        assert "RandomizedDelaySec=30s" in timer
        assert "OnUnitActiveSec" not in timer

    def test_interval_timer_clock_aligned(self, tmp_path, monkeypatch):
        import kdam._cli_shared as shared
        from kdam.config import Config, PanicConfig, RotationConfig, State

        monkeypatch.setattr(shared, "SYSTEMD_USER_DIR", tmp_path)
        monkeypatch.setattr(shared, "_SERVICE_FILE", tmp_path / "kdam-rotate.service")
        monkeypatch.setattr(shared, "_TIMER_FILE", tmp_path / "kdam-rotate.timer")

        # 6 hours starting at 0 -> 00, 06, 12, 18
        config = Config(
            rotation=RotationConfig(mode="interval", order="random", interval="6h", start_hour=0),
            panic=PanicConfig(),
            state=State(enabled=True),
            directories=[],
        )
        shared._write_systemd_files(config)

        timer = (tmp_path / "kdam-rotate.timer").read_text()
        assert "OnCalendar=*-*-* 00,06,12,18:00:00" in timer
        assert "Persistent=true" in timer

    def test_interval_timer_complex_timespan(self, tmp_path, monkeypatch):
        import kdam._cli_shared as shared
        from kdam.config import Config, PanicConfig, RotationConfig, State

        monkeypatch.setattr(shared, "SYSTEMD_USER_DIR", tmp_path)
        monkeypatch.setattr(shared, "_SERVICE_FILE", tmp_path / "kdam-rotate.service")
        monkeypatch.setattr(shared, "_TIMER_FILE", tmp_path / "kdam-rotate.timer")

        # Complex duration: 12d 13h 52m
        config = Config(
            rotation=RotationConfig(mode="interval", order="random", interval="12d 13h 52m"),
            panic=PanicConfig(),
            state=State(enabled=True),
            directories=[],
        )
        shared._write_systemd_files(config)

        timer = (tmp_path / "kdam-rotate.timer").read_text()
        assert "OnUnitActiveSec=12d 13h 52m" in timer
        assert "OnBootSec=1min" in timer
        assert "Persistent=true" in timer

    def test_times_mode_timer(self, tmp_path, monkeypatch):
        import kdam._cli_shared as shared
        from kdam.config import Config, PanicConfig, RotationConfig, State

        monkeypatch.setattr(shared, "SYSTEMD_USER_DIR", tmp_path)
        monkeypatch.setattr(shared, "_SERVICE_FILE", tmp_path / "kdam-rotate.service")
        monkeypatch.setattr(shared, "_TIMER_FILE", tmp_path / "kdam-rotate.timer")

        config = Config(
            rotation=RotationConfig(mode="times", order="random", times=["08:30", "14:00", "21:15"]),
            panic=PanicConfig(),
            state=State(enabled=True),
            directories=[],
        )
        shared._write_systemd_files(config)

        timer = (tmp_path / "kdam-rotate.timer").read_text()
        assert "OnCalendar=*-*-* 08:30:00" in timer
        assert "OnCalendar=*-*-* 14:00:00" in timer
        assert "OnCalendar=*-*-* 21:15:00" in timer

    def test_weekly_and_monthly_timers(self, tmp_path, monkeypatch):
        import kdam._cli_shared as shared
        from kdam.config import Config, PanicConfig, RotationConfig, State

        monkeypatch.setattr(shared, "SYSTEMD_USER_DIR", tmp_path)
        monkeypatch.setattr(shared, "_SERVICE_FILE", tmp_path / "kdam-rotate.service")
        monkeypatch.setattr(shared, "_TIMER_FILE", tmp_path / "kdam-rotate.timer")

        # Weekly on Sunday
        cfg_weekly = Config(
            rotation=RotationConfig(mode="weekly", order="random", weekday="sun"),
            panic=PanicConfig(),
            state=State(enabled=True),
            directories=[],
        )
        shared._write_systemd_files(cfg_weekly)
        timer_w = (tmp_path / "kdam-rotate.timer").read_text()
        assert "OnCalendar=Sun *-*-* 00:00:00" in timer_w

        # Monthly
        cfg_monthly = Config(
            rotation=RotationConfig(mode="monthly", order="random"),
            panic=PanicConfig(),
            state=State(enabled=True),
            directories=[],
        )
        shared._write_systemd_files(cfg_monthly)
        timer_m = (tmp_path / "kdam-rotate.timer").read_text()
        assert "OnCalendar=*-*-01 00:00:00" in timer_m

    def test_service_contains_next_quiet(self, tmp_path, monkeypatch):
        import kdam._cli_shared as shared
        from kdam.config import Config, PanicConfig, RotationConfig, State

        monkeypatch.setattr(shared, "SYSTEMD_USER_DIR", tmp_path)
        monkeypatch.setattr(shared, "_SERVICE_FILE", tmp_path / "kdam-rotate.service")
        monkeypatch.setattr(shared, "_TIMER_FILE", tmp_path / "kdam-rotate.timer")

        config = Config(
            rotation=RotationConfig(mode="daily", order="random", interval_hours=24),
            panic=PanicConfig(),
            state=State(enabled=True),
            directories=[],
        )
        shared._write_systemd_files(config)

        service = (tmp_path / "kdam-rotate.service").read_text()
        assert "next --quiet" in service
        assert "After=graphical-session.target" in service
        assert "Type=oneshot" in service

    def test_committed_service_matches_generator_key_fields(self, tmp_path, monkeypatch):
        """Cross-check: the committed file and the generator agree on all non-path fields."""
        import kdam._cli_shared as shared
        from kdam.config import Config, PanicConfig, RotationConfig, State

        monkeypatch.setattr(shared, "SYSTEMD_USER_DIR", tmp_path)
        monkeypatch.setattr(shared, "_SERVICE_FILE", tmp_path / "kdam-rotate.service")
        monkeypatch.setattr(shared, "_TIMER_FILE", tmp_path / "kdam-rotate.timer")

        config = Config(
            rotation=RotationConfig(mode="daily", order="random", interval_hours=24),
            panic=PanicConfig(),
            state=State(enabled=True),
            directories=[],
        )
        shared._write_systemd_files(config)
        generated = (tmp_path / "kdam-rotate.service").read_text()

        committed = (_SYSTEMD_DIR / "kde-avatar-rotate.service").read_text()

        fields = ("Description=KDE Avatar Auto-Rotator", "Type=oneshot", "After=graphical-session.target")
        for field in fields:
            assert field in generated, f"Generated service missing: {field!r}"
            assert field in committed, f"Committed service missing: {field!r} — update systemd/ to match"
