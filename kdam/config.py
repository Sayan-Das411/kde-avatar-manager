"""Configuration management for kde-avatar-manager.

All runtime data lives in ~/.kde-avatar-manager/ — independent of where the
project source directory is located.  Moving the project only requires re-running
install.sh from the new location to refresh the binary in ~/.local/bin/kdam.
"""
import contextlib
import fcntl
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

CONFIG_DIR: Path = Path.home() / ".kde-avatar-manager"
CONFIG_FILE: Path = CONFIG_DIR / "config.json"
IMAGES_FILE: Path = CONFIG_DIR / "images.json"
PROCESSED_DIR: Path = CONFIG_DIR / "processed"
BACKUP_FILE: Path = CONFIG_DIR / "backup.png"
LOGS_DIR: Path = CONFIG_DIR / "logs"
LOG_FILE: Path = LOGS_DIR / "rotation.log"
_LOCK_FILE: Path = CONFIG_DIR / ".lock"

# ── Avatar output ─────────────────────────────────────────────────────────────

AVATAR_SIZE: int = 256  # px — KDE AccountsService avatar dimension


def cleanup_temp_files() -> None:
    """Silently remove any stray temporary files in PROCESSED_DIR.

    Only panic.png is kept. Stray temporary files from interrupted
    rotations or unexpected process kills are cleaned automatically.
    """
    if not PROCESSED_DIR.exists():
        return
    panic_png = (PROCESSED_DIR / "panic.png").resolve()
    try:
        for p in PROCESSED_DIR.iterdir():
            if p.is_file() and p.resolve() != panic_png:
                p.unlink(missing_ok=True)
    except Exception:
        pass


def ensure_dirs() -> None:
    """Create all runtime directories if they don't exist and clean stray temp files."""
    CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
    except Exception:
        pass
    PROCESSED_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    cleanup_temp_files()


@contextlib.contextmanager
def _db_lock():
    """Exclusive advisory lock around any read-modify-write on the data files.

    Uses fcntl.flock on ~/.kde-avatar-manager/.lock so concurrent kdam calls
    (e.g. systemd timer firing while the user runs a command) cannot corrupt
    images.json or config.json.  The lock is released automatically on exit.
    """
    CONFIG_DIR.mkdir(exist_ok=True)  # only need CONFIG_DIR to exist for the lock file
    with _LOCK_FILE.open("a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Directory:
    path: str
    recursive: bool = False
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Directory":
        return cls(
            path=d["path"],
            recursive=d.get("recursive", False),
            enabled=d.get("enabled", True),
        )


@dataclass
class PanicConfig:
    """Panic reset configuration.  path=None means use the auto-snapshot backup."""
    path: str | None = None
    crop: dict | None = None  # {"x": int, "y": int, "size": int}

    def to_dict(self) -> dict:
        return {"path": self.path, "crop": self.crop}

    @classmethod
    def from_dict(cls, d: dict) -> "PanicConfig":
        return cls(path=d.get("path"), crop=d.get("crop"))



@dataclass
class RotationConfig:
    mode: str = "daily"          # "daily" | "interval" | "times" | "weekly" | "monthly"
    order: str = "random"        # "sequential" | "random" | "weighted" | "lru" | "deck"
    interval_hours: int = 24     # integer hours for simple intervals (legacy & quick)
    interval: str = ""           # flexible duration (e.g. "6h", "45m", "12d 13h 52m")
    start_hour: int = 0          # anchor hour (0-23) for clock-aligned intervals (e.g. 0 -> 00:00, 06:00...)
    times: list[str] = field(default_factory=list)  # list of "HH:MM" clock times for "times" mode
    weekday: str = "mon"         # "mon".."sun" for "weekly" mode
    lru_pool_size: str = "default"  # "default" or integer string (e.g. "15")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RotationConfig":
        raw_times = d.get("times", [])
        if isinstance(raw_times, str):
            times_list = [t.strip() for t in raw_times.split(",") if t.strip()]
        else:
            times_list = list(raw_times)

        return cls(
            mode=d.get("mode", "daily"),
            order=d.get("order", "random"),
            interval_hours=max(1, int(d.get("interval_hours", 24))),
            interval=str(d.get("interval", "")).strip(),
            start_hour=max(0, min(23, int(d.get("start_hour", 0)))),
            times=times_list,
            weekday=str(d.get("weekday", "mon")).strip().lower(),
            lru_pool_size=str(d.get("lru_pool_size", "default")),
        )


@dataclass
class State:
    enabled: bool = True
    current_index: int = 0               # pointer for sequential rotation
    last_rotation: str | None = None  # ISO-8601 datetime string
    last_image_path: str | None = None  # Source path of currently applied rotation image
    frozen: bool = False                 # True = rotation paused
    frozen_until: str | None = None   # ISO-8601 end time; None = indefinite
    deck: list[str] = field(default_factory=list)  # Remaining images in current deck cycle

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        return cls(
            enabled=d.get("enabled", True),
            current_index=d.get("current_index", 0),
            last_rotation=d.get("last_rotation"),
            last_image_path=d.get("last_image_path"),
            frozen=d.get("frozen", False),
            frozen_until=d.get("frozen_until"),
            deck=list(d.get("deck", [])),
        )



@dataclass
class Config:
    rotation: RotationConfig = field(default_factory=RotationConfig)
    directories: list[Directory] = field(default_factory=list)
    panic: PanicConfig = field(default_factory=PanicConfig)
    state: State = field(default_factory=State)

    def to_dict(self) -> dict:
        return {
            "rotation": self.rotation.to_dict(),
            "directories": [d.to_dict() for d in self.directories],
            "panic": self.panic.to_dict(),
            "state": self.state.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        return cls(
            rotation=RotationConfig.from_dict(d.get("rotation", {})),
            directories=[Directory.from_dict(x) for x in d.get("directories", [])],
            panic=PanicConfig.from_dict(d.get("panic", {})),
            state=State.from_dict(d.get("state", {})),
        )


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_config() -> Config:
    """Load config from disk (exclusive lock).  Returns defaults if missing or corrupt.

    If the file exists but is corrupt (invalid JSON), it is renamed to
    config.json.corrupt so the user can inspect it, and defaults are loaded.
    """
    with _db_lock():
        if CONFIG_FILE.exists():
            try:
                with CONFIG_FILE.open() as f:
                    data = json.load(f)
                return Config.from_dict(data)
            except json.JSONDecodeError:
                # Preserve the corrupt file for user inspection
                corrupt = CONFIG_FILE.with_suffix(".json.corrupt")
                try:
                    CONFIG_FILE.rename(corrupt)
                    print(
                        f"⚠ {CONFIG_FILE} was corrupt — renamed to {corrupt.name}",
                        file=sys.stderr,
                    )
                except OSError:
                    pass
            except Exception:
                pass  # other I/O errors — fall through to defaults
    return Config()


def save_config(config: Config) -> None:
    """Persist config to disk atomically (exclusive lock + temp-file swap)."""
    with _db_lock():
        ensure_dirs()
        tmp = CONFIG_FILE.with_suffix(".tmp")
        try:
            with tmp.open("w") as f:
                json.dump(config.to_dict(), f, indent=2)
            tmp.replace(CONFIG_FILE)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
