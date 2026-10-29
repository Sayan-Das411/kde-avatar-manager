"""cli_config.py — config group and subcommands."""

from __future__ import annotations

import json
import re

import click

from ._cli_shared import (
    SERVICE_NAME,
    _complete_config_key,
    _complete_config_value,
    _systemd,
    _timer_active,
    _write_systemd_files,
    console,
    load_config,
    save_config,
)


@click.group(name="config")
def config_cmd() -> None:
    """View and modify avatar manager configuration.

    \b
    SETTINGS OVERVIEW:
    ─────────────────────────────────────────────────────────────────────────────
    The configuration controls how and when avatars rotate, the selection order,
    and fallback settings. Changes to schedule settings take effect immediately
    by automatically reloading and restarting the user's systemd timer.

    \b
    ROTATION MODES (rotation.mode):
      daily      — Rotates once every day at midnight (default).
      interval   — Rotates on a recurring schedule. Supports clock-anchored hours
                   (e.g. 6h -> 00:00, 06:00, 12:00, 18:00) or arbitrary composite
                   timespans (e.g. '12d 13h 52m', '45m').
      times      — Rotates at specific clock times of day (e.g. '08:30, 14:00, 21:15').
      weekly     — Rotates once per week on the specified weekday (default: Monday).
      monthly    — Rotates on the 1st of every month at midnight.

    \b
    SELECTION ORDERS (rotation.order):
      random      — Uniform random selection, automatically avoiding the same image
                    twice in a row if multiple images exist.
      lru         — Least Recently Used (anti-starvation). Prioritizes images that
                    have never been shown or have been waiting the longest, picking
                    from the oldest top-K candidates (with weight support).
      deck        — Shuffle-bag cycle. Shuffles all images like a card deck. Every
                    image is shown exactly once before repeating. New images added
                    mid-cycle are seamlessly shuffled into the unplayed deck.
      sequential  — Deterministic sequential cycle through candidates.
      weighted    — Weighted random selection based on per-image weights ('kdam weight').

    \b
    AVAILABLE CONFIG KEYS:
      rotation.mode            daily | interval | times | weekly | monthly
      rotation.order           random | lru | deck | sequential | weighted
      rotation.interval        Duration string: '6h', '45m', '12d 13h 52m', '3d'
      rotation.interval_hours  Integer hours >= 1 (quick shorthand for simple intervals)
      rotation.start_hour      Integer 0-23 (anchor hour for clock intervals, default 0)
      rotation.times           Comma-separated HH:MM list: '08:30, 14:00, 21:15'
      rotation.weekday         Weekday for weekly mode: mon | tue | wed | thu | fri | sat | sun
      rotation.lru_pool_size   'default' (dynamic 10-50, 15%) or custom integer K >= 1

    \b
    SUB-COMMANDS:
      kdam config set <key> <val>  — Change a configuration setting
      kdam config show             — Print full active configuration as JSON

    \b
    EXAMPLES:
      kdam config set rotation.mode interval
      kdam config set rotation.interval 6h
      kdam config set rotation.start_hour 0        # 00:00, 06:00, 12:00, 18:00
      kdam config set rotation.interval "12d 13h 52m"
      kdam config set rotation.mode times
      kdam config set rotation.times "08:30, 14:00, 21:15"
      kdam config set rotation.order lru
      kdam config set rotation.lru_pool_size default
      kdam config set rotation.order deck
      kdam config show
    """


@config_cmd.command(name="show")
def config_show() -> None:
    """Print current configuration as JSON.

    \b
    Shows all settings including rotation mode, order, interval, state,
    directories, and panic image config. Useful for debugging or scripting.
    """
    config = load_config()
    console.print_json(json.dumps(config.to_dict(), indent=2))


