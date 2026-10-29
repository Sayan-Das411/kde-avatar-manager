"""Shared helpers, constants, and utilities used across all kdam CLI modules."""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import click
from rich.console import Console

from . import kde, notify, scanner
from .config import (
    AVATAR_SIZE,
    BACKUP_FILE,
    LOG_FILE,
    LOGS_DIR,  # noqa: F401
    PROCESSED_DIR,
    Config,
    _db_lock,
    load_config,
    save_config,
)
from .db import ImageDB

console = Console()

# ── Systemd helpers ────────────────────────────────────────────────────────────

SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_NAME = "kde-avatar-rotate"
_SERVICE_FILE = SYSTEMD_USER_DIR / f"{SERVICE_NAME}.service"
_TIMER_FILE = SYSTEMD_USER_DIR / f"{SERVICE_NAME}.timer"


def _kdam_bin() -> str:
    return shutil.which("kdam") or str(Path.home() / ".local" / "bin" / "kdam")


def _build_timer_directives(config: Config) -> tuple[list[str], str]:
    """Generate systemd [Timer] directives based on config.rotation.

    Returns:
        (lines, description_summary)
    """
    mode = config.rotation.mode.lower()
    lines: list[str] = []
    desc = mode

    if mode == "daily":
        lines = [
            "OnCalendar=daily",
            "Persistent=true",
            "RandomizedDelaySec=30s",
        ]
        desc = "daily at midnight"

    elif mode == "weekly":
        w = config.rotation.weekday[:3].capitalize()
        lines = [
            f"OnCalendar={w} *-*-* 00:00:00",
            "Persistent=true",
            "RandomizedDelaySec=30s",
        ]
        desc = f"weekly on {w} at midnight"

    elif mode == "monthly":
        lines = [
            "OnCalendar=*-*-01 00:00:00",
            "Persistent=true",
            "RandomizedDelaySec=30s",
        ]
        desc = "monthly on the 1st at midnight"

    elif mode == "times":
        times_list = config.rotation.times or ["00:00"]
        for t_str in times_list:
            t_clean = t_str.strip()
            # If formatted like 9:00, pad to 09:00:00
            parts = t_clean.split(":")
            hh = f"{int(parts[0]):02d}"
            mm = f"{int(parts[1]):02d}" if len(parts) > 1 else "00"
            lines.append(f"OnCalendar=*-*-* {hh}:{mm}:00")
        lines.append("Persistent=true")
        lines.append("RandomizedDelaySec=30s")
        desc = f"daily at {', '.join(times_list)}"

    elif mode == "interval":
        # Check if a custom interval string is set (e.g. "6h", "45m", "12d 13h 52m")
        interval_str = config.rotation.interval.strip()
        is_even_clock_hour = False
        aligned_hours: list[int] = []

        if not interval_str:
            # Legacy interval_hours
            h = config.rotation.interval_hours
            if 24 % h == 0 and h < 24:
                is_even_clock_hour = True
                start_h = config.rotation.start_hour % h
                aligned_hours = [(start_h + i * h) % 24 for i in range(24 // h)]
                aligned_hours.sort()
            interval_str = f"{h}h"
        else:
            # Test if interval_str represents an integer number of hours that cleanly divides 24
            try:
                td = _parse_duration(interval_str)
                total_seconds = int(td.total_seconds())
                if total_seconds > 0 and total_seconds % 3600 == 0:
                    h = total_seconds // 3600
                    if 24 % h == 0 and h < 24:
                        is_even_clock_hour = True
                        start_h = config.rotation.start_hour % h
                        aligned_hours = [(start_h + i * h) % 24 for i in range(24 // h)]
                        aligned_hours.sort()
            except Exception:
                pass

        if is_even_clock_hour and aligned_hours:
            hours_spec = ",".join(f"{hr:02d}" for hr in aligned_hours)
            lines = [
                f"OnCalendar=*-*-* {hours_spec}:00:00",
                "Persistent=true",
                "RandomizedDelaySec=30s",
            ]
            desc = f"every {h}h (clock-aligned: {hours_spec}:00)"
        else:
            # Complex, arbitrary timespan, or not evenly dividing 24:
            # Use systemd OnUnitActiveSec timespan timer
            lines = [
                "OnBootSec=1min",
                f"OnUnitActiveSec={interval_str}",
                "Persistent=true",
            ]
            desc = f"every {interval_str} (duration interval)"
    else:
        # Fallback to daily
        lines = [
            "OnCalendar=daily",
            "Persistent=true",
            "RandomizedDelaySec=30s",
        ]
        desc = "daily at midnight"

    return lines, desc


def _write_systemd_files(config: Config) -> None:
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)

    _SERVICE_FILE.write_text(
        "[Unit]\n"
        "Description=KDE Avatar Auto-Rotator\n"
        "# Wait for graphical session so the notification daemon is ready.\n"
        "# Without this, notify-send fires into the void when triggered on\n"
        "# login after a missed midnight timer (PC was powered off at midnight).\n"
        "After=graphical-session.target\n"
        "Wants=graphical-session.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "# Brief delay lets knotificationd/plasmashell settle on fresh login.\n"
        "ExecStartPre=/bin/sleep 5\n"
        f"ExecStart={_kdam_bin()} next --quiet\n"
    )

    directives, _ = _build_timer_directives(config)
    timer_body = (
        "[Unit]\nDescription=KDE Avatar Auto-Rotator Timer\n\n"
        "[Timer]\n" + "\n".join(directives) + "\n\n[Install]\nWantedBy=timers.target\n"
    )

    _TIMER_FILE.write_text(timer_body)


def _systemd(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
    )


def _timer_active() -> bool:
    return _systemd("is-active", f"{SERVICE_NAME}.timer").returncode == 0


def _ensure_backup() -> None:
    if not BACKUP_FILE.exists():
        if kde.backup_current_avatar(BACKUP_FILE):
            console.print("[dim]📷 Snapshotted current avatar as panic backup.[/dim]")


def _process_panic_image(
    src_path: Path,
    crop: dict | None = None,
) -> Path:
    """Crop + resize a panic source image and write to both panic.png and backup.png."""
    from PIL import Image as PILImage

    img = PILImage.open(str(src_path)).convert("RGBA")
    if crop:
        iw, ih = img.size
        x = max(0, min(crop["x"], iw - 1))
        y = max(0, min(crop["y"], ih - 1))
        s = max(1, min(crop["size"], iw - x, ih - y))
        img = img.crop((x, y, x + s, y + s))
    img = img.resize((AVATAR_SIZE, AVATAR_SIZE), PILImage.Resampling.LANCZOS)

    PROCESSED_DIR.mkdir(exist_ok=True)
    panic_out = PROCESSED_DIR / "panic.png"
    img.save(str(panic_out), "PNG")
    img.save(str(BACKUP_FILE), "PNG")
    return panic_out


def _do_panic_reset(config: Config) -> bool:
    """Inner logic for panic reset — shared by 'panic reset' and 'toggle'."""
    src_path: Path | None = None

    if config.panic.path:
        p = Path(config.panic.path)
        if p.exists():
            src_path = p
        else:
            console.print(f"[yellow]Configured panic image missing: {config.panic.path}[/yellow]")

    if not src_path:
        if BACKUP_FILE.exists():
            console.print("[dim]Using auto-snapshot backup as panic image.[/dim]")
            src_path = BACKUP_FILE

    if not src_path or not src_path.exists():
        console.print("[red]Panic image not found, and no initial backup exists.[/red]")
        console.print("[dim]Set a panic image with: kdam panic set <path>[/dim]")
        notify.send("❌ Panic reset failed", "Panic image not found.", urgency="critical")
        return False

    try:
        use_crop = (
            config.panic.crop
            and config.panic.path
            and src_path.resolve() == Path(config.panic.path).expanduser().resolve()
        )
        panic_out = _process_panic_image(
            src_path,
            config.panic.crop if use_crop else None,
        )
        kde.set_avatar(panic_out)
        config.state.last_image_path = str(src_path)
        save_config(config)
        try:
            _write_log_entry(str(src_path.resolve()))
        except Exception:
            pass
        return True
    except Exception as exc:
        console.print(f"[red]Error applying panic image: {exc}[/red]")
        return False


LOG_LINE_SEP = " | "
"""Separator used between timestamp and path in rotation.log entries.

Both _write_log_entry() and _log_replace_path() use this constant so the
format is defined in exactly one place. Format: ``{ISO8601} | {abs_path}``
"""


def _write_log_entry(path: str) -> None:
    """Append one rotation log entry for *path* under the advisory lock.

    Format: ``{datetime.now().isoformat()} | {abs_path}\\n``

    This is the **only** function that should write to LOG_FILE directly.
    All other log mutations go through :func:`_log_replace_path`.

    Paths containing lone surrogates or other non-UTF-8 characters are
    sanitized with ``backslashreplace`` so the log file is always valid UTF-8.
    """
    # Sanitize: encode/decode with backslashreplace to handle lone surrogates
    safe_path = path.encode("utf-8", errors="backslashreplace").decode("utf-8")
    line = f"{datetime.now().isoformat()}{LOG_LINE_SEP}{safe_path}\n"
    with _db_lock():
        with LOG_FILE.open("a") as f:
            f.write(line)


def _log_replace_path(old_path: str, new_path: str) -> None:
    """Update rotation log entries for old_path to new_path (line-aware).

    Uses LOG_LINE_SEP field matching instead of plain str.replace to avoid
    false positives when old_path is a substring of a longer path in the log.
    """
    try:
        if not LOG_FILE.exists():
            return
        lines = LOG_FILE.read_text().splitlines(keepends=True)
        new_lines = []
        changed = False
        for line in lines:
            parts = line.split(LOG_LINE_SEP, 1)
            if len(parts) == 2 and parts[1].rstrip("\n") == old_path:
                line = f"{parts[0]}{LOG_LINE_SEP}{new_path}\n"
                changed = True
            new_lines.append(line)
        if changed:
            with _db_lock():
                LOG_FILE.write_text("".join(new_lines))
    except Exception:
        pass


def _parse_duration(s: str) -> timedelta:
    """Parse a human duration string like '3h', '2d', '1w', '30m', '1h30m'.

    Raises ValueError for unrecognised input or trailing garbage (e.g. '3hXYZ').
    """
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    cleaned = s.strip().lower()
    # Reject strings that contain characters outside [0-9smhdw whitespace]
    if not re.fullmatch(r"[\d\s smhdw]+", cleaned):
        raise ValueError(f"Cannot parse duration {s!r}. Use e.g. 30m, 3h, 2d, 1w.")
    matches = re.findall(r"(\d+)\s*([smhdw])", cleaned)
    total = sum(int(n) * units[u] for n, u in matches)
    if total == 0:
        raise ValueError(f"Cannot parse duration {s!r}. Use e.g. 30m, 3h, 2d, 1w.")
    return timedelta(seconds=total)


def _fmt_duration(td: timedelta) -> str:
    total = max(0, int(td.total_seconds()))
    if total >= 86400:
        d, r = divmod(total, 86400)
        h = r // 3600
        return f"{d}d {h}h" if h else f"{d}d"
    if total >= 3600:
        h, r = divmod(total, 3600)
        m = r // 60
        return f"{h}h {m}m" if m else f"{h}h"
    m = total // 60
    return f"{m}m" if m else f"{total}s"


def _resolve_image_path(query: str, db: ImageDB, interactive: bool = True) -> str | None:
    """Resolve a user-provided path or name to an absolute path in registry or disk."""
    import sys

    p = Path(query).expanduser()
    if p.exists():
        return str(p.resolve())

    q_lower = query.strip().lower()
    all_keys = list(db.all().keys())

    for k in all_keys:
        if k.lower() == q_lower:
            return k

    exact_matches = [k for k in all_keys if Path(k).name.lower() == q_lower]
    if len(exact_matches) == 1:
        return exact_matches[0]
    elif len(exact_matches) > 1:
        matches = exact_matches
    else:
        matches = [k for k in all_keys if q_lower in k.lower() or q_lower in Path(k).name.lower()]
        if len(matches) == 1:
            return matches[0]

    if len(matches) > 1:
        if interactive and sys.stdin.isatty():
            console.print(f"\n[yellow]Multiple images match '{query}':[/yellow]")
            for idx, match_path in enumerate(matches, 1):
                exists_tag = "" if Path(match_path).exists() else " [red](missing)[/red]"
                console.print(f"  [[bold cyan]{idx}[/bold cyan]] {match_path}{exists_tag}")
            try:
                choice = click.prompt(
                    f"Select image [1-{len(matches)}] (or 0 to cancel)",
                    type=int,
                    default=1,
                )
                if 1 <= choice <= len(matches):
                    return str(matches[choice - 1])
                console.print("[dim]Selection cancelled.[/dim]")
                return None
            except Exception:
                return None
        else:
            console.print(f"[yellow]Ambiguous image name '{query}' matches multiple files:[/yellow]")
            for match_path in matches:
                console.print(f"  • {match_path}")
            console.print("[dim]Please specify the parent directory or full path.[/dim]")
            return None

    return None


# ── Shell completion helpers ───────────────────────────────────────────────────


def _complete_image_name(ctx, param, incomplete):
    """Return shell completion items for registered image names/paths and disk paths."""
    try:
        from collections import Counter

        from click.shell_completion import CompletionItem

        db = ImageDB()
        all_paths = list(db.all().keys())
        name_counts = Counter(Path(p).name for p in all_paths)

        seen: set[str] = set()
        results = []
        is_path_query = incomplete.startswith(("/", "~", "."))
        home_str = str(Path.home())

        for path in sorted(all_paths):
            name = Path(path).name
            if is_path_query:
                candidates = [path]
                if path.startswith(home_str):
                    candidates.append("~" + path[len(home_str) :])
                for cand in candidates:
                    if cand.lower().startswith(incomplete.lower()) and cand not in seen:
                        seen.add(cand)
                        results.append(CompletionItem(cand))
            else:
                suggestion = name if name_counts[name] == 1 else path
                if suggestion.lower().startswith(incomplete.lower()) and suggestion not in seen:
                    seen.add(suggestion)
                    help_str = path if name_counts[name] > 1 else ""
                    results.append(CompletionItem(suggestion, help=help_str))

        if is_path_query:
            p = Path(incomplete).expanduser()
            parent = p if incomplete.endswith("/") else p.parent
            prefix = "" if incomplete.endswith("/") else p.name
            if parent.is_dir():
                valid_exts = scanner.SUPPORTED_EXTENSIONS
                try:
                    for entry in sorted(parent.iterdir()):
                        if entry.name.startswith("."):
                            continue
                        if (entry.is_file() and entry.suffix.lower() in valid_exts) or entry.is_dir():
                            if entry.name.lower().startswith(prefix.lower()):
                                if incomplete.startswith("~"):
                                    try:
                                        rel = entry.resolve().relative_to(Path.home())
                                        sugg = f"~/{rel}"
                                    except Exception:
                                        sugg = str(entry.resolve())
                                else:
                                    sugg = str(entry.resolve())
                                if entry.is_dir():
                                    sugg += "/"
                                if sugg not in seen:
                                    seen.add(sugg)
                                    results.append(CompletionItem(sugg))
                except Exception:
                    pass

        return results
    except Exception:
        return []


def _complete_configured_dirs(ctx, param, incomplete):
    """Complete directory paths that are currently configured."""
    try:
        from click.shell_completion import CompletionItem

        config = load_config()
        results = []
        home_str = str(Path.home())
        seen: set[str] = set()
        for d in config.directories:
            p_str = str(d.path)
            candidates = [p_str]
            if p_str.startswith(home_str):
                candidates.append("~" + p_str[len(home_str) :])
            for cand in candidates:
                if cand.lower().startswith(incomplete.lower()) and cand not in seen:
                    seen.add(cand)
                    results.append(CompletionItem(cand))
        return results
    except Exception:
        return []


def _complete_command_name(ctx, param, incomplete):
    """Complete top-level kdam command names for 'kdam help <command>'."""
    try:
        from click.shell_completion import CompletionItem

        # Import lazily to avoid circular import — cli is always available at call time
        from .cli import cli as _cli

        commands = sorted(_cli.commands.keys())
        return [CompletionItem(cmd) for cmd in commands if cmd.lower().startswith(incomplete.lower())]
    except Exception:
        return []


_CONFIG_KEYS = [
    "rotation.mode",
    "rotation.order",
    "rotation.interval",
    "rotation.interval_hours",
    "rotation.start_hour",
    "rotation.times",
    "rotation.weekday",
    "rotation.lru_pool_size",
]


def _complete_config_key(ctx, param, incomplete):
    """Complete valid configuration keys for 'kdam config set'."""
    from click.shell_completion import CompletionItem

    return [CompletionItem(k) for k in _CONFIG_KEYS if k.lower().startswith(incomplete.lower())]


def _complete_config_value(ctx, param, incomplete):
    """Complete valid configuration values based on the key."""
    from click.shell_completion import CompletionItem

    key = ctx.args[0] if ctx.args else ctx.params.get("key", "")
    choices: list[str] = []
    if key == "rotation.mode":
        choices = ["daily", "interval", "times", "weekly", "monthly"]
    elif key == "rotation.order":
        choices = ["random", "sequential", "weighted", "lru", "deck"]
    elif key == "rotation.interval":
        choices = ["1h", "2h", "3h", "4h", "6h", "8h", "12h", "24h", "45m", "3d", "7d"]
    elif key == "rotation.start_hour":
        choices = [str(i) for i in range(24)]
    elif key == "rotation.times":
        choices = ["08:00, 18:00", "09:00, 14:00, 21:00", "12:00"]
    elif key == "rotation.weekday":
        choices = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    elif key == "rotation.lru_pool_size":
        choices = ["default", "5", "10", "15", "20", "50"]
    return [CompletionItem(c) for c in choices if c.lower().startswith(incomplete.lower())]
