"""Click CLI — kdam root group and all leaf commands."""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import click
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import kde, rotator, scanner  # noqa: F401

# Import all shared helpers from _cli_shared so tests/submodules can find them
# on this module too (some tests monkeypatch cli.LOG_FILE etc.)
from ._cli_shared import (  # noqa: F401
    _SERVICE_FILE,
    _TIMER_FILE,
    SERVICE_NAME,
    _complete_command_name,
    _complete_config_key,
    _complete_config_value,
    _complete_configured_dirs,
    _complete_image_name,
    _do_panic_reset,
    _ensure_backup,
    _fmt_duration,
    _kdam_bin,
    _log_replace_path,
    _parse_duration,
    _process_panic_image,
    _resolve_image_path,
    _systemd,
    _timer_active,
    _write_systemd_files,
    console,
)
from .config import (
    BACKUP_FILE,
    CONFIG_DIR,
    LOG_FILE,
    LOGS_DIR,  # noqa: F401
    PROCESSED_DIR,  # noqa: F401
    _db_lock,
    load_config,
    save_config,
)
from .db import CropInfo, ImageDB, ImageEntry
from .editor import EDITOR_QUIT

# ── Root group ────────────────────────────────────────────────────────────────

@click.group(context_settings={"max_content_width": 120})
@click.version_option(package_name="kde-avatar-manager")
def cli() -> None:
    """KDAM — KDE Avatar Manager

    \b
    Auto-rotate your KDE Plasma user avatar on a configurable schedule.
    All data lives in ~/.kde-avatar-manager/ — independent of this project's
    source location, so the directory can be freely moved after installation.
    """


# ── help (rich) ───────────────────────────────────────────────────────────────

@cli.command(name="help")
@click.argument("command_name", nargs=-1, required=False, shell_complete=_complete_command_name)
def help_cmd(command_name: tuple) -> None:
    """Show a rich command reference, or help for a specific command.

    \b
    Examples:
      kdam help              — full reference
      kdam help rename       — help for 'rename'
      kdam help panic set    — help for 'panic set'
      kdam help dirs         — list all dirs sub-commands
    """
    if command_name:
        # Traverse the CLI group tree by the words given.
        # cmd_obj is typed Any because Click's MultiCommand/Group type hierarchy
        # doesn't expose get_command/list_commands on BaseCommand in the stubs.
        cmd_obj: Any = cli
        traversed: list[str] = []
        for part in command_name:
            if not hasattr(cmd_obj, "get_command"):
                console.print(f"[red]'{' '.join(traversed)}' has no sub-commands.[/red]")
                return
            child = cmd_obj.get_command(None, part)
            if child is None:
                console.print(f"[red]Unknown command: '{' '.join(list(command_name)[:list(command_name).index(part)+1])}'[/red]")
                valid = sorted(cmd_obj.list_commands(None)) if hasattr(cmd_obj, "list_commands") else []
                if valid:
                    console.print(f"[dim]Valid: {', '.join(valid)}[/dim]")
                return
            cmd_obj = child
            traversed.append(part)
        ctx = click.Context(cmd_obj, info_name=f"kdam {' '.join(command_name)}")
        click.echo(cmd_obj.get_help(ctx))
        return


    def _section(title: str, rows: list[tuple]) -> Panel:
        t = Table(
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            padding=(0, 1),
        )
        t.add_column("Command", style="bold green", no_wrap=True, min_width=32)
        t.add_column("Description")
        for cmd, desc in rows:
            t.add_row(Text(f"kdam {cmd}"), Text(desc))
        return Panel(t, title=f"[bold magenta]{title}[/bold magenta]", border_style="dim")


    console.print()
    console.print(Panel(
        Text.from_markup(
            "[bold white]KDAM[/bold white] — [cyan]KDE Avatar Manager[/cyan]\n\n"
            "Auto-rotate your KDE Plasma user avatar.\n"
            "Data dir: [dim]~/.kde-avatar-manager/[/dim]"
        ),
        border_style="magenta",
        padding=(1, 2),
    ))
    console.print()

    console.print(_section("Image Management", [
        ("scan",                      "Scan dirs; register new images, report stale"),
        ("list [-p]",                 "List all images with status (-p: full paths)"),
        ("add <paths...>",            "Add image(s) + open crop editor"),
        ("add <paths...> --no-editor","Register image(s) without opening crop editor"),
        ("remove <paths...>",         "Remove image(s) from registry"),
        ("remove --stale",            "Remove all missing/stale images from registry"),
        ("remove --all-unconfigured", "Remove all images that were never configured"),
        ("remove <path> --delete-file", "Remove + delete source file from disk"),
        ("edit <path>",               "Re-open crop editor for an image"),
        ("rename <old> <new>",        "Rename image file + migrate crop settings"),
        ("configure [paths...]",      "Open crop editor for specific image(s)"),
        ("configure-all",             "Open crop editor for every unconfigured image (skips panic image)"),
        ("unconfigure <paths...>",    "Reset crop config for image(s) (back to unconfigured)"),
        ("enable-image <path>",       "Re-enable a disabled image"),
        ("disable-image <path>",      "Disable image from rotation (keep in registry)"),
        ("weight <path> <n>",         "Set selection weight (for weighted-random order)"),
        ("set <path>",                "Apply a specific image immediately"),
    ]))
    console.print()

    console.print(_section("Rotation & Freeze", [
        ("next [-c N] [--dry-run]",    "Rotate immediately; -c N skips ahead N; --dry-run previews"),
        ("status",                     "Show full manager status (incl. next trigger time)"),
        ("log [-n N] [--all] [--desc] [--prune N]", "Rotation history past→present; --prune truncates the log"),
        ("freeze [dur] [-i <path>]",   "Freeze avatar indefinitely or for duration (e.g. 3d, 1w)"),
        ("unfreeze [-r]",              "Unfreeze avatar and resume auto-rotation"),
        ("enable",                     "Install + start systemd timer"),
        ("disable",                    "Stop + disable timer"),
        ("toggle",                     "Flip ON↔OFF or unfreeze — bind to a hotkey"),
    ]))
    console.print()

    console.print(_section("Schedule Config", [
        ("config set rotation.mode <mode>",        "daily | interval | times | weekly | monthly"),
        ("config set rotation.interval 6h",        "Interval: e.g. 6h, 45m, 12d 13h 52m (auto-reloads timer)"),
        ("config set rotation.start_hour 0",       "Anchor hour 0-23 for clock-aligned intervals (00:00, 06:00...)"),
        ("config set rotation.times '08:30, 14:00'", "Specific clock times in a day for 'times' mode"),
        ("config set rotation.weekday sun",        "Day of week for 'weekly' mode (mon..sun)"),
        ("config set rotation.order <order>",      "random | sequential | weighted | lru | deck"),
        ("config set rotation.lru_pool_size <K>",  "default (dynamic 10-50, 15%) | custom integer K"),
        ("config show",                            "Print full config as JSON"),
    ]))
    console.print()

    console.print(_section("Panic / Default Avatar", [
        ("panic reset",                     "Revert to panic image + disable auto-rotation"),
        ("panic set <path> [--no-editor]",  "Set panic image + crop (--no-editor: skip crop)"),
        ("panic set --current",             "Snapshot and set current KDE avatar as panic"),
        ("panic edit",                      "Edit crop for panic image (also re-syncs backup.png)"),
        ("panic snapshot",                  "Snapshot active KDE avatar to backup.png"),
        ("panic show",                      "Show configured panic image and backup info"),
    ]))
    console.print()

    console.print(_section("Directories", [
        ("dirs list",                         "Show configured directories"),
        ("dirs add <path>",                   "Add a directory (non-recursive)"),
        ("dirs add <path> -r",                "Add a directory (recursive)"),
        ("dirs remove <path>",                "Remove a directory from config"),
        ("dirs toggle <path>",                "Enable / disable directory scanning only"),
        ("dirs suspend <path>",               "Suspend dir + pause all its registered images"),
        ("dirs resume <path>",                "Resume suspended dir + restore its paused images"),
        ("dirs edit <path>",                  "Show current settings for a directory"),
        ("dirs edit <path> --recursive",      "Enable recursive scanning"),
        ("dirs edit <path> --no-recursive",   "Disable recursive scanning"),
    ]))
    console.print()

    console.print(_section("Maintenance", [
        ("resync",              "Relink registry entries after moving files in a file manager"),
    ]))
    console.print()

    console.print(_section("Backup & Migration", [
        ("backup create [out]",               "Create portable full backup (.tar.gz)"),
        ("backup create --no-images",         "Backup settings & crop metadata only"),
        ("backup create --config-only",       "Backup schedule & settings only"),
        ("backup info <file>",                "Show details of a backup archive"),
        ("backup restore <file> [-f]",        "Restore config, metadata & images (-f: force)"),
        ("backup restore <file> -d <dir>",    "Restore and extract images to custom dir"),
    ]))
    console.print()

    console.print(_section("Lifecycle & Uninstall", [
        ("help [command]",                     "Full reference, or help for a specific command"),
        ("completion [--shell bash|zsh|fish]", "Show shell Tab-completion setup instructions"),
        ("uninstall [--purge] [-y]",           "Stop timers and remove kdam (-y: skip prompt)"),
    ]))
    console.print()



    # Status icon legend
    legend = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", border_style="dim", padding=(0, 2))
    legend.add_column("Icon", style="bold", width=4, justify="center")
    legend.add_column("Meaning")
    for icon, meaning in [
        ("[green]✓[/green]",     "Active — configured and in the rotation pool"),
        ("[dim]○[/dim]",         "Disabled — configured but excluded from rotation"),
        ("[yellow]✗[/yellow]",   "Unconfigured — registered but no crop set yet"),
        ("[red]⚠[/red]",        "Missing — source file deleted from disk"),
        ("[magenta]P[/magenta]", "Panic — reserved as safe fallback, excluded from rotation"),
    ]:
        legend.add_row(icon, meaning)
    console.print(Panel(legend, title="[bold magenta]Status Icons (kdam list / kdam log)[/bold magenta]", border_style="dim"))
    console.print()

    # Crop editor controls
    ctrl = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", border_style="dim", padding=(0, 1))
    ctrl.add_column("Action", style="bold green", min_width=25)
    ctrl.add_column("Effect")
    for action, effect in [
        ("Drag inside square",    "Move crop region"),
        ("Drag ◼ bottom-right",   "Resize crop square"),
        ("Scroll wheel",          "Resize (±5%)"),
        ("Arrow keys",            "Nudge 1 px"),
        ("Shift + Arrow keys",    "Nudge 10 px"),
        ("+ / −",                 "Resize"),
        ("Enter / Return",        "Save crop"),
        ("Esc",                   "Skip image"),
    ]:
        ctrl.add_row(action, effect)
    console.print(Panel(ctrl, title="[bold magenta]Crop Editor Controls[/bold magenta]", border_style="dim"))

    console.print()
    console.print(Panel(
        "[dim]Tip:[/dim] Bind [bold green]kdam toggle[/bold green] to a keyboard shortcut in\n"
        "[dim]System Settings → Shortcuts → Custom Shortcuts[/dim]\n"
        "[dim]Tip:[/dim] [bold green]kdam help <command>[/bold green] shows detailed help for any command\n"
        "[dim]       e.g.  kdam help rename   |   kdam help panic set   |   kdam help dirs[/dim]",
        border_style="dim",
        padding=(0, 2),
    ))
    console.print()


