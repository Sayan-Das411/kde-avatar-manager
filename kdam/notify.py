"""Desktop notifications via notify-send.  All calls are fire-and-forget."""
from __future__ import annotations

import shutil
import subprocess


def send(title: str, message: str = "", urgency: str = "normal") -> None:
    """Send a desktop notification.  Silently skipped if notify-send is absent."""
    if not shutil.which("notify-send"):
        return
    try:
        cmd = [
            "notify-send",
            "--app-name=KDAM",
            f"--urgency={urgency}",
            title,
        ]
        if message:
            cmd.append(message)
        # Fire-and-forget — don't block the main process waiting for the
        # notification daemon (the old subprocess.run blocked up to 3s).
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
