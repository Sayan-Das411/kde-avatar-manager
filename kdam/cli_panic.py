"""cli_panic.py — panic group and subcommands."""

from __future__ import annotations

from pathlib import Path

import click

from ._cli_shared import (
    BACKUP_FILE,
    SERVICE_NAME,
    ImageDB,
    _complete_image_name,
    _do_panic_reset,
    _process_panic_image,
    _resolve_image_path,
    _systemd,
    console,
    kde,
    load_config,
    save_config,
)


@click.group()
def panic() -> None:
    """Manage the panic (safe/default) avatar image.

    \b
    WHAT IS THE PANIC AVATAR?
    ─────────────────────────────────────────────────────────────────────────────
    The panic image is a designated 'safe' avatar — suitable for work, school,
    screen-sharing, or professional meetings.

    \b
    SAFETY & ISOLATION GUARANTEES:
      • Strictly excluded from rotation: Even if you keep your panic photo in the
        same folder as your regular rotating avatars, kdam will never rotate into it.
      • Instant recovery: Running 'kdam panic reset' immediately applies the panic
        avatar via D-Bus and stops auto-rotation in a single step.
      • Automatic fallback: If your configured panic image is ever deleted or missing
        from disk, kdam safely falls back to the initial pristine system snapshot
        (backup.png). It will NEVER substitute random images from your pool.

    \b
    SUB-COMMANDS:
      kdam panic reset                    — Apply panic avatar now and pause auto-rotation
      kdam panic set <path> [--no-editor] — Designate an image as the panic avatar
      kdam panic set --current            — Snapshot & set active KDE avatar as panic
      kdam panic edit                     — Re-open crop editor for the panic avatar
      kdam panic snapshot                 — Capture currently active KDE avatar to backup.png
      kdam panic show                     — Display configured panic image & backup status

    \b
    EXAMPLES:
      kdam panic set ~/Pictures/safe_headshot.jpg
      kdam panic set --current
      kdam panic reset
      kdam panic edit
      kdam panic show
    """


@panic.command(name="reset")
def panic_reset() -> None:
    """Apply the panic image immediately and stop auto-rotation.

    \b
    Instantly sets your avatar to the configured panic image (or the
    auto-snapshot backup.png if no panic image is set), then disables
    the timer so it stays that way.
    Run 'kdam enable' when you are ready to resume auto-rotation.
    """
    config = load_config()
    ok = _do_panic_reset(config)
    if ok:
        config.state.enabled = False
        save_config(config)
        _systemd("stop", f"{SERVICE_NAME}.timer")
        console.print("[green]✓ Panic reset applied.[/green]")
        console.print("[yellow]Rotation disabled. Run [bold]kdam enable[/bold] to resume.[/yellow]")


@panic.command(name="set")
@click.argument("path", required=False, default=None, shell_complete=_complete_image_name)
@click.option("--current", is_flag=True, help="Snapshot and use currently active KDE avatar.")
@click.option("--no-editor", is_flag=True, help="Set without opening crop editor")
def panic_set(path: str | None, current: bool, no_editor: bool) -> None:
    """Set the panic/default avatar image.

    \b
    The panic image is immediately applied by 'kdam panic reset' and 'kdam toggle'
    to replace your avatar with a safe neutral photo (e.g. before a screen-share).
    It is excluded from the regular rotation pool.

    \b
    Supply a PATH from the registry, or a full file path.
    Use --current to snapshot whatever KDE currently shows as your avatar.

    \b
    Examples:
      kdam panic set ~/Pictures/safe.jpg    — set by file path
      kdam panic set portrait.jpg            — set by registered name
      kdam panic set --current               — snapshot current KDE avatar
      kdam panic set ~/safe.jpg --no-editor  — set without cropping
    """
    from .editor import open_crop_editor

    if current:
        ok = kde.backup_current_avatar(BACKUP_FILE)
        if not ok:
            console.print("[red]Failed to read current avatar from KDE.[/red]")
            return
        abs_path = str(BACKUP_FILE.resolve())
        name_str = "Current KDE Avatar (snapshotted to backup.png)"
    elif path:
        db = ImageDB()
        resolved = _resolve_image_path(path, db)
        if resolved:
            abs_path = resolved
            name_str = Path(resolved).name
        else:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                console.print(f"[red]File not found: {path}[/red]")
                return
            abs_path = str(p)
            name_str = p.name
    else:
        console.print("[yellow]Provide an image path or use --current to capture active avatar.[/yellow]")
        console.print("[dim]Example: kdam panic set ~/Pictures/safe.jpg[/dim]")
        console.print("[dim]Example: kdam panic set safe.jpg[/dim]")
        console.print("[dim]Example: kdam panic set --current[/dim]")
        return

    config = load_config()
    config.panic.path = abs_path

    if not no_editor:
        console.print("[cyan]Opening crop editor for panic image …[/cyan]")
        crop = open_crop_editor(abs_path, config.panic.crop)
        if isinstance(crop, dict):
            config.panic.crop = crop

    save_config(config)

    try:
        _process_panic_image(Path(abs_path), config.panic.crop)
        console.print("[dim]✓ backup.png and processed/panic.png synced to panic image.[/dim]")
    except Exception as exc:
        console.print(f"[yellow]Warning: could not sync backup.png: {exc}[/yellow]")

    console.print(f"[green]✓ Panic image set to: {name_str}[/green]")