# ── scan ──────────────────────────────────────────────────────────────────────

@cli.command()
def scan() -> None:
    """Scan configured directories for new or removed images.

    \b
    Newly found images are added to the registry as unconfigured.
    Images whose source files have been deleted are reported as stale.
    Run 'kdam configure-all' afterwards to set crops for new images.
    The panic image is reported separately and skipped for configure-all.
    """
    config = load_config()
    db = ImageDB()

    if not config.directories:
        console.print("[yellow]No directories configured.[/yellow]")
        console.print("  [dim]kdam dirs add /path/to/images[/dim]")
        return

    new_imgs, stale_paths = scanner.scan(config, db)
    db.save()

    panic_path_r = str(Path(config.panic.path).resolve()) if config.panic.path else None
    new_normal = [p for p in new_imgs if panic_path_r is None or str(Path(p).resolve()) != panic_path_r]
    new_panic  = [p for p in new_imgs if panic_path_r and str(Path(p).resolve()) == panic_path_r]

    if new_imgs:
        console.print(f"[green]✓ {len(new_imgs)} new image(s) found:[/green]")
        for p in new_normal:
            console.print(f"  [dim]{p}[/dim]")
        for p in new_panic:
            console.print(f"  [dim]{p}[/dim] [magenta](panic image — already configured)[/magenta]")
        if new_normal:
            console.print("\n[cyan]Tip:[/cyan] Run [bold]kdam configure-all[/bold] to set crops in one go.")
    else:
        console.print("[dim]No new images found.[/dim]")

    if stale_paths:
        console.print(f"\n[yellow]⚠  {len(stale_paths)} stale entry(ies) — source file missing:[/yellow]")
        for p in stale_paths:
            console.print(f"  [dim]{p}[/dim]")
        console.print("[dim]Use [bold]kdam remove <path>[/bold] to clean them up.[/dim]")


# ── list ──────────────────────────────────────────────────────────────────────

@cli.command(name="list")
@click.option("-p", "--paths", is_flag=True, help="Always show full directory paths.")
def list_cmd(paths: bool) -> None:
    """List all managed images, status, crops, weights, and last-used times.

    \b
    COLUMNS DISPLAYED:
    ─────────────────────────────────────────────────────────────────────────────
      Icon      — Status indicator for each image (see below).
      Image     — Filename or relative path (use -p / --paths for absolute paths).
                  Colliding filenames in different folders automatically show parent dirs.
      Crop      — Crop square coordinates (x, y, size) in source image pixels.
      Wt        — Selection weight for weighted-random mode (default: 1.0).
      Last used — Date and time when the avatar was last rotated to.

    \b
    STATUS ICONS:
      ✓ (green)    — Active: configured with a crop and active in the rotation pool.
      ► (green)    — Current: the avatar currently applied to your KDE desktop.
      ○ (dim)      — Disabled: configured but temporarily disabled ('kdam disable-image').
      ✗ (yellow)   — Unconfigured: registered but no crop set yet ('kdam configure').
      ⚠ (red)      — Missing: source image file was deleted or moved from disk.
      P (magenta)  — Panic: designated as safe/default avatar, isolated from pool.

    \b
    EXAMPLES:
      kdam list       — Standard compact view
      kdam list -p    — Display full absolute filesystem paths for all images
    """
    from collections import Counter

    db = ImageDB()
    config = load_config()
    all_imgs = db.all()

    if not all_imgs:
        console.print("[dim]No images registered. Run [bold]kdam scan[/bold] or [bold]kdam add <path>[/bold].[/dim]")
        return

    panic_path_r = str(Path(config.panic.path).resolve()) if config.panic.path else None

    # Count duplicate filenames to detect naming collisions across different directories
    name_counts = Counter(Path(p).name for p in all_imgs.keys())

    t = Table(show_header=True, header_style="bold cyan", border_style="dim")
    t.add_column("", width=2, justify="center")
    t.add_column("Image", no_wrap=True)
    t.add_column("Crop", width=20)
    t.add_column("Wt", width=4, justify="right")
    t.add_column("Last used", width=17)

    # Resolve the currently active image path for highlighting
    current_path_r: str | None = None
    if config.state.last_image_path:
        try:
            current_path_r = str(Path(config.state.last_image_path).resolve())
        except Exception:
            pass

    for path, entry in sorted(all_imgs.items()):
        p_obj = Path(path)
        exists = p_obj.exists()
        is_panic = panic_path_r and str(p_obj.resolve()) == panic_path_r
        is_current = bool(current_path_r and str(p_obj.resolve()) == current_path_r)

        if is_panic:
            icon, name_style = "P", "magenta"   # panic image — not in rotation pool
        elif not exists:
            icon, name_style = "⚠", "red"
        elif not entry.configured:
            icon, name_style = "✗", "yellow"
        elif entry.enabled:
            icon, name_style = "✓", "green"
        else:
            icon, name_style = "○", "dim"

        # Currently-in-use image gets a ► icon and bold name
        if is_current:
            icon = "►"

        # If --paths flag is set, show full path.
        # Otherwise, if there is a filename collision (multiple files with same name in different folders),
        # display parent_dir/filename or ~/.../filename to distinguish them clearly.
        # Otherwise, display just filename.
        if paths:
            display_name = str(p_obj)
        elif name_counts[p_obj.name] > 1:
            try:
                home = Path.home()
                if p_obj.is_relative_to(home):
                    display_name = f"~/{p_obj.relative_to(home)}"
                else:
                    display_name = f"{p_obj.parent.name}/{p_obj.name}"
            except Exception:
                display_name = f"{p_obj.parent.name}/{p_obj.name}"
        else:
            display_name = p_obj.name

        if is_current:
            name = f"[bold][{name_style}]{display_name}[/{name_style}][/bold]"
        else:
            name = f"[{name_style}]{display_name}[/{name_style}]"

        if not exists:
            name += " [dim](missing)[/dim]"
        elif is_panic:
            name += " [dim](panic)[/dim]"
        elif is_current:
            name += " [dim](current)[/dim]"

        # Show crop: for panic image use config.panic.crop; for others use entry.crop
        crop_str = ""
        if is_panic and config.panic.crop:
            c = config.panic.crop
            crop_str = f"x={c['x']} y={c['y']} s={c['size']}"
        elif entry.crop:
            ci: CropInfo = entry.crop
            crop_str = f"x={ci.x} y={ci.y} s={ci.size}"

        last = entry.last_used[:16].replace("T", " ") if entry.last_used else "—"
        if is_panic:
            weight_str = "[dim]—[/dim]"
        elif entry.configured:
            weight_str = f"{entry.weight:.1f}"
        else:
            weight_str = "—"

        if is_current:
            t.add_row(f"[bold][{name_style}]{icon}[/{name_style}][/bold]", name, crop_str, weight_str, last)
        else:
            t.add_row(f"[{name_style}]{icon}[/{name_style}]", name, crop_str, weight_str, last)

    console.print(t)

    # Counts: panic image is neither "active" nor "unconfigured" — it's its own category
    active_n = sum(
        1 for p, e in all_imgs.items()
        if e.configured and e.enabled
        and (panic_path_r is None or str(Path(p).resolve()) != panic_path_r)
    )
    uncfg_n = sum(
        1 for p, e in all_imgs.items()
        if not e.configured
        and (panic_path_r is None or str(Path(p).resolve()) != panic_path_r)
    )
    panic_n = 1 if (panic_path_r and panic_path_r in {str(Path(p).resolve()) for p in all_imgs}) else 0
    stale_n = sum(1 for p in all_imgs if not Path(p).exists())

    summary = (
        f"\n[dim]Total {len(all_imgs)} | "
        f"[green]Active {active_n}[/green] | "
        f"[yellow]Unconfigured {uncfg_n}[/yellow] | "
        f"[red]Missing {stale_n}[/red]"
    )
    if panic_n:
        summary += " | [magenta]Panic 1[/magenta]"
    if current_path_r:
        summary += " | [bold]► current[/bold]"
    summary += "[/dim]"
    console.print(summary)



