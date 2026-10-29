"""cli_backup.py — backup group and subcommands."""

from __future__ import annotations

from pathlib import Path

import click

from . import backup as backup_mod
from ._cli_shared import (
    console,
)


@click.group(name="backup")
def backup_cmd() -> None:
    """Create and restore portable backups of kdam data.

    \b
    PORTABILITY & MIGRATION:
    ─────────────────────────────────────────────────────────────────────────────
    Backups are compressed .tar.gz archives containing a machine-readable JSON
    manifest, your configuration, crop coordinate registry, and optionally the
    source image files themselves.

    \b
    ARCHIVE TYPES:
      • Full backup (default)     — Archives settings, crop data, and original images.
                                    Restores seamlessly on a new machine or OS reinstall.
      • Metadata-only (--no-images) — Lightweight archive containing only settings and
                                    crop regions. Useful when source images are synced
                                    via Git, Nextcloud, or Dropbox.
      • Config-only (--config-only) — Only rotation schedule and timer settings.

    \b
    SUB-COMMANDS:
      kdam backup create [path]           — Create backup (.tar.gz)
      kdam backup info <archive>          — Inspect an archive without restoring it
      kdam backup restore <archive> [-f]  — Restore data to this machine (-f to overwrite)
      kdam backup restore <archive> -d <dir> — Restore and extract images to custom folder

    \b
    EXAMPLES:
      kdam backup create
      kdam backup create ~/Backups/avatar_backup.tar.gz
      kdam backup create --no-images
      kdam backup info ~/Backups/avatar_backup.tar.gz
      kdam backup restore ~/Backups/avatar_backup.tar.gz
      kdam backup restore ~/Backups/avatar_backup.tar.gz -d ~/Pictures/RestoredAvatars
    """


@backup_cmd.command(name="create")
@click.argument("output_path", required=False, default=None)
@click.option(
    "--include-images/--no-images",
    default=True,
    help="Include original source image files in archive (default: yes).",
)
@click.option(
    "--config-only",
    is_flag=True,
    help="Include only config.json (settings & schedule), omitting image registry.",
)
def backup_create(output_path: str | None, include_images: bool, config_only: bool) -> None:
    """Create a portable backup archive (.tar.gz).

    \b
    By default saves config, crop metadata, and all source images.
    Use --no-images for a lightweight metadata-only archive.
    Use --config-only for just the rotation schedule settings.
    The archive can be restored on any machine with 'kdam backup restore'.

    \b
    Examples:
      kdam backup create                          — full backup to current dir
      kdam backup create ~/backups/kdam.tar.gz   — save to custom path
      kdam backup create --no-images             — metadata only (smaller)
      kdam backup create --config-only           — settings only
    """
    try:
        archive = backup_mod.create_backup(
            output_path, include_images=include_images, config_only=config_only
        )
        sz_kb = archive.stat().st_size / 1024
        sz_str = f"{sz_kb / 1024:.2f} MB" if sz_kb >= 1024 else f"{sz_kb:.1f} KB"
        console.print(f"[green]✓ Backup created successfully:[/green] {archive} ({sz_str})")
        if config_only:
            console.print("[dim]Includes configuration and schedule settings only.[/dim]")
        elif include_images:
            console.print("[dim]Includes configuration, crop metadata, and source images.[/dim]")
        else:
            console.print("[dim]Includes configuration and crop metadata only (no image files).[/dim]")
    except Exception as exc:
        console.print(f"[red]Failed to create backup: {exc}[/red]")


@backup_cmd.command(name="info")
@click.argument("archive_path", type=click.Path(exists=True, path_type=Path))
def backup_info(archive_path: Path) -> None:
    """Show details of a backup archive without restoring it.

    \b
    Prints creation date, origin machine, image count, included directories,
    and whether source images were bundled. Use this to check what a backup
    contains before committing to a restore.
    """
    try:
        info = backup_mod.inspect_backup(archive_path)
        sz_kb = info["archive_size"] / 1024
        sz_str = f"{sz_kb / 1024:.2f} MB" if sz_kb >= 1024 else f"{sz_kb:.1f} KB"
        console.print("\n[bold]kdam Backup Archive Info[/bold]")
        console.print(f"  File         : {archive_path.name} ({sz_str})")
        console.print(f"  Created at   : {info.get('created_at', 'unknown')[:19].replace('T', ' ')}")
        console.print(f"  Origin Home  : {info.get('original_home', 'unknown')}")
        console.print(f"  Backup Type  : {info.get('backup_type', 'unknown')}")
        console.print(f"  Images Saved : {'yes' if info.get('include_images') else 'no (metadata only)'}")
        console.print(f"  Image Count  : {info.get('image_count', 0)}")
        if info.get("directories"):
            console.print("  Directories  :")
            for d in info["directories"]:
                rec = " (recursive)" if d.get("recursive") else ""
                console.print(f"    - {d.get('path')}{rec}")
        if info.get("panic_path"):
            console.print(f"  Panic Image  : {info.get('panic_path')}")
        console.print()
    except Exception as exc:
        console.print(f"[red]Error inspecting backup: {exc}[/red]")


@backup_cmd.command(name="restore")
@click.argument("archive_path", type=click.Path(exists=True, path_type=Path))
@click.option("--images-dir", "-d", help="Custom directory to unpack bundled images into.")
@click.option("--force", "-f", is_flag=True, help="Overwrite current settings without asking.")
def backup_restore(archive_path: Path, images_dir: str | None, force: bool) -> None:
    """Restore configuration and images from a backup archive.

    \b
    Restores config.json, crop metadata (images.json), and if the backup
    includes source images, unpacks them to their original paths or to a
    custom directory specified by -d / --images-dir.

    \b
    Home directory remapping is applied automatically when restoring on a
    different machine. Use 'kdam backup info ARCHIVE' to preview first.

    \b
    Examples:
      kdam backup restore kdam-2024.tar.gz
      kdam backup restore kdam-2024.tar.gz -d ~/RestoredPhotos
      kdam backup restore kdam-2024.tar.gz --force
    """
    if not force:
        if not click.confirm("This will restore configuration and crop metadata. Continue?", default=True):
            console.print("[dim]Cancelled.[/dim]")
            return

    if images_dir:
        img_dir_path = Path(images_dir).expanduser()
        img_dir_path.mkdir(parents=True, exist_ok=True)
        images_dir = str(img_dir_path)

    try:
        ok, msg = backup_mod.restore_backup(archive_path, target_images_dir=images_dir)
        if ok:
            console.print(f"[green]✓ {msg}[/green]")
            console.print(
                "[dim]Run [bold]kdam status[/bold] or [bold]kdam list[/bold] to check restored state.[/dim]"
            )
        else:
            console.print(f"[red]Restore failed: {msg}[/red]")
    except Exception as exc:
        console.print(f"[red]Error restoring backup: {exc}[/red]")
