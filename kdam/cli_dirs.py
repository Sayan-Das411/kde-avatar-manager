"""cli_dirs.py — dirs group and subcommands."""

from __future__ import annotations

from pathlib import Path

import click

from ._cli_shared import (
    ImageDB,
    _complete_configured_dirs,
    console,
    load_config,
    save_config,
)
from .config import Directory


@click.group()
def dirs() -> None:
    """Manage image source directories.

    \b
    HOW DIRECTORIES WORK IN KDAM:
    ─────────────────────────────────────────────────────────────────────────────
    Directories are the source roots where kdam looks for avatar images.
    When you add a directory, kdam can monitor it for new image files (JPG, PNG,
    WEBP, GIF, BMP, TIFF).

    Directories can be non-recursive (top-level only) or recursive (-r / --recursive),
    which automatically scans all sub-folders and nested collections.

    \b
    SUSPEND vs TOGGLE vs REMOVE:
      • dirs toggle <dir>   — Disables scanning for new images without touching
                              already-registered images.
      • dirs suspend <dir>  — Disables scanning AND temporarily pauses all images
                              originating from this directory so they won't rotate.
      • dirs resume <dir>   — Re-enables a suspended directory and restores all
                              its paused images back into the active rotation pool.
      • dirs remove <dir>   — Removes the directory from the scan list completely.
                              Already-configured images stay in your registry.

    \b
    SUB-COMMANDS:
      kdam dirs list              — Show all configured directories and scan status
      kdam dirs add <path> [-r]   — Add directory to scan (-r for recursive)
      kdam dirs remove <path>     — Stop scanning a directory
      kdam dirs toggle <path>     — Toggle directory scanning ON or OFF
      kdam dirs suspend <path>    — Pause directory + pause all its active images
      kdam dirs resume <path>     — Resume suspended directory + restore images
      kdam dirs edit <path>       — View or toggle recursive scanning flag

    \b
    EXAMPLES:
      kdam dirs add ~/Pictures/Avatars
      kdam dirs add ~/Wallpapers/Portraits -r
      kdam dirs list
      kdam dirs suspend ~/Wallpapers/Portraits
      kdam dirs resume ~/Wallpapers/Portraits
      kdam dirs edit ~/Pictures/Avatars --recursive
    """


@dirs.command(name="list")
def dirs_list() -> None:
    """List all configured image source directories.

    \b
    Shows each directory's path, whether it is enabled, and whether
    scanning descends into sub-directories (recursive mode).
    A ✓ means the directory is active; ✗ means it is missing on disk;
    'off' means it has been manually disabled with 'kdam dirs toggle'.
    """
    from rich.table import Table

    config = load_config()
    if not config.directories:
        console.print("[dim]No directories configured.[/dim]")
        console.print("  [dim]kdam dirs add /path/to/images[/dim]")
        return

    t = Table(show_header=True, header_style="bold cyan", border_style="dim")
    t.add_column("", width=2)
    t.add_column("Recursive", width=9)
    t.add_column("Path")

    for d in config.directories:
        exists = Path(d.path).expanduser().exists()
        icon = (
            "[green]✓[/green]"
            if (d.enabled and exists)
            else ("[red]✗[/red]" if not exists else "[dim]off[/dim]")
        )
        path_str = d.path if exists else f"[red]{d.path}[/red] [dim](missing)[/dim]"
        t.add_row(icon, "yes" if d.recursive else "no", path_str)

    console.print(t)