# ── add ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--no-editor", is_flag=True, help="Register without opening crop editor")
def add(paths: tuple[Path, ...], no_editor: bool) -> None:
    """Add image(s) to manager (opens crop editor)."""
    from .editor import open_crop_editor

    db = ImageDB()
    for i, path in enumerate(paths, 1):
        abs_path = str(path.resolve())

        if db.has(abs_path):
            console.print(f"[yellow]Already registered: {path.name}[/yellow]")
            if not click.confirm(f"Re-configure {path.name}?", default=False):
                continue

        entry = db.get(abs_path) or ImageEntry()

        if no_editor:
            db.set(abs_path, ImageEntry(configured=False, enabled=False))
            db.save()
            console.print(f"[yellow]Registered (unconfigured): {path.name}[/yellow]")
            console.print(f"  [dim]kdam configure {path.name}[/dim]")
            continue

        if len(paths) > 1:
            console.print(f"\n[cyan][{i}/{len(paths)}] Opening crop editor for: {path.name} …[/cyan]")
        else:
            console.print("[cyan]Opening crop editor …[/cyan]")

        initial = entry.crop.__dict__ if entry.crop else None
        crop = open_crop_editor(abs_path, initial)

        if not isinstance(crop, dict):
            console.print(f"[dim]Skipped: {path.name}[/dim]")
            continue

        entry.configured = True
        entry.enabled = True
        entry.crop = CropInfo(**crop)
        db.set(abs_path, entry)
        db.save()
        console.print(f"[green]✓ Added and configured: {path.name}[/green]")


# ── remove ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("paths", nargs=-1, required=False, shell_complete=_complete_image_name)
@click.option("--stale", is_flag=True, help="Remove all stale/missing images from registry.")
@click.option("--delete-file", is_flag=True, help="Also delete the source image from disk")
@click.option("--all-unconfigured", "all_uncfg", is_flag=True,
              help="Remove all images that have never been configured (crop not set).")
def remove(paths: tuple[str, ...], stale: bool, delete_file: bool, all_uncfg: bool) -> None:
    """Remove image(s) from the avatar registry and optionally delete from disk.

    \b
    BEHAVIOR:
    ─────────────────────────────────────────────────────────────────────────────
    Removes the specified image(s) or batch-sweeps entries from ~/.kde-avatar-manager/images.json.
    By default, only the registry record (and custom crop data) is removed — the source
    image file on disk is preserved unless --delete-file is explicitly passed.

    \b
    FLAGS & MODES:
      --stale             — Clean up broken registry entries whose files no longer exist on disk.
      --all-unconfigured  — Remove all images that have never been cropped (prompts for confirmation).
      --delete-file       — Permanently deletes the underlying file from disk as well. Use with care.

    \b
    ARGUMENTS:
      PATHS               — One or more image names, partial paths, or full absolute paths.
                            Tab-completion works automatically for registered names.

    \b
    EXAMPLES:
      kdam remove portrait.jpg                   — Remove single image from registry (keeps file)
      kdam remove img1.png img2.jpg              — Remove multiple images
      kdam remove --stale                        — Sweep all deleted/missing files from registry
      kdam remove --all-unconfigured             — Clear all uncropped images discovered by scan
      kdam remove old_pic.jpg --delete-file      — Remove from registry AND permanently delete file
    """
    db = ImageDB()

    if stale:
        stale_dict = db.stale()
        if not stale_dict:
            console.print("[green]No stale/missing images found in registry.[/green]")
            return
        for abs_path in list(stale_dict.keys()):
            db.remove(abs_path)
            console.print(f"[green]✓ Removed stale entry:[/green] {Path(abs_path).name}")
        db.save()
        return

    if all_uncfg:
        uncfg = db.unconfigured()
        if not uncfg:
            console.print("[green]No unconfigured images found.[/green]")
            return
        console.print(f"[yellow]Found {len(uncfg)} unconfigured image(s):[/yellow]")
        for p in uncfg:
            console.print(f"  [dim]{p}[/dim]")
        if not click.confirm(f"Remove {len(uncfg)} unconfigured image(s) from registry?"):
            return
        for p in list(uncfg.keys()):
            db.remove(p)
            if delete_file:
                try:
                    src = Path(p)
                    if src.exists():
                        src.unlink()
                        console.print(f"[green]✓ Removed from registry and deleted from disk:[/green] {src.name}")
                    else:
                        console.print(f"[green]✓ Removed from registry:[/green] {Path(p).name} [dim](file already missing)[/dim]")
                except OSError as exc:
                    console.print(f"[yellow]✓ Removed from registry, but could not delete file: {exc}[/yellow]")
            else:
                console.print(f"[green]✓ Removed:[/green] {Path(p).name}")
        db.save()
        return

    if not paths:
        console.print("[yellow]Provide image path(s) to remove, or use --stale to clean missing files.[/yellow]")
        console.print("[dim]Example: kdam remove photo1.jpg photo2.jpg[/dim]")
        console.print("[dim]Example: kdam remove --stale[/dim]")
        return

    for path in paths:
        abs_path = _resolve_image_path(path, db) or str(Path(path).expanduser().resolve())

        if not db.has(abs_path):
            console.print(f"[red]Not in registry: {path}[/red]")
            continue

        db.remove(abs_path)

        if delete_file and Path(abs_path).exists():
            Path(abs_path).unlink()
            console.print(f"[green]✓ Removed from registry and deleted from disk: {Path(abs_path).name}[/green]")
        else:
            console.print(f"[green]✓ Removed from registry: {Path(abs_path).name}[/green]")

    db.save()


# ── unconfigure ───────────────────────────────────────────────────────────────

@cli.command(name="unconfigure")
@click.argument("paths", nargs=-1, required=True, shell_complete=_complete_image_name)
def unconfigure(paths: tuple[str, ...]) -> None:
    """Reset the crop configuration for one or more images.

    \b
    The image stays in the registry but its crop, enabled status, and
    processed cache are all cleared — it returns to the 'unconfigured' (✗)
    state as if it had just been scanned for the first time.

    \b
    Use 'kdam configure <path>' or 'kdam configure-all' to re-configure.
    To remove an image from the registry entirely, use 'kdam remove'.
    """
    db = ImageDB()
    for path in paths:
        abs_path = _resolve_image_path(path, db) or str(Path(path).expanduser().resolve())
        entry = db.get(abs_path)
        if not entry:
            console.print(f"[red]Not in registry: {path}[/red]")
            continue

        entry.configured = False
        entry.enabled = False
        entry.crop = None
        entry.suspended_by_dir = False  # prevent contradictory state if dir is later resumed
        db.set(abs_path, entry)
        console.print(f"[yellow]✓ Reset crop config: {Path(abs_path).name} (now unconfigured)[/yellow]")
    db.save()


# ── rename ────────────────────────────────────────────────────────────────────

