"""Avatar rotation logic — image processing and selection."""

from __future__ import annotations

import os
import random
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import kde, notify
from .config import (
    AVATAR_SIZE,
    LOG_FILE,  # noqa: F401
    LOGS_DIR,  # noqa: F401
    PROCESSED_DIR,
    Config,
    ensure_dirs,
    save_config,
)
from .db import CropInfo, ImageDB

# _write_log_entry is imported lazily inside rotate()/apply_specific() to avoid
# circular import (rotator ← _cli_shared ← rotator).  Use a helper wrapper here.


def _log(path: str) -> None:
    """Write a structured log entry — thin wrapper to avoid circular import."""
    from ._cli_shared import _write_log_entry

    _write_log_entry(path)


# ── Error notification rate-limiter ───────────────────────────────────────────
# When D-Bus is broken, the systemd timer fires every hour. Without rate-limiting,
# every fire sends a critical desktop notification, spamming the user.
# We suppress repeat error notifications within a 1-hour window.
# Persisted to ~/.kde-avatar-manager/.last_error_notify so oneshot systemd processes
# respect the cooldown.

_ERROR_NOTIFY_COOLDOWN_SECS = 3600  # 1 hour
_last_error_notify_ts: float | None = None  # process-local cache
_disk_ts_loaded: bool = False


def _should_notify_error() -> bool:
    """Return True if enough time has passed since the last error notification."""
    global _last_error_notify_ts, _disk_ts_loaded
    from . import config as _cfg

    notify_file = _cfg.CONFIG_DIR / ".last_error_notify"
    if not _disk_ts_loaded and _last_error_notify_ts is None:
        _disk_ts_loaded = True
        if notify_file.exists():
            try:
                _last_error_notify_ts = float(notify_file.read_text().strip())
            except Exception:
                pass

    now = datetime.now().timestamp()
    if _last_error_notify_ts is None or (now - _last_error_notify_ts) >= _ERROR_NOTIFY_COOLDOWN_SECS:
        _last_error_notify_ts = now
        try:
            _cfg.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            notify_file.write_text(str(now))
        except Exception:
            pass
        return True
    return False


# ── Image processing ──────────────────────────────────────────────────────────


