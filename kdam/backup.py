"""Backup and Restore system for kde-avatar-manager (kdam).

Handles creating portable backup archives containing:
- config.json (settings, schedule, panic settings, state)
- images.json (registered images, crop coordinates, weights)
- backup.png (original system avatar snapshot)
- Optional: all source image files and panic image files.

Also handles smart restoration with automatic home directory remapping
(e.g. /home/mit -> /home/newuser) and extraction of bundled images.
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any

from kdam import __version__

from .config import (
    BACKUP_FILE,
    CONFIG_DIR,
    CONFIG_FILE,
    IMAGES_FILE,
    Config,
    _db_lock,
    load_config,
    save_config,
)
from .db import ImageDB

MANIFEST_FILENAME = "kdam_manifest.json"


def create_backup(
    output_path: str | Path | None = None,
    include_images: bool = True,
    config_only: bool = False,
) -> Path:
    """Create a portable .tar.gz backup archive.

    Returns the Path to the created archive.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    db = ImageDB()
    config = load_config()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if output_path:
        out = Path(output_path).expanduser().resolve()
        if out.is_dir():
            out = out / f"kdam_backup_{timestamp}.tar.gz"
        elif not out.name.endswith(".tar.gz") and not out.name.endswith(".tgz"):
            out = Path(str(out) + ".tar.gz")
    else:
        out = Path.home() / f"kdam_backup_{timestamp}.tar.gz"

    out.parent.mkdir(parents=True, exist_ok=True)

    # Collect source images
    image_paths: list[Path] = []
    if not config_only:
        for p_str in db.all():
            p = Path(p_str)
            if p.exists():
                image_paths.append(p.resolve())

        if config.panic.path and Path(config.panic.path).exists():
            p_path = Path(config.panic.path).resolve()
            if p_path not in image_paths:
                image_paths.append(p_path)

    backup_type = "config_only" if config_only else ("full" if include_images else "metadata_only")

    manifest = {
        "version": "1.0",
        "kdam_version": __version__,
        "created_at": datetime.now().isoformat(),
        "original_home": str(Path.home()),
        "backup_type": backup_type,
        "include_images": include_images and not config_only,
        "image_count": len(image_paths) if not config_only else 0,
        "directories": [d.to_dict() for d in config.directories],
        "panic_path": config.panic.path,
    }

    with tarfile.open(out, "w:gz") as tar:
        # 1. Manifest
        manifest_data = json.dumps(manifest, indent=2).encode("utf-8")
        ti = tarfile.TarInfo(name=MANIFEST_FILENAME)
        ti.size = len(manifest_data)
        ti.mtime = int(datetime.now().timestamp())
        tar.addfile(ti, io.BytesIO(manifest_data))

        # 2. config.json
        if CONFIG_FILE.exists():
            tar.add(CONFIG_FILE, arcname="config.json")

        if not config_only:
            # 3. images.json
            if IMAGES_FILE.exists():
                tar.add(IMAGES_FILE, arcname="images.json")

            # 4. backup.png
            if BACKUP_FILE.exists():
                tar.add(BACKUP_FILE, arcname="backup.png")

            # 5. Source image files (if requested)
            if include_images:
                for idx, img_p in enumerate(image_paths, 1):
                    arc_name = f"images/{idx:04d}_{img_p.name}"
                    tar.add(img_p, arcname=arc_name)

    return out