@cli.command(name="rename")
@click.argument("old_path", shell_complete=_complete_image_name)
@click.argument("new_name")
def rename_cmd(old_path: str, new_name: str) -> None:
    """Rename an image file and update all references in the registry.

    \b
    OLD_PATH  — image to rename (name, partial path, or full path)
    NEW_NAME  — new filename or destination path (e.g. 'portrait.jpg' or '~/Pictures/portrait.jpg')

    Also updates: crop config, panic/state references, and the rotation log.
    If this image is the panic image, backup.png is re-synced automatically.
    """
    db = ImageDB()
    config = load_config()

    resolved_old = _resolve_image_path(old_path, db)
    if not resolved_old:
        console.print(f"[red]Image not found in registry or disk: {old_path}[/red]")
        return

    src_p = Path(resolved_old)
    if not src_p.exists():
        console.print(f"[red]Cannot rename missing file on disk: {src_p}[/red]")
        console.print("[dim]Use kdam remove --stale to clean up deleted files.[/dim]")
        return

    # Determine destination path
    new_p_input = Path(new_name).expanduser()
    if new_p_input.is_absolute() or len(new_p_input.parts) > 1:
        dst_p = new_p_input.resolve()
        if not dst_p.suffix and src_p.suffix:
            dst_p = dst_p.with_suffix(src_p.suffix)
    else:
        dest_filename = new_name
        if not Path(dest_filename).suffix and src_p.suffix:
            dest_filename += src_p.suffix
        dst_p = (src_p.parent / dest_filename).resolve()

    if dst_p == src_p:
        console.print("[yellow]Old name and new name are identical.[/yellow]")
        return

    # Ensure parent dir exists
    if not dst_p.parent.exists():
        dst_p.parent.mkdir(parents=True, exist_ok=True)

    # 1. Move/rename on disk — atomically claim dst_p with O_EXCL to close TOCTOU
    #    race between the dst_p.exists() check above and the actual move.
    try:
        fd = os.open(str(dst_p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        console.print(f"[red]Destination file already exists: {dst_p}[/red]")
        return
    try:
        shutil.move(str(src_p), str(dst_p))
    except Exception as exc:
        dst_p.unlink(missing_ok=True)  # clean up placeholder
        console.print(f"[red]Failed to rename file on disk: {exc}[/red]")
        return

    # 2. Update ImageDB
    entry = db.get(str(src_p))
    if entry:
        db.remove(str(src_p))
        db.set(str(dst_p), entry)
        db.save()
    else:
        db.set(str(dst_p), ImageEntry(configured=False, enabled=False))
        db.save()

    # 3. Update config references
    config_modified = False
    if config.panic.path and Path(config.panic.path).resolve() == src_p:
        config.panic.path = str(dst_p)
        config_modified = True
        if config.panic.crop:
            try:
                _process_panic_image(dst_p, config.panic.crop)
            except Exception:
                pass

    if config.state.last_image_path and Path(config.state.last_image_path).resolve() == src_p:
        config.state.last_image_path = str(dst_p)
        config_modified = True

    if config_modified:
        save_config(config)

    # Update rotation log — line-aware replace avoids substring collisions
    _log_replace_path(str(src_p), str(dst_p))

    console.print(f"[green]✓ Renamed on disk:[/green] {src_p.name} → [bold cyan]{dst_p.name}[/bold cyan]")
    console.print("[green]✓ Registry entry and crop settings updated.[/green]")
    if config_modified:
        console.print("[dim]✓ Updated panic/state references.[/dim]")


# ── edit ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("path", shell_complete=_complete_image_name)
def edit(path: str) -> None:
    """Re-open the crop editor for an already-registered image.

    \b
    Use this to adjust the crop region after the initial 'kdam add' or
    'kdam configure' session. The previous crop is loaded as the starting
    position so you can nudge it rather than starting over.
    After saving, the new crop is applied on the next rotation or 'kdam set'.

    \b
    PATH  — image name, partial path, or full path (Tab-completion works)

    \b
    To edit the crop of the panic image use: kdam panic edit
    """
    from .editor import open_crop_editor

    db = ImageDB()
    abs_path = _resolve_image_path(path, db) or str(Path(path).resolve())
    p = Path(abs_path)

    if not p.exists():
        console.print(f"[red]File not found: {path}[/red]")
        return

    entry = db.get(abs_path) or ImageEntry()
    initial = entry.crop.__dict__ if entry.crop else None

    console.print("[cyan]Opening crop editor …[/cyan]")
    crop = open_crop_editor(abs_path, initial)

    if not isinstance(crop, dict):
        console.print("[dim]Cancelled — no changes saved.[/dim]")
        return

    entry.configured = True
    entry.enabled = True
    entry.crop = CropInfo(**crop)
    db.set(abs_path, entry)
    db.save()
    console.print(f"[green]✓ Crop updated: {p.name}[/green]")


# ── configure ─────────────────────────────────────────────────────────────────

@cli.command(name="configure")
@click.argument("paths", nargs=-1, required=False, shell_complete=_complete_image_name)
def configure(paths: tuple[str, ...]) -> None:
    """Configure crop for one or more images (opens the crop editor).

    \b
    With no arguments, opens the editor for every unconfigured image in order.
    With paths, opens the editor for each specified image only.
    Skips the panic image (configure it with 'kdam panic set' or 'kdam panic edit').
    After cropping, the image is enabled for rotation automatically.
    """
    from .editor import open_crop_editor

    db = ImageDB()
    if paths:
        target_paths = []
        for p in paths:
            resolved = _resolve_image_path(p, db)
            if not resolved or not Path(resolved).exists():
                console.print(f"[red]Image not found: {p}[/red]")
                continue
            target_paths.append(resolved)

        if not target_paths:
            return

        for i, path in enumerate(target_paths, 1):
            if len(target_paths) > 1:
                console.print(f"\n[bold][{i}/{len(target_paths)}][/bold] {Path(path).name}")
            entry = db.get(path) or ImageEntry()
            crop = open_crop_editor(path, entry.crop.__dict__ if entry.crop else None)
            if not isinstance(crop, dict):
                console.print(f"  [dim]Skipped {Path(path).name}.[/dim]")
                continue
            entry.configured = True
            entry.enabled = True
            entry.crop = CropInfo(**crop)
            db.set(path, entry)
            db.save()
            console.print(f"  [green]✓ Configured {Path(path).name}.[/green]")
    else:
        config = load_config()
        panic_path_r = str(Path(config.panic.path).resolve()) if config.panic.path else None
        pending = [
            (p, e) for p, e in db.unconfigured().items()
            if Path(p).exists() and (panic_path_r is None or str(Path(p).resolve()) != panic_path_r)
        ]
        if not pending:
            console.print("[green]All images are already configured![/green]")
            console.print("[dim]To re-configure an image, run: kdam configure <filename-or-path>[/dim]")
            return

        console.print(f"[yellow]{len(pending)} unconfigured image(s) available:[/yellow]")
        for i, (p, _) in enumerate(pending, 1):
            console.print(f"  [bold cyan]{i}.[/bold cyan] {Path(p).name}")
        console.print("\n[dim]Run [bold]kdam configure-all[/bold] to configure all of them,[/dim]")
        console.print("[dim]or [bold]kdam configure <name>[/bold] to configure specific ones.[/dim]")


# ── configure-all ─────────────────────────────────────────────────────────────

@cli.command(name="configure-all")
def configure_all() -> None:
    """Open crop editor for every unconfigured image in sequence.

    \b
    Buttons in the editor:
      Save Crop    — save and proceed to the next image
      Reset Frame  — re-centre the crop box to the image centre
      Skip         — skip this image (stays unconfigured)
      Quit         — stop configure-all immediately
    Closing the window has the same effect as Skip.
    The panic image is excluded (configure it with 'kdam panic set').
    """
    from .editor import open_crop_editor

    db = ImageDB()
    config = load_config()
    panic_path_r = str(Path(config.panic.path).resolve()) if config.panic.path else None
    pending = [
        (p, e) for p, e in db.unconfigured().items()
        if Path(p).exists() and (panic_path_r is None or str(Path(p).resolve()) != panic_path_r)
    ]

    if not pending:
        console.print("[green]All images are already configured![/green]")
        return

    console.print(f"[cyan]{len(pending)} image(s) to configure.[/cyan]")
    console.print("[dim]  Save Crop → next   |   Skip → skip   |   Quit → stop[/dim]\n")
    configured_count = 0

    for i, (path, entry) in enumerate(pending, 1):
        console.print(f"[bold][{i}/{len(pending)}][/bold] {Path(path).name}")
        crop = open_crop_editor(path, entry.crop.__dict__ if entry.crop else None)
        if crop is EDITOR_QUIT:
            console.print("  [dim]Quit — stopping.[/dim]")
            break
        if not isinstance(crop, dict):
            console.print("  [dim]Skipped.[/dim]")
            continue
        entry.configured = True
        entry.enabled = True
        entry.crop = CropInfo(**crop)
        db.set(path, entry)
        db.save()
        console.print("  [green]✓ Configured.[/green]")
        configured_count += 1

    console.print(f"\n[green]Done — {configured_count}/{len(pending)} configured.[/green]")


def _ensure_image_configured(path: str, db: ImageDB) -> bool:
    """Ensure an image is configured with a crop, opening editor if needed. Returns True on success."""
    entry = db.get(path)
    if not entry or not entry.configured:
        from .editor import open_crop_editor
        console.print("[cyan]Image not configured — opening crop editor …[/cyan]")
        crop = open_crop_editor(path, entry.crop.__dict__ if (entry and entry.crop) else None)
        if not isinstance(crop, dict):
            console.print("[dim]Cancelled.[/dim]")
            return False
        entry = entry or ImageEntry()
        entry.configured = True
        entry.enabled = True
        entry.crop = CropInfo(**crop)
        db.set(path, entry)
        db.save()
    return True


# ── set ───────────────────────────────────────────────────────────────────────

@cli.command(name="set")
@click.argument("path", shell_complete=_complete_image_name)
def set_cmd(path: str) -> None:
    """Apply a specific image as your avatar right now.

    \b
    The image is processed (cropped + resized to 256×256) and applied via
    D-Bus immediately. If the image has no crop set yet, the crop editor
    opens first so you can configure it before applying.

    \b
    This does not affect the auto-rotation schedule — the timer keeps
    running and will rotate to the next image at its usual trigger time.
    To also freeze on this image, use: kdam freeze --image <path>
    """
    db = ImageDB()
    abs_path = _resolve_image_path(path, db) or str(Path(path).resolve())
    p = Path(abs_path)

    if not p.exists():
        console.print(f"[red]File not found: {path}[/red]")
        return

    if not _ensure_image_configured(abs_path, db):
        return

    ok = rotator.apply_specific(abs_path, db, load_config())
    if ok:
        console.print(f"[green]✓ Avatar set to: {p.name}[/green]")
    else:
        console.print("[red]Failed to set avatar. Check logs.[/red]")


# ── freeze & unfreeze ─────────────────────────────────────────────────────────

@cli.command()
@click.argument("duration", required=False, default=None)
@click.option("--image", "-i", help="Specific image to apply and freeze on.", shell_complete=_complete_image_name)
def freeze(duration: str | None, image: str | None) -> None:
    """Freeze the avatar — stop auto-rotation indefinitely or for a set time.

    \b
    While frozen, 'kdam next' and the systemd timer both skip rotation.
    The freeze lifts automatically when the duration expires, or manually
    with 'kdam unfreeze'.

    \b
    Duration format: combine any of  s  m  h  d  w
      30m   → 30 minutes         3h    → 3 hours
      2d    → 2 days             1w    → 1 week
      1h30m → 1 hour 30 minutes

    \b
    Examples:
      kdam freeze             — freeze indefinitely
      kdam freeze 3d          — freeze for 3 days
      kdam freeze 1w -i safe  — switch to 'safe' image and freeze for 1 week
    """
    config = load_config()
    db = ImageDB()

    if image:
        resolved = _resolve_image_path(image, db)
        if not resolved or not Path(resolved).exists():
            console.print(f"[red]Image not found: {image}[/red]")
            return

        if not _ensure_image_configured(resolved, db):
            return

        ok = rotator.apply_specific(resolved, db, config)
        if not ok:
            console.print("[red]Failed to apply image.[/red]")
            return


    until_dt: datetime | None = None
    if duration:
        try:
            delta = _parse_duration(duration)
            until_dt = datetime.now() + delta
            config.state.frozen_until = until_dt.isoformat()
        except ValueError as err:
            console.print(f"[red]{err}[/red]")
            return
    else:
        config.state.frozen_until = None

    config.state.frozen = True
    save_config(config)

    if until_dt:
        dur_str = _fmt_duration(until_dt - datetime.now())
        until_str = until_dt.strftime("%Y-%m-%d %H:%M")
        console.print(f"[green]✓ Avatar frozen for {dur_str} (until {until_str}).[/green]")
    else:
        console.print("[green]✓ Avatar frozen indefinitely.[/green]")
    console.print("[dim]Run [bold]kdam unfreeze[/bold] anytime to resume auto-rotation.[/dim]")


@cli.command()
@click.option("--rotate", "-r", is_flag=True, help="Rotate to next avatar immediately upon unfreezing.")
def unfreeze(rotate: bool) -> None:
    """Unfreeze the avatar and resume auto-rotation.

    \b
    The current avatar image is left as-is after unfreezing — the next
    rotation happens at the timer's scheduled time. Use -r / --rotate to
    immediately jump to the next image in the pool right now.

    \b
    If the freeze period has already expired it is cleared silently; the
    avatar was never changed while frozen, so nothing looks different.
    """
    config = load_config()
    db = ImageDB()

    if not config.state.frozen:
        console.print("[dim]Avatar is not frozen.[/dim]")
        if rotate:
            ok = rotator.rotate(config, db)
            if ok:
                console.print("[green]✓ Avatar rotated.[/green]")
        return

    config.state.frozen = False
    config.state.frozen_until = None
    save_config(config)
    console.print("[green]✓ Avatar unfrozen — auto-rotation resumed.[/green]")

    if rotate:
        ok = rotator.rotate(config, db)
        if ok:
            console.print("[green]✓ Avatar rotated to next image.[/green]")


# ── next ──────────────────────────────────────────────────────────────────────

@cli.command(name="next")
@click.option("--quiet", is_flag=True, help="Suppress output (used by systemd).")
@click.option("--count", "-c", default=1, show_default=True,
              help="Rotate N times in a row (useful to skip ahead several images).")
@click.option("--dry-run", is_flag=True,
              help="Show which image would be selected next without applying it.")
def next_cmd(quiet: bool, count: int, dry_run: bool) -> None:
    """Rotate to the next avatar immediately.

    \b
    Picks the next image per the configured order (random/sequential/weighted/lru/deck),
    processes it (crop + resize) if needed, and applies it via D-Bus.
    Does nothing if rotation is disabled or the avatar is frozen.
    Use --count N to skip ahead N images at once.
    Use --dry-run to preview the next image without actually rotating.
    Use 'kdam set <path>' to jump to a specific image right now.
    --quiet suppresses all output (used internally by the systemd service).
    """
    config = load_config()
    db = ImageDB()

    if dry_run:
        nxt = rotator.get_next_image(config, db)
        if nxt:
            console.print(f"[cyan]Would rotate to:[/cyan] [bold]{Path(nxt).name}[/bold]  [dim]{nxt}[/dim]")
        else:
            console.print("[yellow]No configured images available for rotation.[/yellow]")
        return

    if not config.state.enabled:
        if not quiet:
            console.print("[yellow]Rotation is disabled. Run [bold]kdam enable[/bold] to resume.[/yellow]")
        return

    # Freeze check: auto-unfreeze if the period has expired
    if config.state.frozen:
        if config.state.frozen_until:
            until = datetime.fromisoformat(config.state.frozen_until)
            if datetime.now() < until:
                if not quiet:
                    remaining = until - datetime.now()
                    console.print(
                        f"[yellow]Avatar is frozen for {_fmt_duration(remaining)} more. "
                        f"Run [bold]kdam unfreeze[/bold] to resume early.[/yellow]"
                    )
                return
            else:
                # Period expired — auto-unfreeze silently
                config.state.frozen = False
                config.state.frozen_until = None
                save_config(config)
                if not quiet:
                    console.print("[dim]Freeze period ended — resuming rotation.[/dim]")
        else:
            if not quiet:
                console.print(
                    "[yellow]Avatar is frozen indefinitely. "
                    "Run [bold]kdam unfreeze[/bold] to resume.[/yellow]"
                )
            return

    rotated = 0
    pool_empty = False
    for _ in range(max(1, count)):
        ok = rotator.rotate(config, db, quiet=quiet)
        if ok is True:
            rotated += 1
        elif ok is None:
            # Pool is genuinely empty — no point retrying
            pool_empty = True
            break
        # ok is False: specific image failed (missing/unreadable) — continue to next attempt

    if not quiet:
        if pool_empty and rotated == 0:
            console.print("[yellow]No configured images available for rotation. Run [bold]kdam list[/bold].[/yellow]")
        elif rotated == 0:
            pass  # error already printed/notified by rotate()
        elif count == 1:
            console.print("[green]✓ Avatar rotated.[/green]")
        else:
            console.print(f"[green]✓ Rotated {rotated}/{count} times.[/green]")


# ── status ────────────────────────────────────────────────────────────────────

@cli.command()
def status() -> None:
    """Show a full summary of the avatar manager's current state.

    \b
    Displays: rotation ON/OFF, systemd timer active/inactive, freeze status
    and remaining time, rotation mode and order, active image count, current
    avatar, last rotation time, next scheduled trigger, and panic image status.
    """
    config = load_config()
    db = ImageDB()
    all_imgs = db.all()

    panic_path = str(Path(config.panic.path).resolve()) if config.panic.path else None
    active_rotation_pool = [
        p for p, e in all_imgs.items()
        if e.configured and e.enabled and (panic_path is None or str(Path(p).resolve()) != panic_path)
    ]
    active_n = len(active_rotation_pool)
    uncfg_n  = sum(
        1 for p, e in all_imgs.items()
        if not e.configured and (panic_path is None or str(Path(p).resolve()) != panic_path)
    )
    timer_on = _timer_active()
    rot_on   = config.state.enabled

    console.print("\n[bold]KDAM — Avatar Manager Status[/bold]")
    console.print(f"  Rotation  : {'[green]ON[/green]' if rot_on else '[red]OFF[/red]'}")
    console.print(f"  Timer     : {'[green]active[/green]' if timer_on else '[dim]inactive[/dim]'}")

    if config.state.frozen:
        if config.state.frozen_until:
            until = datetime.fromisoformat(config.state.frozen_until)
            remaining = until - datetime.now()
            if remaining.total_seconds() > 0:
                console.print(f"  Frozen    : [yellow]yes — {_fmt_duration(remaining)} remaining[/yellow]")
            else:
                console.print("  Frozen    : [dim]period expired (resumes on next rotation)[/dim]")
        else:
            console.print("  Frozen    : [yellow]yes — indefinitely[/yellow]")
    else:
        console.print("  Frozen    : [dim]no[/dim]")

    mode_val = config.rotation.mode.lower()
    if mode_val == "interval":
        interval_display = config.rotation.interval or f"{config.rotation.interval_hours}h"
        mode_str = f"interval ({interval_display})"
    elif mode_val == "weekly":
        mode_str = f"weekly ({config.rotation.weekday})"
    elif mode_val == "times":
        times_display = ", ".join(config.rotation.times) if config.rotation.times else "00:00"
        mode_str = f"times ({times_display})"
    else:
        mode_str = config.rotation.mode
    console.print(f"  Mode      : {mode_str}")
    order_val = config.rotation.order
    if order_val in ("lru", "least-recent"):
        from .rotator import _get_lru_pool_size
        k = _get_lru_pool_size(active_n, config)
        pool_type = f"custom={config.rotation.lru_pool_size}" if config.rotation.lru_pool_size != "default" else "default"
        console.print(f"  Order     : lru (Least Recently Used, pool: {k}/{active_n} images, {pool_type})")
    elif order_val in ("deck", "shuffle-bag"):
        deck_remaining = len([p for p in config.state.deck if p in all_imgs and all_imgs[p].enabled and all_imgs[p].configured])
        console.print(f"  Order     : deck (Shuffle-bag cycle: {deck_remaining}/{active_n} remaining before reshuffle)")
    else:
        console.print(f"  Order     : {order_val}")

    from ._cli_shared import _build_timer_directives
    _, schedule_desc = _build_timer_directives(config)
    console.print(f"  Schedule  : {schedule_desc}")

    console.print(f"  Images    : [green]{active_n} active for rotation[/green] / {len(all_imgs)} total"
                  + (f" [yellow]({uncfg_n} unconfigured)[/yellow]" if uncfg_n else ""))

    suspended_n = sum(1 for e in all_imgs.values() if e.suspended_by_dir)
    if suspended_n:
        console.print(f"  Suspended : [dim]{suspended_n} image(s) paused by dirs suspend[/dim]")

    # Orphaned: registered but parent dir not under any configured directory
    configured_dirs = config.directories

    def _is_under_configured_dir(p_str: str) -> bool:
        img_p = Path(p_str).resolve()
        for d in configured_dirs:
            dp = Path(d.path).expanduser().resolve()
            if d.recursive:
                try:
                    img_p.relative_to(dp)
                    return True
                except ValueError:
                    pass
            else:
                if img_p.parent == dp:
                    return True
        return False

    orphaned_n = sum(
        1 for p in all_imgs
        if (panic_path is None or str(Path(p).resolve()) != panic_path)
        and not _is_under_configured_dir(p)
    )
    if orphaned_n:
        console.print(f"  Orphaned  : [yellow]{orphaned_n} image(s) not under any configured directory[/yellow]")
        console.print("             [dim](run 'kdam dirs add <dir>' or 'kdam remove --stale')[/dim]")

    current_display = Path(config.state.last_image_path).name if config.state.last_image_path else "[dim]unknown[/dim]"
    actual_avatar = kde.get_current_avatar()
    if actual_avatar:
        try:
            actual_p = Path(actual_avatar).resolve()
            last_p = Path(config.state.last_image_path).resolve() if config.state.last_image_path else None
            panic_p = Path(config.panic.path).resolve() if config.panic.path else None
            backup_p = BACKUP_FILE.resolve()

            if last_p and actual_p == last_p:
                console.print(f"  Current   : {current_display}")
            elif panic_p and actual_p == panic_p:
                console.print(f"  Current   : {panic_p.name} [dim](panic image active)[/dim]")
            elif actual_p == backup_p:
                console.print(f"  Current   : backup.png [dim](fallback snapshot active)[/dim]")
            else:
                console.print(f"  Current   : {current_display} [dim](AccountsService: {actual_p.name})[/dim]")
        except Exception:
            console.print(f"  Current   : {current_display}")
    else:
        console.print(f"  Current   : {current_display}")

    if config.state.last_rotation:
        console.print(f"  Last run  : {config.state.last_rotation[:16].replace('T', ' ')}")

    # Next timer trigger
    if timer_on:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "show", f"{SERVICE_NAME}.timer",
                 "--property=NextElapseUSecRealtime,NextElapseUSecMonotonic"],
                capture_output=True, text=True
            )
            props = dict(line.split("=", 1) for line in result.stdout.strip().splitlines() if "=" in line)
            raw_rt = props.get("NextElapseUSecRealtime", "").strip()
            next_dt = None
            if raw_rt and raw_rt != "0":
                try:
                    next_dt = datetime.fromtimestamp(int(raw_rt) / 1_000_000)
                except ValueError:
                    parts = raw_rt.split()
                    if len(parts) >= 3:
                        next_dt = datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S")

            if next_dt:
                delta = next_dt - datetime.now()
                if delta.total_seconds() > 0:
                    total_s = int(delta.total_seconds())
                    h, rem = divmod(total_s, 3600)
                    m = rem // 60
                    left_str = f"{h}h {m}m" if h else f"{m}m"
                    console.print(f"  Next run  : {next_dt.strftime('%Y-%m-%d %H:%M:%S')}  [dim](in {left_str})[/dim]")
            else:
                raw_mono = props.get("NextElapseUSecMonotonic", "").strip()
                if raw_mono and raw_mono != "0":
                    import time
                    now_mono_usec = int(time.clock_gettime(time.CLOCK_MONOTONIC) * 1_000_000)
                    target_mono_usec = int(raw_mono)
                    diff_sec = (target_mono_usec - now_mono_usec) / 1_000_000
                    if diff_sec > 0:
                        next_dt = datetime.now() + timedelta(seconds=diff_sec)
                        h, rem = divmod(int(diff_sec), 3600)
                        m = rem // 60
                        left_str = f"{h}h {m}m" if h else f"{m}m"
                        console.print(f"  Next run  : {next_dt.strftime('%Y-%m-%d %H:%M:%S')}  [dim](in {left_str})[/dim]")
        except Exception:
            pass

    panic_src = config.panic.path or f"{BACKUP_FILE} (auto-snapshot)"
    panic_exists = Path(config.panic.path).exists() if config.panic.path else BACKUP_FILE.exists()
    crop_info = " [custom crop]" if config.panic.crop else ""
    console.print(f"  Panic img : {panic_src}{crop_info} [{'[green]✓[/green]' if panic_exists else '[red]missing[/red]'}]")
    console.print()


