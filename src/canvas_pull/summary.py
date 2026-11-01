"""Merge many sync records into one net summary ("what changed this week")."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def parse_since(value: str, now: datetime | None = None) -> datetime:
    """'7d', '24h', '2w', 'today' or a date like '2026-10-01' -> aware datetime."""
    now = now or datetime.now(UTC)
    text = value.strip().lower()
    if text == "today":
        local = datetime.now().astimezone()
        return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    m = re.fullmatch(r"(\d+)\s*([hdw])", text)
    if m:
        return now - timedelta(**{_UNITS[m[2]]: int(m[1])})
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"Can't read {value!r}; try 7d, 24h, 2w, today or 2026-10-01.") from None
    return (
        dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    )


@dataclass
class NetChange:
    kind: str  # new | modified | moved | deleted
    course: str
    path: str
    old_path: str | None = None
    time: str = ""


def summarize(records: list[dict], since: datetime) -> list[NetChange]:
    """Net effect of all changes after `since`, oldest record first in `records`.

    new→modified stays new, new→deleted disappears, a moved file keeps its first path.
    """
    state: dict[str, NetChange] = {}
    for rec in records:
        if datetime.fromisoformat(rec["time"]) < since:
            continue
        for c in rec["changes"]:
            kind, path, course, t = c["kind"], c["path"], c["course"], rec["time"]
            if kind == "restored":  # a local event, not a Canvas change
                continue
            if kind == "new":
                state[path] = NetChange("new", course, path, time=t)
                continue
            old = c.get("old_path") or path
            prev = state.pop(old, None)
            if kind == "deleted":
                if not (prev and prev.kind == "new"):  # added then removed: no net change
                    state[path] = NetChange("deleted", course, path, time=t)
                continue
            # modified or moved
            origin = prev.old_path if prev and prev.old_path else old
            origin = origin if origin != path else None
            if prev and prev.kind == "new":
                state[path] = NetChange("new", course, path, time=t)
            elif kind == "modified" or (prev and prev.kind == "modified"):
                state[path] = NetChange("modified", course, path, origin, t)
            elif origin:  # moved, and not back to where it started
                state[path] = NetChange("moved", course, path, origin, t)
    order = {"new": 0, "modified": 1, "moved": 2, "deleted": 3}
    return sorted(state.values(), key=lambda n: (n.course.lower(), order[n.kind], n.path.lower()))