@panic.command(name="snapshot")
def panic_snapshot() -> None:
    """Snapshot the currently active KDE avatar to backup.png.

    \b
    Reads the avatar D-Bus currently reports as active and saves it to
    ~/.kde-avatar-manager/backup.png. This file is the automatic fallback
    used by 'kdam panic reset' if no explicit panic image has been set.

    \b
    Run this after first setting up kdam to preserve your original avatar
    as a safe fallback. 'kdam panic set' also keeps backup.png in sync.
    """
    ok = kde.backup_current_avatar(BACKUP_FILE)
    if ok:
        console.print(f"[green]✓ Active avatar snapshotted to:[/green] {BACKUP_FILE}")
        console.print("[dim]This serves as your fallback image if no panic image is set.[/dim]")
    else:
        console.print("[red]Failed to snapshot active avatar from KDE.[/red]")


@panic.command(name="edit")
def panic_edit() -> None:
    """Re-crop the configured panic image.

    \b
    Opens the crop editor for the panic image so you can adjust the framing
    without changing which image is set as the panic image.
    If no panic image is set, falls back to the backup.png snapshot.
    Changes are synced to both processed/panic.png and backup.png immediately.
    """
    from .editor import open_crop_editor

    config = load_config()
    src = config.panic.path if config.panic.path else (str(BACKUP_FILE) if BACKUP_FILE.exists() else None)
    if not src or not Path(src).exists():
        console.print("[red]No panic image configured or backup file missing.[/red]")
        console.print("[dim]Set one with: kdam panic set <path>[/dim]")
        return

    console.print("[cyan]Opening crop editor for panic image …[/cyan]")
    crop = open_crop_editor(src, config.panic.crop)
    if not isinstance(crop, dict):
        console.print("[dim]Cancelled — no changes saved.[/dim]")
        return

    config.panic.crop = crop
    save_config(config)

    try:
        _process_panic_image(Path(src), crop)
        console.print("[green]✓ Panic image crop updated.[/green]")
    except Exception as exc:
        console.print("[yellow]⚠ Crop saved to config, but image re-processing failed:[/yellow]")
        console.print(f"  [dim]{exc}[/dim]")
        console.print("[dim]Run 'kdam panic set' again to force re-sync.[/dim]")


@panic.command(name="show")
def panic_show() -> None:
    """Show the configured panic image and backup status.

    \b
    Displays the path and existence of the configured panic image (if any),
    its crop settings, and the status of the automatic backup.png fallback.
    """
    config = load_config()
    console.print("\n[bold]Panic & Default Avatar Status[/bold]")
    if config.panic.path:
        p = Path(config.panic.path)
        exists = p.exists()
        crop_info = (
            f" (crop: x={config.panic.crop['x']} y={config.panic.crop['y']} s={config.panic.crop['size']})"
            if config.panic.crop
            else " (default crop)"
        )
        console.print(f"  Configured panic image : {config.panic.path}{crop_info}")
        console.print(
            f"  Status                 : {'[green]found[/green]' if exists else '[red]MISSING[/red]'}"
        )
    else:
        console.print("  Configured panic image : [dim]none (using fallback snapshot)[/dim]")

    b_exists = BACKUP_FILE.exists()
    console.print(f"  Initial backup snapshot: {BACKUP_FILE}")
    console.print(
        f"  Backup status          : "
        f"{'[green]found[/green]' if b_exists else '[yellow]not yet created (run kdam panic snapshot)[/yellow]'}"  # noqa: E501
    )
    console.print()