# ── log ───────────────────────────────────────────────────────────────────────

@cli.command(name="log")
@click.option("-n", "count", default=20, show_default=True, help="Number of entries to show.")
@click.option("--all", "show_all", is_flag=True, help="Show all entries.")
@click.option("--desc", is_flag=True, help="Show newest first (default: oldest first, past→present).")
@click.option("--prune", "prune_n", default=None, type=int,
              help="Truncate log to the most recent N entries and exit (no table shown).")
def log_cmd(count: int, show_all: bool, desc: bool, prune_n: int | None) -> None:
    """Show rotation history, oldest-first (past → present).

    \b
    Default shows the last N entries in chronological order (oldest at top,
    newest at bottom). Use --desc to flip to newest-first. Use --all to see
    the full log.
    """
    if not LOG_FILE.exists():
        console.print("[dim]No rotation history yet.[/dim]")
        return

    lines = [line.strip() for line in LOG_FILE.read_text().splitlines() if line.strip()]
    if not lines:
        console.print("[dim]Rotation log is empty.[/dim]")
        return

    # ── Prune mode ────────────────────────────────────────────────────────────
    if prune_n is not None:
        if prune_n < 1:
            console.print("[red]--prune N requires N ≥ 1.[/red]")
            return
        kept = lines[-prune_n:]
        removed = len(lines) - len(kept)
        if removed <= 0:
            console.print(f"[dim]Log has {len(lines)} entries — nothing to prune (already ≤ {prune_n}).[/dim]")
            return
        with _db_lock():
            LOG_FILE.write_text("\n".join(kept) + "\n")
        console.print(f"[green]✓ Pruned {removed} old entries — kept {len(kept)}.[/green]")
        return

    config = load_config()
    db = ImageDB()
    all_imgs = db.all()

    # Resolve all registered paths for quick lookup
    registered_paths = {str(Path(p).resolve()) for p in all_imgs}
    panic_path_r = str(Path(config.panic.path).resolve()) if config.panic.path else None
    current_path = str(Path(config.state.last_image_path).resolve()) if config.state.last_image_path else None

    # Parse entries (raw order = chronological, oldest first)
    entries = []
    for line in lines:
        if " | " not in line:
            continue
        ts_str, path_str = line.split(" | ", 1)
        path_str = path_str.strip()
        entries.append((ts_str.strip(), path_str))

    # Default: show last N in chronological order (oldest→newest)
    # --desc: show newest N first
    if show_all:
        display = entries if not desc else list(reversed(entries))
    else:
        # Slice from the end to get the most recent N, then maybe reverse
        recent = entries[-count:]
        display = recent if not desc else list(reversed(recent))

    # Pre-compute which display index is the "current" marker.
    # We want the chronologically LATEST entry that matches current_path.
    # - oldest-first (default): keep updating so the last match wins (= most-recent entry)
    # - newest-first (--desc): take only the FIRST match (already the most-recent entry)
    current_display_idx = None
    if current_path:
        for i, (_, path_str) in enumerate(display):
            p = Path(path_str)
            p_resolved = str(p.resolve()) if p.is_absolute() else None
            if p_resolved == current_path:
                if desc and current_display_idx is not None:
                    break   # already found the most-recent in newest-first order
                current_display_idx = i   # keep updating — last match = most recent in chrono order

    t = Table(show_header=True, header_style="bold cyan", border_style="dim")
    t.add_column("", width=2, justify="center")
    t.add_column("Time", width=19, no_wrap=True)
    t.add_column("Image", ratio=1)

    need_legend = False
    for row_idx, (ts_str, path_str) in enumerate(display):
        p = Path(path_str)
        p_resolved = str(p.resolve()) if p.is_absolute() else None
        exists = p.exists() if p.is_absolute() else False
        in_registry = p_resolved in registered_paths if p_resolved else False
        is_panic = p_resolved == panic_path_r if p_resolved else False
        is_current = (row_idx == current_display_idx)

        # Determine icon and style
        if is_panic:
            icon, style = "P", "magenta"
        elif not p.is_absolute():
            icon, style = "?", "dim"
        elif exists and in_registry:
            icon, style = "✓", "green"
        elif exists and not in_registry:
            icon, style = "○", "dim"
            need_legend = True
        elif not exists:
            icon, style = "✗", "red"
            need_legend = True

        # Dim the parent path, highlight the filename
        if p.is_absolute():
            img_display = f"[dim]{str(p.parent)}/[/dim][{style}]{p.name}[/{style}]"
        else:
            img_display = f"[dim]{path_str}[/dim]"

        if is_current:
            img_display += " [bold dim]← current[/bold dim]"

        try:
            dt = datetime.fromisoformat(ts_str)
            time_display = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            time_display = ts_str

        t.add_row(f"[{style}]{icon}[/{style}]", time_display, img_display)

    console.print(t)

    total = len(entries)
    showing = len(display)
    if not show_all and total > count:
        console.print(f"[dim]Showing {showing} of {total} entries. Use --all or -n N to see more.[/dim]")

    if need_legend:
        console.print("[dim]  ✓ active   ○ removed from registry   ✗ missing   P panic[/dim]")




