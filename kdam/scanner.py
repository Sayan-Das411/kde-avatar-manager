"""Directory scanner — discovers new images and detects stale registry entries."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from .config import Config
from .db import ImageDB, ImageEntry

SUPPORTED_EXTENSIONS: frozenset = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}
)


def _is_valid_image(path: Path) -> bool:
    """Return True if *path* is a readable, non-corrupt image file."""
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return False
    try:
        with Image.open(path) as img:
            img.verify()  # verify() consumes the handle; do not use img after this call
        return True
    except Exception:
        return False


def _walk_directory(dir_path: Path, recursive: bool) -> list[Path]:
    """Yield resolved image Paths under *dir_path*."""
    images: list[Path] = []
    if not dir_path.exists() or not dir_path.is_dir():
        return images

    if recursive:
        for root, _dirs, files in os.walk(str(dir_path), followlinks=False, onerror=lambda _: None):
            root_path = Path(root)
            for fname in sorted(files):
                fpath = (root_path / fname).resolve()
                if fpath.suffix.lower() in SUPPORTED_EXTENSIONS and fpath.is_file():
                    images.append(fpath)
    else:
        try:
            for entry in sorted(dir_path.iterdir()):
                if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
                    images.append(entry.resolve())
        except PermissionError:
            pass

    return images


def scan(config: Config, db: ImageDB) -> tuple[list[str], list[str]]:
    """Scan all configured directories.

    - Adds newly-found valid images to the DB as unconfigured.
    - Does NOT remove stale entries (caller decides what to do with them).

    Returns:
        new_images  — list of absolute path strings added in this scan
        stale_paths — list of absolute path strings whose source file is gone
    """
    found_paths: set[str] = set()

    for dir_cfg in config.directories:
        if not dir_cfg.enabled:
            continue
        dir_path = Path(dir_cfg.path).expanduser().resolve()
        for img_path in _walk_directory(dir_path, dir_cfg.recursive):
            found_paths.add(str(img_path))

    existing_keys = set(db.all().keys())

    # Register genuinely new images (not yet in registry)
    new_images: list[str] = []
    for path_str in sorted(found_paths - existing_keys):
        p = Path(path_str)
        if _is_valid_image(p):
            db.set(path_str, ImageEntry(configured=False, enabled=False))
            new_images.append(path_str)

    # Detect stale entries (in registry, source gone from disk)
    stale_paths: list[str] = [k for k in db.all() if not Path(k).exists()]

    return new_images, stale_paths