def inspect_backup(archive_path: str | Path) -> dict[str, Any]:
    """Read and return the manifest and contents of a backup archive."""
    p = Path(archive_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Backup archive not found: {p}")

    with tarfile.open(p, "r:gz") as tar:
        try:
            m_member = tar.getmember(MANIFEST_FILENAME)
            f = tar.extractfile(m_member)
            if f is None:
                raise ValueError("Could not read manifest from archive.")
            manifest: dict = json.loads(f.read().decode("utf-8"))
        except KeyError:
            raise ValueError("Archive is not a valid kdam backup (missing manifest).") from None

        members = [m.name for m in tar.getmembers()]
        manifest["archive_size"] = p.stat().st_size
        manifest["file_list"] = members
        return manifest


def restore_backup(
    archive_path: str | Path,
    target_images_dir: str | Path | None = None,
) -> tuple[bool, str]:
    """Restore configuration, metadata, and images from a backup archive.

    Automatically remaps home paths (e.g. /home/olduser -> /home/newuser).
    If images are bundled, extracts them and relinks images.json and config.json.
    """
    p = Path(archive_path).expanduser().resolve()
    if not p.exists():
        return False, f"Archive not found: {p}"

    manifest = inspect_backup(p)
    old_home = manifest.get("original_home", "")
    new_home = str(Path.home())
    has_images = manifest.get("include_images", False)
    conflicting_names: set[str] = set()  # populated below if has_images; avoids NameError

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Determine where images will be extracted — only needed if backup contains images.
    # img_dest_dir is resolved lazily inside the tarfile block so we never create
    # ~/Pictures/Avatars (or any directory) unless images are actually being extracted.
    _img_dest_dir_override = target_images_dir  # carry the user's --images-dir arg

    with tarfile.open(p, "r:gz") as tar:
        # 1. Extract config.json
        try:
            cfg_member = tar.getmember("config.json")
            f = tar.extractfile(cfg_member)
            if f:
                cfg_dict = json.loads(f.read().decode("utf-8"))
                # Remap directories
                for d in cfg_dict.get("directories", []):
                    if old_home and d["path"].startswith(old_home):
                        d["path"] = d["path"].replace(old_home, new_home, 1)
                # Remap panic path
                if cfg_dict.get("panic", {}).get("path"):
                    pp = cfg_dict["panic"]["path"]
                    if old_home and pp.startswith(old_home):
                        cfg_dict["panic"]["path"] = pp.replace(old_home, new_home, 1)
                # Save restored config
                restored_cfg = Config.from_dict(cfg_dict)
                save_config(restored_cfg)
        except KeyError:
            pass

        # 2. Extract backup.png — keep a pre-restore copy if one already exists
        try:
            b_member = tar.getmember("backup.png")
            f = tar.extractfile(b_member)
            if f:
                if BACKUP_FILE.exists():
                    BACKUP_FILE.rename(BACKUP_FILE.with_suffix(".png.pre-restore"))
                with BACKUP_FILE.open("wb") as out_f:
                    shutil.copyfileobj(f, out_f, length=1024 * 1024)
        except KeyError:
            pass

        # 3. Extract bundled images and map paths
        #    Pre-scan the archive first to detect filename collisions (two images
        #    from different directories sharing the same basename).
        path_mapping: dict[str, str] = {}  # map_key -> absolute new path on disk
        # conflicting_names already initialized above (before tarfile block)

        if has_images:
            # Scan archive members first — only resolve/create the destination
            # directory if there are actual image files to extract.
            # has_images=True in manifest but image_count=0 means the backup was
            # created with --include-images on an empty DB: no directory needed.
            from collections import Counter

            _fname_count: Counter = Counter()
            _actual_image_members = []
            for m in tar.getmembers():
                if m.name.startswith("images/") and m.isfile():
                    _actual_image_members.append(m)
                    _raw = m.name.split("/", 1)[1]
                    _cn = _raw[5:] if (len(_raw) > 5 and _raw[:4].isdigit() and _raw[4] == "_") else _raw
                    _fname_count[_cn] += 1

            if _actual_image_members:
                # Only now resolve and create destination directory
                if _img_dest_dir_override:
                    img_dest_dir = Path(_img_dest_dir_override).expanduser().resolve()
                else:
                    dirs = manifest.get("directories", [])
                    if dirs:
                        first_dir = dirs[0]["path"]
                        if old_home and first_dir.startswith(old_home):
                            first_dir = first_dir.replace(old_home, new_home, 1)
                        img_dest_dir = Path(first_dir)
                    else:
                        img_dest_dir = Path.home() / "Pictures" / "Avatars"
                img_dest_dir.mkdir(parents=True, exist_ok=True)
            conflicting_names = {cn for cn, c in _fname_count.items() if c > 1}

            if conflicting_names:
                print(
                    "\n⚠  Filename conflict(s) detected during restore:\n"
                    + "".join(
                        f"   • '{n}' exists in multiple source directories\n"
                        for n in sorted(conflicting_names)
                    )
                    + "   Conflicting files have been extracted with their index prefix\n"
                    + "   (e.g. '0001_photo.jpg', '0002_photo.jpg') to avoid overwriting each other.\n"
                    + "   Please rename the copies you want to keep, then run:\n"
                    + "     kdam add <renamed-file>   — to register each one with a unique name.\n"
                )

            for member in tar.getmembers():
                if member.name.startswith("images/") and (member.isfile() or member.isreg()):
                    fname = member.name.split("/", 1)[1]
                    clean_name = (
                        fname[5:] if (len(fname) > 5 and fname[:4].isdigit() and fname[4] == "_") else fname
                    )

                    if clean_name in conflicting_names:
                        # Use the full indexed name so both files land on disk distinctly
                        dest_name = Path(fname).name  # basename only — prevent traversal
                        map_key = fname  # unique key per archive entry
                    else:
                        dest_name = Path(clean_name).name  # basename only — prevent traversal
                        map_key = clean_name

                    dest_file = (img_dest_dir / dest_name).resolve()
                    # Guard: ensure resolved path stays inside the target directory.
                    # startswith() is insufficient — "/home/mit/restored-evil" starts with
                    # "/home/mit/restored". relative_to() raises ValueError if outside.
                    try:
                        dest_file.relative_to(img_dest_dir.resolve())
                    except ValueError:
                        continue  # path escapes target dir — skip
                    f = tar.extractfile(member)
                    if f:
                        with dest_file.open("wb") as out_f:
                            shutil.copyfileobj(f, out_f, length=1024 * 1024)
                        path_mapping[map_key] = str(dest_file)

        # 4. Extract and restore images.json
        try:
            imgs_member = tar.getmember("images.json")
            f = tar.extractfile(imgs_member)
            if f:
                raw_imgs = json.loads(f.read().decode("utf-8"))
                restored_imgs = {}

                for orig_path, entry_dict in raw_imgs.items():
                    orig_name = Path(orig_path).name

                    if has_images and orig_name in conflicting_names:
                        # Can't reliably match by name — fall back to home remapping.
                        # The user has been warned and told to re-register these manually.
                        if old_home and orig_path.startswith(old_home):
                            new_path = orig_path.replace(old_home, new_home, 1)
                        else:
                            new_path = orig_path
                    elif has_images and orig_name in path_mapping:
                        new_path = path_mapping[orig_name]
                    elif old_home and orig_path.startswith(old_home):
                        new_path = orig_path.replace(old_home, new_home, 1)
                    else:
                        new_path = orig_path

                    restored_imgs[new_path] = entry_dict

                with _db_lock():
                    _imgs_tmp = IMAGES_FILE.with_suffix(".tmp")
                    try:
                        _imgs_tmp.write_text(json.dumps(restored_imgs, indent=2))
                        _imgs_tmp.replace(IMAGES_FILE)
                    except Exception:
                        _imgs_tmp.unlink(missing_ok=True)
                        raise
        except KeyError:
            pass

    # Ensure directories in config exist (mkdir only — no re-save needed)
    cfg = load_config()
    for d in cfg.directories:
        Path(d.path).mkdir(parents=True, exist_ok=True)

    msg = f"Successfully restored {manifest.get('image_count', 0)} image(s) and configuration."
    if conflicting_names:
        msg += (
            f" ({len(conflicting_names)} filename conflict(s) — "
            f"{', '.join(sorted(conflicting_names))} — see warnings above.)"
        )
    return True, msg