# ── resync ────────────────────────────────────────────────────────────────────

@cli.command(name="resync")
def resync() -> None:
    """Resync registry after files were moved between directories.

    \b
    Use this after restructuring your image directories in a file manager:
    kdam will find stale registry entries and match them to their new
    locations by filename. Files renamed (not just moved) will be reported
    as missing and must be handled manually.
    """
    config = load_config()
    db = ImageDB()

    stale = {p: e for p, e in db.all().items() if not Path(p).exists()}
    if not stale:
        console.print("[green]Registry is up to date — nothing to resync.[/green]")
        return

    console.print(f"[yellow]{len(stale)} stale entry(ies) to resync...[/yellow]\n")

    # Scan all configured directories for current files
    scan_results: dict[str, list[str]] = {}  # basename -> [abs_paths...]
    for d in config.directories:
        dir_path = Path(d.path).expanduser()
        if not dir_path.exists():
            continue
        pattern = "**/*" if d.recursive else "*"
        for f in dir_path.glob(pattern):
            if f.is_file() and f.suffix.lower() in scanner.SUPPORTED_EXTENSIONS:
                name = f.name
                full = str(f.resolve())
                scan_results.setdefault(name, []).append(full)

    auto_updated = 0
    ambiguous = 0
    missing = 0

    panic_path_r = str(Path(config.panic.path).resolve()) if config.panic.path else None
    existing_keys = {str(Path(k).resolve()) for k in db.all()}

    for old_path, entry in stale.items():
        name = Path(old_path).name
        matches = scan_results.get(name, [])
        # Exclude matches that are already registered under a different key
        matches = [m for m in matches if m not in existing_keys]

        if len(matches) == 0:
            console.print(f"  [red]✗[/red] [dim]{name}[/dim] — not found in configured directories")
            missing += 1

        elif len(matches) == 1:
            new_path = matches[0]
            # Move DB entry
            db.remove(old_path)
            db.set(new_path, entry)
            existing_keys.discard(str(Path(old_path).resolve()))
            existing_keys.add(str(Path(new_path).resolve()))
            # Update config references
            if panic_path_r and str(Path(old_path).resolve()) == panic_path_r:
                config.panic.path = new_path
            if config.state.last_image_path and str(Path(config.state.last_image_path).resolve()) == str(Path(old_path).resolve()):
                config.state.last_image_path = new_path
            # Update log — use line-aware replace to avoid substring collisions
            _log_replace_path(old_path, new_path)
            console.print(f"  [green]✓[/green] [dim]{name}[/dim] → [cyan]{new_path}[/cyan]")
            auto_updated += 1

        else:
            console.print(f"  [yellow]?[/yellow] [dim]{name}[/dim] — multiple matches:")
            for i, m in enumerate(matches, 1):
                console.print(f"    [bold cyan]{i}.[/bold cyan] {m}")
            try:
                choice = click.prompt(f"    Pick 1–{len(matches)}, or 0 to skip", type=int)
            except click.Abort:
                choice = 0
            if 1 <= choice <= len(matches):
                new_path = matches[choice - 1]
                db.remove(old_path)
                db.set(new_path, entry)
                existing_keys.discard(str(Path(old_path).resolve()))
                existing_keys.add(str(Path(new_path).resolve()))
                if panic_path_r and str(Path(old_path).resolve()) == panic_path_r:
                    config.panic.path = new_path
                if config.state.last_image_path and str(Path(config.state.last_image_path).resolve()) == str(Path(old_path).resolve()):
                    config.state.last_image_path = new_path
                # Update log — use line-aware replace to avoid substring collisions
                _log_replace_path(old_path, new_path)
                console.print(f"  [green]✓[/green] Linked to {new_path}")
                auto_updated += 1
            else:
                console.print("  [dim]Skipped.[/dim]")
                ambiguous += 1

    db.save()
    save_config(config)

    console.print()
    console.print(f"[green]Done.[/green] Updated: {auto_updated}  |  "
                  f"[yellow]Ambiguous/skipped: {ambiguous}[/yellow]  |  "
                  f"[red]Missing: {missing}[/red]")
    if missing:
        console.print("[dim]Missing images can be cleaned up with: kdam remove --stale[/dim]")


