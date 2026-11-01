"""Desktop notifications (macOS Notification Center, or notify-send on Linux)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable

from .sync import Change

TITLE = "Canvas Pull"


def send(message: str, title: str = TITLE) -> bool:
    """Show a notification. Returns False when no notifier is available."""
    try:
        if sys.platform == "darwin":
            # Pass text as argv so quotes in file names cannot break the script.
            script = [
                "-e", "on run argv",
                "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run",
            ]  # fmt: skip
            cmd = ["osascript", *script, title, message]
        elif shutil.which("notify-send"):
            cmd = ["notify-send", "--app-name=canvas-pull", title, message]
        else:
            return False
        return subprocess.run(cmd, capture_output=True, timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def describe(changes: Iterable[Change], limit: int = 220) -> str:
    """'Signals: 2 new, 1 modified · OS: 1 deleted' (truncated)."""
    per_course: dict[str, Counter[str]] = defaultdict(Counter)
    for c in changes:
        per_course[c.course][str(c.kind)] += 1
    parts = [
        f"{course}: " + ", ".join(f"{n} {k}" for k, n in counts.items())
        for course, counts in per_course.items()
    ]
    text = " · ".join(parts)
    return text if len(text) <= limit else text[: limit - 1] + "…"
