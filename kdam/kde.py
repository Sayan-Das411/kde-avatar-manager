"""KDE / AccountsService D-Bus interface.

Uses org.freedesktop.Accounts.User.SetIconFile — the same call that KDE System
Settings makes internally — so no root privilege is ever required.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import dbus


def _user_iface() -> dbus.Interface:
    bus = dbus.SystemBus()
    uid = os.getuid()
    user_path = f"/org/freedesktop/Accounts/User{uid}"
    user_obj = bus.get_object("org.freedesktop.Accounts", user_path)
    return dbus.Interface(user_obj, "org.freedesktop.Accounts.User")


def set_avatar(icon_path: str | Path) -> None:
    """Set the KDE user avatar.  icon_path must be an absolute path.

    Raises RuntimeError if the D-Bus call fails (e.g. accountsservice not running).
    """
    try:
        iface = _user_iface()
        iface.SetIconFile(str(Path(icon_path).resolve()))
    except dbus.exceptions.DBusException as exc:
        raise RuntimeError(f"D-Bus SetIconFile failed: {exc}") from exc


def get_current_avatar() -> str | None:
    """Return the current avatar path from AccountsService, or None on failure."""
    try:
        bus = dbus.SystemBus()
        uid = os.getuid()
        user_path = f"/org/freedesktop/Accounts/User{uid}"
        user_obj = bus.get_object("org.freedesktop.Accounts", user_path)
        props = dbus.Interface(user_obj, "org.freedesktop.DBus.Properties")
        result = props.Get("org.freedesktop.Accounts.User", "IconFile")
        return str(result) if result else None
    except Exception:
        return None


def backup_current_avatar(dest: Path) -> bool:
    """Copy the current AccountsService avatar file to *dest*.

    Returns True on success, False if the current avatar is missing or the
    copy fails (non-fatal; the caller decides how to handle it).
    """
    try:
        current = get_current_avatar()
        if current and Path(current).exists():
            shutil.copy2(current, dest)
            return True
    except Exception:
        pass
    return False