# ── enable-image / disable-image ──────────────────────────────────────────────

@cli.command(name="enable-image")
@click.argument("path", shell_complete=_complete_image_name)
def enable_image(path: str) -> None:
    """Re-enable a previously disabled image for rotation.

    \b
    Image must already be configured (crop set). Use 'kdam configure <path>'
    first if the image was never cropped. Images disabled by 'kdam dirs suspend'
    are better re-enabled with 'kdam dirs resume'.
    """
    db = ImageDB()
    abs_path = _resolve_image_path(path, db) or str(Path(path).resolve())
    entry = db.get(abs_path)
    if not entry:
        console.print(f"[red]Not in registry: {path}[/red]")
        return
    if not entry.configured:
        console.print(f"[yellow]Run [bold]kdam configure {path}[/bold] first to set its crop.[/yellow]")
        return
    entry.enabled = True
    entry.suspended_by_dir = False   # explicit enable overrides any dir-level suspension
    db.set(abs_path, entry)
    db.save()
    console.print(f"[green]✓ Enabled: {Path(abs_path).name}[/green]")


@cli.command(name="disable-image")
@click.argument("path", shell_complete=_complete_image_name)
def disable_image(path: str) -> None:
    """Disable an image from rotation (keeps it in the registry).

    \b
    The image remains registered with its crop — use 'kdam enable-image' to
    restore it later. To remove it entirely, use 'kdam remove'.
    To pause a whole directory at once, use 'kdam dirs suspend'.
    """
    db = ImageDB()
    abs_path = _resolve_image_path(path, db) or str(Path(path).resolve())
    entry = db.get(abs_path)
    if not entry:
        console.print(f"[red]Not in registry: {path}[/red]")
        return
    entry.enabled = False
    db.set(abs_path, entry)
    db.save()
    console.print(f"[dim]Disabled: {Path(abs_path).name}[/dim]")


# ── weight ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("path", shell_complete=_complete_image_name)
@click.argument("value", type=float)
def weight(path: str, value: float) -> None:
    """Set the selection weight for an image (weighted-random mode only).

    \b
    VALUE must be > 0. Higher weight = more likely to be selected.
    Example: weight 2.0 means twice as likely as an image with weight 1.0.
    Only has effect when 'config set rotation.order weighted' is active.
    Use 'kdam list' to see current weights.
    """
    if value <= 0:
        console.print("[red]Weight must be > 0.[/red]")
        return
    db = ImageDB()
    abs_path = _resolve_image_path(path, db) or str(Path(path).resolve())
    entry = db.get(abs_path)
    if not entry:
        console.print(f"[red]Not in registry: {path}[/red]")
        return
    entry.weight = value
    db.set(abs_path, entry)
    db.save()
    console.print(f"[green]✓ Weight → {value} for {Path(abs_path).name}[/green]")


# ── enable / disable / toggle / uninstall ─────────────────────────────────────