@dirs.command(name="add")
@click.argument("path", type=click.Path(file_okay=False, dir_okay=True, path_type=Path))
@click.option("--recursive", "-r", is_flag=True, help="Descend into subdirectories")
def dirs_add(path: Path | str, recursive: bool) -> None:
    """Add a directory to scan for images.

    \b
    kdam will include this directory when running 'kdam scan'.
    Use -r / --recursive to also descend into subdirectories.
    If the directory is already configured, its settings are updated instead.
    The directory does not need to exist yet — it will be skipped at scan time
    if it is missing.

    \b
    Examples:
      kdam dirs add ~/Pictures/Avatars
      kdam dirs add ~/Photos --recursive
    """
    config = load_config()
    abs_path = str(Path(path).expanduser().resolve())

    for d in config.directories:
        if str(Path(d.path).expanduser().resolve()) == abs_path:
            changed = False
            if d.recursive != recursive:
                d.recursive = recursive
                changed = True
            if not d.enabled:
                d.enabled = True
                changed = True
            if changed:
                save_config(config)
                rec = " (recursive)" if recursive else " (non-recursive)"
                console.print(f"[green]✓ Updated existing directory{rec}: {abs_path}[/green]")
                console.print("[dim]Run [bold]kdam scan[/bold] to discover images.[/dim]")
            else:
                rec_str = "recursive" if d.recursive else "non-recursive"
                console.print(f"[yellow]Directory is already configured ({rec_str}): {abs_path}[/yellow]")
            return

    if not Path(abs_path).exists():
        console.print(f"[yellow]Warning: directory does not exist yet: {abs_path}[/yellow]")

    config.directories.append(Directory(path=abs_path, recursive=recursive))
    save_config(config)
    rec = " (recursive)" if recursive else ""
    console.print(f"[green]✓ Added{rec}: {abs_path}[/green]")
    console.print("[dim]Run [bold]kdam scan[/bold] to discover images.[/dim]")


@dirs.command(name="remove")
@click.argument("path", shell_complete=_complete_configured_dirs)
def dirs_remove(path: str) -> None:
    """Remove a directory from the scan configuration.

    \b
    kdam stops scanning this directory for new images. Images that were
    already discovered and registered are NOT removed from the registry —
    use 'kdam remove --stale' or 'kdam remove <path>' to clean those up.
    """
    config = load_config()
    abs_path = str(Path(path).expanduser().resolve())
    before = len(config.directories)
    config.directories = [
        d for d in config.directories if str(Path(d.path).expanduser().resolve()) != abs_path
    ]
    if len(config.directories) < before:
        save_config(config)
        console.print(f"[green]✓ Removed: {abs_path}[/green]")
        console.print("[dim]Images from this directory remain in the registry until manually removed.[/dim]")
    else:
        console.print(f"[red]Not in config: {path}[/red]")


@dirs.command(name="toggle")
@click.argument("path", shell_complete=_complete_configured_dirs)
def dirs_toggle(path: str) -> None:
    """Toggle directory scanning ON or OFF.

    \b
    When disabled, kdam skips this directory during 'kdam scan'. Images
    already in the registry are unaffected — they stay in the rotation pool.

    \b
    To also pause the registered images from this directory, use
    'kdam dirs suspend' instead. That is the stronger operation.
    """
    config = load_config()
    abs_path = str(Path(path).expanduser().resolve())
    for d in config.directories:
        if str(Path(d.path).expanduser().resolve()) == abs_path:
            d.enabled = not d.enabled
            save_config(config)
            state = "[green]enabled[/green]" if d.enabled else "[dim]disabled[/dim]"
            console.print(f"{abs_path}: {state}")
            return
    console.print(f"[red]Not in config: {path}[/red]")


