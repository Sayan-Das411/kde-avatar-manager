# kde-avatar-manager (`kdam`)

[![CI](https://github.com/Sayan-Das411/kde-avatar-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/Sayan-Das411/kde-avatar-manager/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform: KDE Plasma](https://img.shields.io/badge/platform-KDE%20Plasma-blue.svg)](https://kde.org/plasma-desktop/)

Auto-rotate your **KDE Plasma / KDE Neon** user avatar on a configurable schedule, with a built-in crop editor, directory scanning, systemd timer persistence, freeze/unfreeze support, panic fallback isolation, and a keyboard-shortcut–friendly toggle.

---

## Table of Contents

1. [Features](#features)
2. [Architecture Specification](#architecture-specification)
3. [How It Works & Safety](#how-it-works--safety)
4. [Installation & Source Relocation](#installation--source-relocation)
5. [Quick Start](#quick-start)
6. [Core Concepts](#core-concepts)
   - [Scanned Directories](#scanned-directories)
   - [Image Registry & Status](#image-registry--status)
   - [Crop Editor & Controls](#crop-editor--controls)
   - [The Active Rotation Pool vs. Panic Image](#the-active-rotation-pool-vs-panic-image)
   - [Rotation Orders](#rotation-orders)
   - [Freezing & Pausing](#freezing--pausing)
   - [Panic / Default Avatar & Fallback Behavior](#panic--default-avatar--fallback-behavior)
   - [Smart Notifications](#smart-notifications)
7. [Complete Command Reference](#complete-command-reference)
8. [Schedule & Configuration Guide](#schedule--configuration-guide)
9. [Keyboard Shortcut Setup (KDE Plasma)](#keyboard-shortcut-setup-kde-plasma)
10. [Data Storage & File Locations](#data-storage--file-locations)
11. [Development](#development)
12. [Uninstallation](#uninstallation)
13. [FAQ & Edge Cases](#faq--edge-cases)

---

## Architecture Specification

For an in-depth technical analysis, system boundaries, sequence diagrams, selection engine algorithms, and security design, see the dedicated [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Features

- 🖼 **Interactive Crop Editor** — Drag a square, scroll wheel to resize, arrow keys to nudge, and live circular preview matching KDE's lock/login screen.
- 📁 **Multi-Directory Scanning** — Add one or more source folders; set recursive or non-recursive per folder; suspend/resume entire directories at once.
- ⏰ **Flexible Scheduling Modes** — `daily` (midnight), `interval` (clock-aligned hours or custom durations like `6h`, `45m`, `12d 13h 52m`), `times` (specific clock times like `08:30, 14:00`), `weekly` (chosen day of week), and `monthly` (1st of month).
- ❄ **Freeze / Pause Rotation** — Temporarily lock your avatar indefinitely or for an exact duration (e.g. `3d`, `12h`, `1w`), with an optional specific picture of your choice.
- 🔀 **Five Selection Orders** — `random` (anti-consecutive), `lru` (Least Recently Used dynamic anti-starvation pool), `deck` (shuffle-bag cycle where every card is shown once before repeating), `sequential` (deterministic cycle), and `weighted` (frequency by weight).
- 🚨 **Panic / Default Avatar Isolation** — Store your default/safe image anywhere (even in the same folder as rotating images); it is strictly excluded from normal rotation.
- ⌨ **One-Key Hotkey Toggle** — `kdam toggle` works seamlessly: if frozen → unfreezes; if active → applies panic avatar & disables timer; if inactive → enables timer & rotates.
- 🔔 **Smart Desktop Notifications** — Notifies on real avatar changes; suppresses redundant notifications on system boot, when frozen, or when only 1 image is in the pool.
- ✅ **100% User Space & Rootless** — Communicates directly with the `org.freedesktop.Accounts.User` D-Bus service, requiring zero `sudo` or system file modifications.
- 💾 **Portable Backups** — Archive config, crop metadata, and source images as a `.tar.gz`; restore on any machine with automatic home-directory remapping.

---


## How It Works & Safety

KDE Plasma reads user avatars managed by `AccountsService` via D-Bus (`/org/freedesktop/Accounts/User<UID>`). When you set an avatar in KDE System Settings, it converts the image and tells AccountsService to update the icon path.

`kdam` automates this process:
1. It crops and scales your chosen source images to standard 256×256 PNGs stored inside `~/.kde-avatar-manager/processed/`.
2. It sends the update signal through your user's system D-Bus bus.
3. Your original image files are **never modified or touched**.
4. The systemd timer runs strictly under your user session (`systemctl --user`).

---

## Installation & Source Relocation

```bash
cd ~/Projects/antigravity-manager/kde-avatar-manager
bash install.sh
```

The installer builds a self-contained virtual environment at `~/.kde-avatar-manager/venv/` with `--system-site-packages` (so heavy libraries like PyQt5, Pillow, and dbus-python are shared without polluting system Python) and creates a wrapper executable at `~/.local/bin/kdam`.

### Moving the Source Folder Later
All runtime configurations, registries, cached images, and logs reside in `~/.kde-avatar-manager/`. The source folder is completely decoupled from runtime data. If you move or rename the repository directory at any time, simply run `bash install.sh` from the new folder once to update the binary symlink.

Make sure `~/.local/bin` is in your `$PATH`. If not, add this to your `~/.bashrc` or `~/.zshrc`:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

---

## Quick Start

```bash
# 1. Register your avatar image folders
kdam dirs add ~/Pictures/Avatars
kdam dirs add ~/Pictures/Wallpapers/Characters --recursive

# 2. Scan to discover all images
kdam scan

# 3. Configure image crops
kdam configure-all                   # Opens crop editor for all new images one by one
# OR configure specific ones:
kdam configure photo1.jpg photo2.png

# 4. Set a safe panic/default image
kdam panic set ~/Pictures/my-default.jpg

# 5. Enable the rotation timer
kdam enable

# 6. Check your status anytime
kdam status
```

---

## Core Concepts

### Scanned Directories & Multi-Directory Management
- You can register multiple image folders across different locations.
- Directories can be non-recursive (scan top-level only) or recursive (`-r`, scan all subfolders).
- **Smart Path & Name Resolution**:
  - When referencing images in any command (e.g. `kdam edit photo.jpg`, `kdam set photo.jpg`), providing just the filename is sufficient.
  - If multiple directories contain files with the **exact same filename** (e.g. `~/Pictures/Avatars/photo.jpg` and `~/Pictures/Wallpapers/photo.jpg`):
    - `kdam list` automatically shows distinguishing parent folder paths (e.g. `Avatars/photo.jpg` vs `Wallpapers/photo.jpg`), or use `kdam list -p` to see full absolute paths.
    - CLI commands interactively present a numbered menu (`[1] Avatars/photo.jpg, [2] Wallpapers/photo.jpg`) asking you to choose, or you can specify a folder fragment (e.g. `kdam edit Wallpapers/photo.jpg`).
- If you run `kdam dirs add` on a folder that is already added, `kdam` safely updates its recursive or enabled settings rather than creating duplicates.
- Disabling a directory (`kdam dirs toggle <path>`) excludes it from future `kdam scan` runs without removing already-configured images.

### Image Registry & Status
`kdam list` shows the full state table:
- `✓` **Active (Green)**: Configured with a crop and enabled for rotation.
- `✗` **Unconfigured (Yellow)**: Discovered by scanner, but needs a crop region before it can be used.
- `○` **Disabled (Dim)**: Configured, but temporarily disabled from rotation via `kdam disable-image`.
- `⚠` **Missing / Stale (Red)**: The original image file was moved or deleted from disk.

### Crop Editor & Controls
When configuring an image, the interactive crop editor opens:
- **Left Canvas**: Full image view with dark mask and rule-of-thirds grid.
- **Right Panel**: Real-time circular avatar preview matching KDE.

| Action | Control |
|---|---|
| Move crop square | Click & drag inside the square |
| Resize crop square | Drag the bottom-right white handle ◼ |
| Zoom / Resize | Mouse scroll wheel (±5% steps) or `+` / `–` keys |
| Micro-nudge (1 px) | Arrow keys (`←` `↑` `→` `↓`) |
| Fast-nudge (10 px) | `Shift` + Arrow keys |
| Save & Apply | Click **Save Crop** or press `Enter` |
| Skip Image | Click **Skip** or press `Esc` |
| Quit Editor | Click **Quit** |

### The Active Rotation Pool vs. Panic Image
- **Panic Image Isolation**: You can keep your panic/default image inside the exact same folder as your rotating avatars. As long as it is designated via `kdam panic set`, it is **strictly excluded** from the normal rotation pool.
- **Single / Zero Image Safety**: If only 1 image (or 0 images) are in the active rotation pool, `kdam` will never send redundant "Avatar changed" notifications.

### Rotation Orders
Change order via `kdam config set rotation.order <order>`:
1. `random` (default): Chooses randomly from the active pool, automatically preventing consecutive duplicates if 2 or more images exist.
2. `sequential`: Cycles in deterministic order, preserving the index across reboots.
3. `weighted`: Uses per-image weights (`kdam weight <image> <weight>`) to control selection probability (e.g. weight 3.0 is 3× more likely than weight 1.0).
4. `lru` (Least Recently Used / Anti-starvation): Automatically tracks when each image was last shown. It identifies the oldest / never-shown images (the top-$K$ candidates) and randomly selects among them with weight support.
   - **Pool size ($K$)**: Configured via `kdam config set rotation.lru_pool_size <value>`:
     - `default`: Dynamic sizing $\min(50, \max(10, \lfloor 0.15 \times N \rfloor))$ clamped to $N$. Keeps pool sized comfortably whether you have 10 images or 5,000 images without starving any image.
     - Custom integer (e.g. `15`): Locks $K$ to a custom pool size. If your collection currently has fewer images than $K$, the full pool is used until enough images are added.
5. `deck` (Shuffle-Bag Cycle): Shuffles all active images like a deck of cards. Each rotation draws one image. Every image is guaranteed to be shown exactly once before any image repeats. If new images are added mid-cycle, they are seamlessly shuffled into the remaining unplayed deck.

### Freezing & Pausing
You can pause rotation without disabling your systemd timers:
- `kdam freeze`: Locks current avatar indefinitely.
- `kdam freeze 3d`: Locks current avatar for 3 days, then auto-resumes on the next rotation after 3 days pass.
- `kdam freeze 1w -i favorite.jpg`: Sets `favorite.jpg` as the avatar and locks it for 1 week.
- `kdam unfreeze`: Immediately unlocks and resumes auto-rotation.
- `kdam unfreeze -r`: Unlocks and immediately rotates to the next image.

### Panic / Default Avatar & Fallback Behavior
- Set panic image: `kdam panic set <path>` (automatically launches crop editor).
- Adjust panic crop: `kdam panic edit`.
- Trigger panic reset: `kdam panic reset` (applies panic avatar and disables rotation).
- **Missing Panic File Fallback**: If the configured panic image is ever missing on disk, `kdam` falls back to the initial pristine system snapshot (`backup.png`). It will **never** substitute random images from your rotation pool.

### Smart Notifications
Notifications are only fired when a visible change occurs:
- System boot / reboot with no change: **Silent**.
- Single-item pool re-evaluating: **Silent**.
- When frozen: **Silent**.
- When avatar rotates to a new image: **Displays desktop notification**.

---

## Complete Command Reference

### Image Management
```bash
kdam scan                              # Scan configured dirs for new/missing images
kdam list                              # Display all registered images & state table
kdam list -p                           # Display all images with full directory paths
kdam add <path1> [path2...]            # Register image(s) & open crop editor
kdam add <path1> [path2...] --no-editor# Register image(s) as unconfigured
kdam rename <old> <new>                # Rename file on disk & migrate crop/state configs
kdam configure                         # List all pending unconfigured images
kdam configure <image1> [image2...]    # Open crop editor for specific image(s)
kdam configure-all                     # Open crop editor for all unconfigured images
kdam edit <image>                      # Re-open crop editor to modify an existing crop
kdam unconfigure <image1> [image2...]  # Reset crop config for image(s) (back to unconfigured)
kdam remove <image1> [image2...]       # Remove image(s) from manager (preserves disk files)
kdam remove --stale                    # Remove all missing/deleted image entries at once
kdam remove --all-unconfigured         # Remove all images that have never been cropped
kdam remove <image> --delete-file      # Remove image from manager AND delete from disk
kdam enable-image <image>              # Enable an image for rotation
kdam disable-image <image>             # Exclude an image from rotation without removing
kdam weight <image> <value>            # Set selection weight for weighted order (e.g. 2.5)
```
*(Note: `<image>` arguments accept exact filenames like `photo.jpg`, folder fragments like `Avatars/photo.jpg`, or full paths. If the same filename exists in multiple directories, `kdam` prompts you interactively to choose).*

### Immediate Avatar Actions & Freezing
```bash
kdam set <image>                       # Immediately set and crop image as avatar
kdam next                              # Force immediate rotation to next avatar
kdam next --count 3                    # Skip ahead 3 images at once
kdam next --dry-run                    # Preview which image would be next (no change made)
kdam next --quiet                      # Silent rotation (used by systemd)
kdam freeze                            # Freeze current avatar indefinitely
kdam freeze 2d                         # Freeze for 2 days (supports s, m, h, d, w)
kdam freeze 12h -i <image>             # Set specific image and freeze for 12 hours
kdam unfreeze                          # Unfreeze avatar and resume auto-rotation
kdam unfreeze -r                       # Unfreeze and rotate to next image immediately
kdam status                            # View rotation state, timer status, pool counts, orphaned
```

### Scheduling & Systemd Timers
```bash
kdam enable                            # Install and activate daily systemd timer
kdam disable                           # Stop and disable systemd timer
kdam toggle                            # Smart flip: Frozen→Unfreeze, On→Panic/Off, Off→On/Next
```

### Panic / Default Avatar & Initial Snapshot
```bash
kdam panic set <path>                  # Set panic avatar image (opens crop editor)
kdam panic set --current               # Capture current KDE avatar & set as panic image
kdam panic set <path> --no-editor      # Set panic avatar without opening crop editor
kdam panic edit                        # Re-open crop editor for current panic avatar
kdam panic snapshot                    # Capture active KDE avatar to backup.png
kdam panic reset                       # Immediately apply panic avatar & stop rotation
kdam panic show                        # Show current panic file path, status, and crop
```

### Source Directories
```bash
kdam dirs list                         # List registered folders & recursive flags
kdam dirs add <path>                   # Add directory (non-recursive)
kdam dirs add <path> -r                # Add directory (recursive scanning)
kdam dirs edit <path>                  # View settings for a specific directory
kdam dirs edit <path> --recursive      # Turn on recursive scanning for directory
kdam dirs edit <path> --no-recursive   # Turn off recursive scanning for directory
kdam dirs toggle <path>                # Temporarily enable/disable scanning (images unaffected)
kdam dirs suspend <path>               # Disable scanning AND pause all images from this directory
kdam dirs resume <path>                # Re-enable a suspended directory and restore its images
kdam dirs remove <path>                # Remove directory from configuration
```

### Configuration
```bash
kdam config show                       # Print raw config.json
kdam config set rotation.mode daily    # Rotate daily at midnight (default)
kdam config set rotation.mode interval # Rotate on interval (e.g. 6h, 45m, 12d 13h 52m)
kdam config set rotation.interval 6h   # Flexible interval duration
kdam config set rotation.mode times    # Rotate at specific times of day
kdam config set rotation.times "08:30, 14:00, 21:15"
kdam config set rotation.mode weekly   # Rotate weekly on specific day
kdam config set rotation.weekday sun   # mon | tue | wed | thu | fri | sat | sun
kdam config set rotation.mode monthly  # Rotate on the 1st of every month
kdam config set rotation.order lru     # Order: random | lru | deck | sequential | weighted
kdam config set rotation.lru_pool_size default # default (dynamic 15%) or integer K
```

### Backup & System Migration
```bash
# 1. Create a complete portable backup (includes settings, crops, AND all images)
kdam backup create ~/kdam_backup.tar.gz

# 2. Create a lightweight backup (settings & crop metadata ONLY, no image files)
kdam backup create ~/kdam_meta.tar.gz --no-images

# 3. Create a config-only backup (schedules & settings only)
kdam backup create ~/kdam_config.tar.gz --config-only

# 4. Inspect a backup archive (shows date, source user home, images count, directories)
kdam backup info ~/kdam_backup.tar.gz

# 5. Restore on a new PC or after reinstall (auto-remaps paths to new username)
kdam backup restore ~/kdam_backup.tar.gz

# 6. Restore and extract bundled images to a custom destination directory
kdam backup restore ~/kdam_backup.tar.gz --images-dir ~/Pictures/MyAvatars
```

### Uninstallation & Cleanup
```bash
# Standard uninstall (stops timer, removes ~/.local/bin/kdam, preserves crop configs):
kdam uninstall
# OR via repo script:
bash uninstall.sh

# Complete purge (removes everything including ~/.kde-avatar-manager/):
kdam uninstall --purge
```



---

## Keyboard Shortcut Setup (KDE Plasma)

You can bind `kdam toggle` to a global hotkey (e.g. `Meta+Shift+A`):

1. Open **System Settings** → **Shortcuts** (or **Custom Shortcuts** on Plasma 5).
2. Click **Add New** → **Command or URL**.
3. Set Name: `Toggle Avatar Rotation`.
4. Set Trigger / Shortcut: Press your desired keys (e.g. `Meta+Shift+A`).
5. Set Command: `kdam toggle`.
6. Click **Apply**.

### Toggle Behavior Matrix
- If avatar is **FROZEN** → Unfreezes and resumes schedule.
- If avatar rotation is **ON** → Applies panic/default avatar and disables auto-rotation.
- If avatar rotation is **OFF** → Enables timer and immediately rotates to the next image.

---

## Data Storage & File Locations

All runtime data is strictly isolated in standard user directories:

```
~/.kde-avatar-manager/
├── config.json                 # Rotation schedule, panic config, and state
├── images.json                 # Registry of all discovered images and crop coordinates
├── backup.png                  # Automatic snapshot of your system avatar before kdam was used
├── venv/                       # Isolated Python virtual environment
├── processed/                  # Working directory for 256×256 avatars
│   └── panic.png               # Processed panic avatar (rotation avatars rendered on-the-fly)
└── logs/
    └── rotation.log            # Append-only timestamped rotation history

~/.local/bin/kdam               # Executable bash launcher wrapper
~/.config/systemd/user/
├── kde-avatar-rotate.service   # Systemd oneshot user service
└── kde-avatar-rotate.timer     # Systemd timer (daily / interval)
```

---

## Development

### Running Tests

The test suite uses `pytest` and runs entirely in the isolated venv:

```bash
cd kde-avatar-manager

# Quick (summary only):
~/.kde-avatar-manager/venv/bin/python -m pytest tests/ -q

# Verbose (each test named):
~/.kde-avatar-manager/venv/bin/python -m pytest tests/ -v

# Single test file:
~/.kde-avatar-manager/venv/bin/python -m pytest tests/test_healthcheck.py -v

# Stop on first failure:
~/.kde-avatar-manager/venv/bin/python -m pytest tests/ -x
```

Tests are isolated — they never touch your live `~/.kde-avatar-manager/` data. A session-level fixture snapshots and restores your `config.json`, `images.json`, `backup.png`, and `processed/` before and after every test run.

### Type Checking (mypy)

```bash
pip install -e ".[typecheck]"
mypy kdam/ --ignore-missing-imports
```

mypy is configured in `pyproject.toml` under `[tool.mypy]`. C-extension modules (`dbus-python`, `PyQt5`) are excluded from strict checks via `ignore_missing_imports = true`.

### CI (GitHub Actions)

The workflow at `.github/workflows/ci.yml` runs on every push and pull request:
- **Tests** on Python 3.11 and 3.12 (Ubuntu latest)
- **Type check** with mypy on Python 3.12

### Project Structure

```
kdam/
├── __init__.py        # Version string
├── _cli_shared.py     # Shared CLI helpers (console, systemd utils, completions)
├── cli.py             # Root CLI group + all leaf commands
├── cli_panic.py       # `panic` subgroup (reset, set, edit, snapshot, show)
├── cli_dirs.py        # `dirs` subgroup (list, add, remove, toggle, edit, suspend, resume)
├── cli_config.py      # `config` subgroup (show, set)
├── cli_backup.py      # `backup` subgroup (create, info, restore)
├── config.py          # Config dataclasses, load/save, AVATAR_SIZE constant
├── db.py              # ImageDB — JSON-backed registry of discovered images
├── editor.py          # PyQt5 crop editor (interactive GUI)
├── kde.py             # D-Bus interface to KDE AccountsService
├── notify.py          # Fire-and-forget desktop notifications
├── rotator.py         # Image selection + processing + rotation logic
├── scanner.py         # Directory walking + image validation
└── backup.py          # Backup archive creation, inspection, and restore
```

---

## Uninstallation

To remove `kdam` and its systemd timers:

```bash
cd ~/Projects/antigravity-manager/kde-avatar-manager
bash uninstall.sh
```

To also delete all image registry caches and configuration data:
```bash
rm -rf ~/.kde-avatar-manager
```

---

## FAQ & Edge Cases

#### Q: When I reboot, will the lock/SDDM screen show the new avatar before I log in?
KDE's login manager (`SDDM`) reads your avatar icon from `/var/lib/AccountsService/icons/<user>` during system boot. When `kdam` rotates your avatar, it writes to AccountsService via D-Bus. This means whatever avatar was set during your previous session will be displayed on the login screen. Missed rotations (if your PC was turned off at midnight) execute immediately upon user login.

#### Q: What if an image in my folder is deleted or moved?
`kdam scan` flags missing images with `⚠ Missing`. During rotation, missing images are automatically skipped with an alert and will not break the rotation cycle. You can remove stale entries with `kdam remove <name>`.

#### Q: Can I manually set an avatar for an event without breaking my rotation schedule?
Yes! Run `kdam freeze 1d -i event-photo.jpg`. It will crop and apply `event-photo.jpg`, hold it for 24 hours, and automatically resume your regular rotation pool afterwards.

#### Q: Does `kdam` require root or `sudo`?
No. Standard desktop users have full D-Bus permissions over their own user account in `org.freedesktop.Accounts`. No root passwords or sudoers configurations are needed.