def process_image(source_path: str | Path, crop: CropInfo) -> Path:
    """Crop + resize source image to a 256×256 PNG and return a temporary path.

    The caller is responsible for deleting the returned file after use.
    Only panic.png (written by panic commands) is kept persistently.
    """
    ensure_dirs()
    source_path = str(Path(source_path).resolve())

    with Image.open(source_path) as _raw:
        img = _raw.convert("RGBA")  # in-memory copy; _raw file handle is closed after this
    iw, ih = img.size

    # Clamp crop to image bounds (guards against images being replaced by smaller ones)
    x = max(0, min(crop.x, iw - 1))
    y = max(0, min(crop.y, ih - 1))
    size = max(1, min(crop.size, iw - x, ih - y))

    cropped = img.crop((x, y, x + size, y + size))
    resized = cropped.resize((AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)

    # Write to a temp file in PROCESSED_DIR so it is on the same filesystem
    # as other kdam data (avoids cross-device issues with KDE's D-Bus call).
    fd, tmp_path = tempfile.mkstemp(suffix=".png", dir=PROCESSED_DIR)
    os.close(fd)
    out_path = Path(tmp_path)
    resized.save(str(out_path), "PNG")
    return out_path


# ── Selection ─────────────────────────────────────────────────────────────────


def _get_lru_pool_size(n: int, config: Config) -> int:
    """Calculate the effective top-K LRU pool size.

    - If config.rotation.lru_pool_size is 'default':
        min(50, max(10, int(0.15 * n))) clamped to <= n.
    - If it is an integer string:
        int(val) clamped to <= n.
    """
    if n <= 0:
        return 0

    val_str = str(config.rotation.lru_pool_size).strip().lower()
    if val_str != "default":
        try:
            custom_k = int(val_str)
            if custom_k > 0:
                return min(custom_k, n)
        except ValueError:
            pass

    # Default formula: minimum 10, 15% scaling, capped at 50, clamped to <= n
    dynamic_k = min(50, max(10, int(0.15 * n)))
    return min(dynamic_k, n)


def get_next_image(config: Config, db: ImageDB) -> str | None:
    """Pick the next image path from the active pool based on rotation order.

    The panic/default image is excluded from normal rotation so it is never
    used as a regular rotating avatar.
    Returns None if the active pool is empty.
    """
    panic_path = str(Path(config.panic.path).resolve()) if config.panic.path else None
    active_dict = db.active()

    # Exclude panic image from rotation pool
    candidates = sorted(
        p for p in active_dict.keys() if panic_path is None or str(Path(p).resolve()) != panic_path
    )

    if not candidates:
        return None

    order = config.rotation.order.lower()

    if order == "sequential":
        idx = config.state.current_index % len(candidates)
        # Increment in-memory only — rotate() saves the full config after a
        # successful apply, so if the rotation fails the index does not advance.
        config.state.current_index = (idx + 1) % len(candidates)
        return candidates[idx]

    if order == "weighted":
        weights = [active_dict[p].weight for p in candidates]
        return random.choices(candidates, weights=weights, k=1)[0]

    if order in ("lru", "least-recent"):
        # Sort candidates ascending by last_used.
        # Images never used (last_used is None) go first (epoch 0).
        # A random secondary key breaks ties among images with identical timestamps
        # (especially the large cohort of "never used" images) so that no single
        # artist's images dominate the pool due to alphabetical ordering.
        def _sort_key(p: str) -> tuple[float, float]:
            entry = active_dict[p]
            if not entry.last_used:
                return (0.0, random.random())
            try:
                return (datetime.fromisoformat(entry.last_used).timestamp(), random.random())
            except Exception:
                return (0.0, random.random())

        candidates_by_recency = sorted(candidates, key=_sort_key)
        k = _get_lru_pool_size(len(candidates_by_recency), config)
        pool = candidates_by_recency[:k]

        # Avoid picking the exact same image twice in a row if multiple pool choices exist
        if len(pool) > 1 and config.state.last_image_path in pool:
            filtered = [p for p in pool if p != config.state.last_image_path]
            if filtered:
                pool = filtered

        # Weighted selection among the oldest K images
        pool_weights = [active_dict[p].weight for p in pool]
        return random.choices(pool, weights=pool_weights, k=1)[0]

    if order in ("deck", "shuffle-bag"):
        # Synchronize deck with currently active candidates:
        # 1. Prune any items no longer in candidates
        cand_set = set(candidates)
        deck = [p for p in config.state.deck if p in cand_set]

        # 2. Add any newly added candidates into the remaining deck (shuffled)
        in_deck_set = set(deck)
        # Determine items played in the current cycle: cand_set - in_deck_set
        # If new candidates were added that aren't in deck, shuffle them in
        missing_from_deck = [p for p in candidates if p not in in_deck_set]

        # If deck is empty, start a fresh cycle with all candidates shuffled
        if not deck:
            deck = list(candidates)
            random.shuffle(deck)
            # Ensure the first card of new cycle is not identical to last_image_path if > 1
            if len(deck) > 1 and deck[0] == config.state.last_image_path:
                deck[0], deck[-1] = deck[-1], deck[0]
        elif missing_from_deck:
            # We have an ongoing cycle. Insert new candidates randomly into remaining deck
            random.shuffle(missing_from_deck)
            deck.extend(missing_from_deck)

        chosen = deck.pop(0)
        config.state.deck = deck
        return chosen

    # Default: random — avoid picking the exact same image twice in a row if multiple exist
    if len(candidates) > 1 and config.state.last_image_path in candidates:
        choices = [p for p in candidates if p != config.state.last_image_path] or candidates
        return random.choice(choices)

    return random.choice(candidates)


# ── Rotation ──────────────────────────────────────────────────────────────────


def rotate(config: Config, db: ImageDB, quiet: bool = False) -> bool | None:
    """Perform one full rotation cycle:

    1. Pick next image (excluding panic image).
    2. Process (crop + resize) on-the-fly — no persistent cache.
    3. Apply via D-Bus, then delete the temp processed file.
    4. Log and send notification ONLY if the avatar actually changed.

    Returns:
        True  — rotation successful
        False — rotation failed (image missing/unreadable, D-Bus error, etc.)
        None  — active pool is empty; caller should stop retrying
    """
    next_path = get_next_image(config, db)
    if not next_path:
        return None  # pool is empty; caller should not retry

    if not Path(next_path).exists():
        if config.rotation.order in ("sequential", "deck", "shuffle-bag"):
            save_config(config)
        if not quiet:
            notify.send("⚠ Avatar Manager", f"Next image missing: {Path(next_path).name}")
        return False

    entry = db.get(next_path)
    if not entry or not entry.crop:
        return False

    processed: Path | None = None
    try:
        # Check whether the source image actually changed (for log/notification purposes only)
        image_changed = config.state.last_image_path != next_path

        # Process fresh every time — ~100ms, no persistent cache needed
        processed = process_image(next_path, entry.crop)

        # Always apply via D-Bus — this ensures the avatar is restored after
        # reboots/logins where KDE resets the lock-screen avatar even if our
        # config already remembers the last applied image.
        kde.set_avatar(processed)
        entry.last_used = datetime.now().isoformat()
        db.set(next_path, entry)
        db.save()

        config.state.last_rotation = datetime.now().isoformat()
        config.state.last_image_path = next_path
        save_config(config)

        # Write to rotation log and notify only when image actually changes
        if image_changed:
            _log(next_path)
            notify.send("🖼 Avatar changed", Path(next_path).name)
        return True

    except Exception as exc:  # noqa: BLE001
        if not quiet and _should_notify_error():
            notify.send("❌ Avatar Manager error", str(exc), urgency="critical")
        return False
    finally:
        # Always clean up the temp file whether rotation succeeded or failed
        if processed is not None:
            processed.unlink(missing_ok=True)


def apply_specific(path: str | Path, db: ImageDB, config: Config | None = None) -> bool:
    """Immediately apply a specific (already-configured) image as avatar."""
    abs_path = str(Path(path).resolve())
    entry = db.get(abs_path)
    if not entry or not entry.crop:
        return False

    processed: Path | None = None
    try:
        processed = process_image(abs_path, entry.crop)
        kde.set_avatar(processed)
        entry.last_used = datetime.now().isoformat()
        db.set(abs_path, entry)
        db.save()

        if config is not None:
            config.state.last_image_path = abs_path
            config.state.last_rotation = datetime.now().isoformat()
            save_config(config)

        # Log the manual set
        _log(abs_path)
        return True
    except Exception:
        return False
    finally:
        if processed is not None:
            processed.unlink(missing_ok=True)