@dirs.command(name="edit")
@click.argument("path", shell_complete=_complete_configured_dirs)
@click.option(
    "--recursive/--no-recursive", default=None, help="Set or clear recursive scanning for this directory."
)
def dirs_edit(path: str, recursive: bool | None) -> None:
    """View or change directory scan settings.

    \b
    With no options, prints the current settings for the given directory.
    Use --recursive or --no-recursive to change whether subdirectories are scanned.
    Run 'kdam scan' afterwards to pick up any newly reachable images.

    \b
    Examples:
      kdam dirs edit ~/Pictures/Avatars              — view settings
      kdam dirs edit ~/Pictures/Avatars --recursive  — enable recursive scan
    """
    config = load_config()
    abs_path = str(Path(path).expanduser().resolve())
    for d in config.directories:
        if str(Path(d.path).expanduser().resolve()) == abs_path:
            if recursive is None:
                console.print(f"  Path      : {d.path}")
                console.print(f"  Recursive : {'yes' if d.recursive else 'no'}")
                console.print(f"  Enabled   : {'yes' if d.enabled else 'no'}")
                console.print("\n[dim]Use --recursive / --no-recursive to change.[/dim]")
                return
            d.recursive = recursive
            save_config(config)
            rec_str = "[green]recursive[/green]" if recursive else "[dim]non-recursive[/dim]"
            console.print(f"[green]✓[/green] {abs_path} → {rec_str}")
            console.print("[dim]Run [bold]kdam scan[/bold] to pick up newly reachable images.[/dim]")
            return
    console.print(f"[red]Not in config: {path}[/red]")
    console.print("[dim]Use [bold]kdam dirs add[/bold] to add this directory first.[/dim]")


@dirs.command(name="suspend")
@click.argument("path", shell_complete=_complete_configured_dirs)
def dirs_suspend(path: str) -> None:
    """Suspend a directory — disable scanning and pause all its registered images.

    Unlike 'dirs toggle', this also pauses images already in the registry so
    they are excluded from rotation until 'kdam dirs resume' is run.
    """
    config = load_config()
    db = ImageDB()
    abs_path = str(Path(path).expanduser().resolve())

    found = False
    for d in config.directories:
        if str(Path(d.path).expanduser().resolve()) == abs_path:
            d.enabled = False
            found = True
            break

    if not found:
        console.print(f"[red]Not in config: {path}[/red]")
        return

    paused = 0
    for img_path, entry in db.all().items():
        try:
            img_abs = str(Path(img_path).resolve())
            if img_abs.startswith(abs_path + "/") or img_abs == abs_path:
                if entry.enabled and not entry.suspended_by_dir:
                    entry.enabled = False
                    entry.suspended_by_dir = True
                    db.set(img_path, entry)
                    paused += 1
        except Exception:
            pass

    db.save()
    save_config(config)
    console.print(f"[yellow]Suspended:[/yellow] {abs_path}")
    console.print(
        f"[dim]{paused} image(s) paused. Run [bold]kdam dirs resume {path}[/bold] to restore.[/dim]"
    )


@dirs.command(name="resume")
@click.argument("path", shell_complete=_complete_configured_dirs)
def dirs_resume(path: str) -> None:
    """Resume a suspended directory — re-enable scanning and restore its images.

    \b
    Re-enables scanning of the directory and restores all images that were
    paused by 'kdam dirs suspend' back into the rotation pool.
    Images that were individually disabled with 'kdam disable-image' are
    not affected — only images paused by suspend are restored.
    """
    config = load_config()
    db = ImageDB()
    abs_path = str(Path(path).expanduser().resolve())

    found = False
    for d in config.directories:
        if str(Path(d.path).expanduser().resolve()) == abs_path:
            d.enabled = True
            found = True
            break

    if not found:
        console.print(f"[red]Not in config: {path}[/red]")
        return

    resumed = 0
    stale_skipped = 0
    for img_path, entry in db.all().items():
        try:
            img_abs = str(Path(img_path).resolve())
            if img_abs.startswith(abs_path + "/") or img_abs == abs_path:
                if entry.suspended_by_dir:
                    entry.suspended_by_dir = False
                    if Path(img_path).exists():
                        entry.enabled = True
                        resumed += 1
                    else:
                        # Source file deleted while suspended — clear flag but leave disabled
                        stale_skipped += 1
                    db.set(img_path, entry)
        except Exception:
            pass

    db.save()
    save_config(config)
    console.print(f"[green]Resumed:[/green] {abs_path}")
    console.print(f"[dim]{resumed} image(s) restored to rotation.[/dim]")
    if stale_skipped:
        console.print(
            f"[yellow]{stale_skipped} image(s) skipped — source file(s) missing "
            f"(run [bold]kdam remove --stale[/bold] to clean up).[/yellow]"
        )
