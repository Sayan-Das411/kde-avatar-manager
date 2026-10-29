"""Image registry — stores per-image crop metadata and state."""
from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import IMAGES_FILE, _db_lock, ensure_dirs

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class CropInfo:
    x: int
    y: int
    size: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CropInfo:
        return cls(x=int(d["x"]), y=int(d["y"]), size=int(d["size"]))


@dataclass
class ImageEntry:
    configured: bool = False         # Has crop been set by the user?
    enabled: bool = False            # Active in rotation pool?
    weight: float = 1.0              # For weighted-random mode
    crop: CropInfo | None = None
    last_used: str | None = None        # ISO-8601 datetime
    suspended_by_dir: bool = False         # Disabled by `kdam dirs suspend`, not by the user

    def to_dict(self) -> dict:
        return {
            "configured": self.configured,
            "enabled": self.enabled,
            "weight": self.weight,
            "crop": self.crop.to_dict() if self.crop else None,
            "last_used": self.last_used,
            "suspended_by_dir": self.suspended_by_dir,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ImageEntry:
        crop_data = d.get("crop")
        return cls(
            configured=d.get("configured", False),
            enabled=d.get("enabled", False),
            weight=float(d.get("weight", 1.0)),
            crop=CropInfo.from_dict(crop_data) if crop_data else None,
            last_used=d.get("last_used"),
            suspended_by_dir=d.get("suspended_by_dir", False),
        )


# ── Registry ──────────────────────────────────────────────────────────────────

class ImageDB:
    """JSON-backed registry of managed avatar images.

    Keys are resolved absolute paths (str).  This ensures that two directory
    entries pointing at the same image via different paths are deduplicated.
    """

    def __init__(self) -> None:
        self._data: dict[str, ImageEntry] = {}
        self.load()

    # ── I/O ──────────────────────────────────────────────────────────────────

    def load(self) -> None:
        with _db_lock():
            if IMAGES_FILE.exists():
                try:
                    with IMAGES_FILE.open() as f:
                        raw: dict = json.load(f)
                    self._data = {k: ImageEntry.from_dict(v) for k, v in raw.items()}
                    return
                except json.JSONDecodeError:
                    corrupt = IMAGES_FILE.with_suffix(".json.corrupt")
                    try:
                        IMAGES_FILE.rename(corrupt)
                        print(
                            f"⚠ {IMAGES_FILE} was corrupt — renamed to {corrupt.name}",
                            file=sys.stderr,
                        )
                    except OSError:
                        pass
                except OSError as exc:
                    # Permission error, disk error, etc. — warn but do NOT silently
                    # fall through to empty DB, as a subsequent save() would wipe the file.
                    print(
                        f"⚠ Could not read {IMAGES_FILE}: {exc}",
                        file=sys.stderr,
                    )
                    self._data = {}
                    return
                except Exception:
                    pass
            self._data = {}

    def save(self) -> None:
        with _db_lock():
            ensure_dirs()
            raw = {k: v.to_dict() for k, v in self._data.items()}
            tmp = IMAGES_FILE.with_suffix(".tmp")
            try:
                with tmp.open("w") as f:
                    json.dump(raw, f, indent=2)
                tmp.replace(IMAGES_FILE)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise

    # ── CRUD ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _key(path: str | Path) -> str:
        return str(Path(path).resolve())

    def has(self, path: str | Path) -> bool:
        return self._key(path) in self._data

    def get(self, path: str | Path) -> ImageEntry | None:
        return self._data.get(self._key(path))

    def set(self, path: str | Path, entry: ImageEntry) -> None:
        self._data[self._key(path)] = entry

    def remove(self, path: str | Path) -> None:
        self._data.pop(self._key(path), None)

    # ── Views ─────────────────────────────────────────────────────────────────

    def all(self) -> dict[str, ImageEntry]:
        return dict(self._data)

    def active(self) -> dict[str, ImageEntry]:
        """Configured AND enabled (the rotation pool)."""
        return {k: v for k, v in self._data.items() if v.configured and v.enabled}

    def unconfigured(self) -> dict[str, ImageEntry]:
        """Registered but no crop set yet."""
        return {k: v for k, v in self._data.items() if not v.configured}

    def stale(self) -> dict[str, ImageEntry]:
        """Source file no longer exists on disk."""
        return {k: v for k, v in self._data.items() if not Path(k).exists()}

    def items(self) -> Iterator[tuple[str, ImageEntry]]:
        return iter(self._data.items())

    def __len__(self) -> int:
        return len(self._data)