@config_cmd.command(name="set")
@click.argument("key", shell_complete=_complete_config_key)
@click.argument("value", shell_complete=_complete_config_value)
def config_set(key: str, value: str) -> None:
    """Set a configuration value.

    \b
    KEY                        VALUE
    ─────────────────────────  ──────────────────────────────────────────
    rotation.mode              daily | interval | times | weekly | monthly
    rotation.order             random | sequential | weighted | lru | deck
    rotation.interval          e.g. 6h, 45m, 12d 13h 52m (for interval mode)
    rotation.interval_hours    integer ≥ 1 (simple hour interval)
    rotation.start_hour        integer 0–23 (anchor hour for clock intervals)
    rotation.times             HH:MM list (e.g. "08:30, 14:00, 21:15")
    rotation.weekday           mon | tue | wed | thu | fri | sat | sun
    rotation.lru_pool_size     default | integer ≥ 1 (for lru mode)

    \b
    If the systemd timer is active, schedule changes take effect immediately
    (the timer is reloaded and restarted automatically).

    \b
    Examples:
      kdam config set rotation.mode interval
      kdam config set rotation.interval 6h
      kdam config set rotation.start_hour 0        # 00:00, 06:00, 12:00, 18:00
      kdam config set rotation.interval "12d 13h 52m"
      kdam config set rotation.mode times
      kdam config set rotation.times "08:30, 14:00, 21:15"
      kdam config set rotation.mode weekly
      kdam config set rotation.weekday sun
      kdam config set rotation.order lru
      kdam config set rotation.lru_pool_size default
      kdam config set rotation.order deck
    """
    config = load_config()
    section, _, field = key.partition(".")

    if section == "rotation":
        reload_timer_needed = False

        if field == "mode":
            val_norm = value.lower()
            if val_norm not in ("daily", "interval", "times", "weekly", "monthly"):
                console.print("[red]rotation.mode must be: daily | interval | times | weekly | monthly[/red]")
                raise SystemExit(1)
            config.rotation.mode = val_norm
            value = val_norm
            reload_timer_needed = True

        elif field == "order":
            val_norm = value.lower()
            if val_norm in ("least-recent", "least_recent"):
                val_norm = "lru"
            elif val_norm in ("shuffle-bag", "shuffle_bag"):
                val_norm = "deck"

            if val_norm not in ("sequential", "random", "weighted", "lru", "deck"):
                console.print(
                    "[red]rotation.order must be: sequential | random | weighted | lru | deck[/red]"
                )
                raise SystemExit(1)
            config.rotation.order = val_norm
            value = val_norm

        elif field == "interval":
            # Test duration format
            from ._cli_shared import _parse_duration

            try:
                td = _parse_duration(value)
                if int(td.total_seconds()) <= 0:
                    raise ValueError
            except Exception:
                console.print(f"[red]Cannot parse interval {value!r}. Use e.g. 6h, 45m, 12d 13h 52m.[/red]")
                raise SystemExit(1) from None

            config.rotation.interval = value.strip()
            # If it's a simple hour interval, also keep interval_hours in sync
            secs = int(td.total_seconds())
            if secs >= 3600 and secs % 3600 == 0:
                config.rotation.interval_hours = secs // 3600
            reload_timer_needed = True

        elif field == "interval_hours":
            try:
                h = int(value)
            except ValueError:
                console.print("[red]rotation.interval_hours must be an integer.[/red]")
                raise SystemExit(1) from None
            if h < 1:
                console.print("[red]rotation.interval_hours must be ≥ 1.[/red]")
                raise SystemExit(1)
            config.rotation.interval_hours = h
            config.rotation.interval = f"{h}h"
            reload_timer_needed = True

        elif field == "start_hour":
            try:
                sh = int(value)
            except ValueError:
                console.print("[red]rotation.start_hour must be an integer between 0 and 23.[/red]")
                raise SystemExit(1) from None
            if not (0 <= sh <= 23):
                console.print("[red]rotation.start_hour must be between 0 and 23.[/red]")
                raise SystemExit(1)
            config.rotation.start_hour = sh
            reload_timer_needed = True

        elif field == "times":
            # Expects comma-separated times like "08:30, 14:00, 21:15"
            time_parts = [t.strip() for t in value.split(",") if t.strip()]
            valid_times = []
            for t in time_parts:
                m = re.match(r"^(\d{1,2}):(\d{2})$", t)
                if not m:
                    console.print(
                        f"[red]Invalid time format {t!r}. Expected HH:MM (e.g. 08:30, 14:00).[/red]"
                    )
                    raise SystemExit(1)
                hh, mm = int(m.group(1)), int(m.group(2))
                if not (0 <= hh <= 23 and 0 <= mm <= 59):
                    console.print(f"[red]Invalid time {t!r} — hour must be 0-23, minute 0-59.[/red]")
                    raise SystemExit(1)
                valid_times.append(f"{hh:02d}:{mm:02d}")
            if not valid_times:
                console.print("[red]Please specify at least one time in HH:MM format.[/red]")
                raise SystemExit(1)
            config.rotation.times = valid_times
            value = ", ".join(valid_times)
            reload_timer_needed = True

        elif field == "weekday":
            val_norm = value.lower()[:3]
            days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            if val_norm not in days:
                console.print("[red]rotation.weekday must be: mon | tue | wed | thu | fri | sat | sun[/red]")
                raise SystemExit(1)
            config.rotation.weekday = val_norm
            value = val_norm
            reload_timer_needed = True

        elif field == "lru_pool_size":
            val_norm = value.lower()
            if val_norm != "default":
                try:
                    k_val = int(val_norm)
                except ValueError:
                    console.print("[red]rotation.lru_pool_size must be 'default' or an integer ≥ 1.[/red]")
                    raise SystemExit(1) from None
                if k_val < 1:
                    console.print("[red]rotation.lru_pool_size must be ≥ 1.[/red]")
                    raise SystemExit(1)
                config.rotation.lru_pool_size = str(k_val)

                # Check current active image count and inform user if K > N
                from .db import ImageDB

                db = ImageDB()
                active_n = len(db.active())
                if active_n < k_val:
                    diff = k_val - active_n
                    console.print(
                        f"[yellow]Notice: Currently {active_n} active image(s) registered"
                        f" (smaller than K={k_val}).[/yellow]\n"
                        f"[dim]The entire pool of {active_n} image(s) will be used"
                        f" until at least {k_val} images "
                        f"are present (add {diff} more image(s)).[/dim]"
                    )
            else:
                config.rotation.lru_pool_size = "default"

        else:
            console.print(f"[red]Unknown key: {key}[/red]")
            console.print(
                "[dim]Valid keys: rotation.mode | rotation.order | rotation.interval"
                " | rotation.interval_hours | "
                "rotation.start_hour | rotation.times | rotation.weekday | rotation.lru_pool_size[/dim]"
            )
            raise SystemExit(1)

        if reload_timer_needed and _timer_active():
            _write_systemd_files(config)
            _systemd("daemon-reload")
            _systemd("restart", f"{SERVICE_NAME}.timer")
            console.print("[dim]Timer reloaded with new schedule.[/dim]")

    else:
        console.print(f"[red]Unknown section: {section!r} — key must be in 'section.field' format.[/red]")
        console.print("[dim]Example: kdam config set rotation.mode daily[/dim]")
        raise SystemExit(1)

    save_config(config)
    console.print(f"[green]✓ {key} = {value}[/green]")