@cli.command()
def enable() -> None:
    """Install and start the daily rotation timer.

    \b
    Writes the systemd .timer and .service files, enables and starts them.
    Safe to re-run after changing rotation.mode or moving the project.
    Use 'kdam status' to see the next scheduled trigger time.
    """
    config = load_config()
    _ensure_backup()
    _write_systemd_files(config)
    _systemd("daemon-reload")
    r = _systemd("enable", "--now", f"{SERVICE_NAME}.timer")
    if r.returncode != 0:
        console.print(f"[red]systemctl error:[/red] {r.stderr.strip()}")
        return
    config.state.enabled = True
    save_config(config)
    console.print("[green]✓ Timer enabled.[/green]")
    info = _systemd("status", f"{SERVICE_NAME}.timer")
    for line in info.stdout.splitlines():
        if "rigger" in line:
            console.print(f"  [dim]{line.strip()}[/dim]")


@cli.command()
def disable() -> None:
    """Stop and remove the rotation timer (does not delete any data).

    \b
    Stops and disables the systemd timer. The avatar stays on whatever it
    currently is. Re-run 'kdam enable' to start again.
    """
    config = load_config()
    _systemd("disable", "--now", f"{SERVICE_NAME}.timer")
    config.state.enabled = False
    save_config(config)
    console.print("[yellow]Timer disabled — avatar will not auto-rotate.[/yellow]")


@cli.command()
def toggle() -> None:
    """Toggle rotation ON↔OFF, or unfreeze if frozen — ideal for a hotkey.

    \b
    • If frozen:   unfreezes, resumes auto-rotation (does not rotate immediately).
    • If ON:       applies the panic image and stops the timer (rotation OFF).
    • If OFF:      starts the timer and rotates to the next image (rotation ON).

    Bind this to a keyboard shortcut in System Settings → Shortcuts → Custom
    Shortcuts for a one-key toggle during screen-sharing or privacy moments.
    """
    config = load_config()

    if config.state.frozen:
        config.state.frozen = False
        config.state.frozen_until = None
        save_config(config)
        console.print("[green]Unfrozen — auto-rotation resumed.[/green]")
        return

    if config.state.enabled:
        # Turn OFF
        ok = _do_panic_reset(config)
        if ok:
            config.state.enabled = False
            save_config(config)
            _systemd("stop", f"{SERVICE_NAME}.timer")
            console.print("[yellow]Rotation OFF — panic image applied.[/yellow]")
    else:
        # Turn ON
        _ensure_backup()
        _write_systemd_files(config)
        _systemd("daemon-reload")
        _systemd("enable", "--now", f"{SERVICE_NAME}.timer")
        config.state.enabled = True
        save_config(config)
        db = ImageDB()
        result: bool | None = rotator.rotate(config, db)
        if result is True:
            console.print("[green]Rotation ON — avatar rotated.[/green]")
        else:
            console.print("[green]Rotation ON.[/green] [yellow](No configured images to rotate yet.)[/yellow]")


@cli.command()
@click.option("--purge", is_flag=True, help="Also delete all configuration, crops, and image registries.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
def uninstall(purge: bool, yes: bool) -> None:
    """Uninstall kdam and stop the systemd rotation timer.

    \b
    Stops and disables the systemd timer, removes the ~/.local/bin/kdam wrapper,
    removes the virtual environment, and cleans shell completion from RC files.
    Your images and directories are NOT touched.

    \b
    Use --purge to also delete everything in ~/.kde-avatar-manager/ (config,
    image registry, crop data, logs). This is irreversible.

    \b
    Examples:
      kdam uninstall           — remove kdam, keep your data
      kdam uninstall --purge   — complete wipe including data
      kdam uninstall -y        — skip confirmation prompt
    """
    if not yes:
        msg = "Are you sure you want to uninstall kdam?"
        if purge:
            msg += " (ALL data in ~/.kde-avatar-manager will be permanently deleted!)"
        if not click.confirm(msg, default=False):
            console.print("[dim]Cancelled.[/dim]")
            return

    console.print("[cyan]Stopping and disabling systemd timer …[/cyan]")
    _systemd("disable", "--now", f"{SERVICE_NAME}.timer")
    _SERVICE_FILE.unlink(missing_ok=True)
    _TIMER_FILE.unlink(missing_ok=True)
    _systemd("daemon-reload")

    bin_path = Path.home() / ".local" / "bin" / "kdam"
    if bin_path.exists():
        bin_path.unlink()
        console.print("[dim]Removed ~/.local/bin/kdam wrapper.[/dim]")

    if purge:
        if CONFIG_DIR.exists():
            shutil.rmtree(CONFIG_DIR)
            console.print("[dim]Purged ~/.kde-avatar-manager/ directory.[/dim]")
    else:
        # Keep config/images.json but clean venv
        venv_dir = CONFIG_DIR / "venv"
        if venv_dir.exists():
            shutil.rmtree(venv_dir)
            console.print("[dim]Removed virtual environment.[/dim]")

    console.print("[green]✓ kdam uninstalled successfully.[/green]")
    if not purge:
        console.print("[dim]Your settings and crop metadata in ~/.kde-avatar-manager/ were preserved.[/dim]")
        console.print("[dim]Use kdam uninstall --purge if you want a complete wipe.[/dim]")

    # Remove shell completion lines from RC files
    _completion_cleanup()


def _completion_cleanup() -> None:
    """Remove kdam shell completion from RC files, static scripts, and XDG dirs.

    Mirrors the cleanup performed by uninstall.sh:
    - Strips ``_KDAM_COMPLETE`` and ``# kdam`` lines from .bashrc/.zshrc/config.fish
    - Removes static completion script files (completion.bash/zsh/fish)
    - Removes the XDG bash-completion file (~/.local/share/bash-completion/completions/kdam)
    """
    rc_files = [
        Path.home() / ".bashrc",
        Path.home() / ".zshrc",
        Path.home() / ".config" / "fish" / "config.fish",
    ]
    skip_markers = ("_KDAM_COMPLETE", "# kdam shell completion", "# kdam",
                    "completion.bash", "completion.zsh", "completion.fish")
    for rc in rc_files:
        if not rc.exists():
            continue
        try:
            lines = rc.read_text().splitlines(keepends=True)
            new_lines = [ln for ln in lines if not any(m in ln for m in skip_markers)]
            if len(new_lines) != len(lines):
                # Strip excessive trailing blank lines
                while new_lines and not new_lines[-1].strip():
                    new_lines.pop()
                content = "".join(new_lines)
                if content and not content.endswith("\n"):
                    content += "\n"
                rc.write_text(content)
                console.print(f"[dim]Removed completion lines from {rc}.[/dim]")
        except Exception:
            pass

    # Remove static completion script files written by install.sh
    comp_dir = CONFIG_DIR
    for fname in ("completion.bash", "completion.zsh", "completion.fish"):
        p = comp_dir / fname
        if p.exists():
            p.unlink()

    # Remove XDG bash-completion file (installed by install.sh to enable system-wide completion)
    xdg_comp = Path.home() / ".local" / "share" / "bash-completion" / "completions" / "kdam"
    if xdg_comp.exists():
        xdg_comp.unlink()
        console.print("[dim]Removed XDG bash-completion file.[/dim]")


# ── completion setup ──────────────────────────────────────────────────────────

@cli.command(name="completion")
@click.option("--shell", "shell_name", default=None,
              type=click.Choice(["bash", "zsh", "fish"], case_sensitive=False),
              help="Shell type (auto-detected if omitted).")
def completion_cmd(shell_name: str | None) -> None:
    """Print shell completion setup instructions.

    Run the printed command once (or add it to your shell RC file) to enable
    Tab completion for all kdam image-name arguments.
    """
    import os
    if not shell_name:
        detected = Path(os.environ.get("SHELL", "")).name
        if detected in ("bash", "zsh", "fish"):
            shell_name = detected
        else:
            shell_name = "bash"

    if shell_name == "bash":
        eval_line = 'eval "$(_KDAM_COMPLETE=bash_source kdam)"'
        rc = "~/.bashrc"
    elif shell_name == "zsh":
        eval_line = 'eval "$(_KDAM_COMPLETE=zsh_source kdam)"'
        rc = "~/.zshrc"
    else:
        eval_line = "_KDAM_COMPLETE=fish_source kdam | source"
        rc = "~/.config/fish/config.fish"

    console.print(f"\n[bold]Shell completion setup ({shell_name})[/bold]\n")
    console.print(f"Add this line to [cyan]{rc}[/cyan]:\n")
    console.print(f"  [green]{eval_line}[/green]\n")
    console.print("Then restart your terminal or run:")
    if shell_name == "fish":
        console.print(f"  [dim]{eval_line}[/dim]\n")
    else:
        console.print(f"  [dim]source {rc}[/dim]\n")
    console.print("[dim]Tab completion will then work for all image name arguments.[/dim]")
    console.print("[dim]Example: kdam rename by-aus<Tab>  →  by-aus-ome98_DTQG04cktJe_1.jpg[/dim]\n")



# ── Subgroup registrations ───────────────────────────────────────────────────

from .cli_backup import backup_cmd  # noqa: E402
from .cli_config import config_cmd  # noqa: E402
from .cli_dirs import dirs  # noqa: E402
from .cli_panic import panic  # noqa: E402

cli.add_command(panic)
cli.add_command(dirs)
cli.add_command(config_cmd)
cli.add_command(backup_cmd)
