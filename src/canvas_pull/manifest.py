"""Local record of what has been synced, keyed by Canvas file id.

Stored as JSON in <save_dir>/.canvas-pull/ so it is readable and diffs well in git.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

STATE_DIR = ".canvas-pull"
MANIFEST_VERSION = 1


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class Entry:
    id: int
    course_id: int
    course: str
    path: str
    size: int
    stamp: str | None
    sha256: str
    synced_at: str
    deleted: bool = False


class Manifest:
    def __init__(
        self,
        path: Path,
        entries: dict[int, Entry] | None = None,
        archived: dict[int, list[str]] | None = None,
    ) -> None:
        self.path = path
        self.entries: dict[int, Entry] = entries or {}
        # Completed courses already synced in full: course id -> sources used then.
        self.archived: dict[int, list[str]] = archived or {}

    @classmethod
    def load(cls, save_dir: Path) -> Manifest:
        path = save_dir / STATE_DIR / "manifest.json"
        if not path.is_file():
            return cls(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = {e["id"]: Entry(**e) for e in data.get("files", [])}
        archived = {int(k): list(v) for k, v in data.get("archived_courses", {}).items()}
        return cls(path, entries, archived)

    def is_archived(self, course_id: int, sources: list[str]) -> bool:
        """True if this completed course was fully synced with (at least) these sources."""
        done = self.archived.get(course_id)
        return done is not None and set(sources) <= set(done)

    def mark_archived(self, course_id: int, sources: list[str]) -> None:
        self.archived[course_id] = sorted(sources)

    def get(self, file_id: int) -> Entry | None:
        return self.entries.get(file_id)

    def upsert(self, entry: Entry) -> None:
        self.entries[entry.id] = entry

    def mark_deleted(self, file_id: int) -> None:
        if file_id in self.entries:
            self.entries[file_id].deleted = True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": MANIFEST_VERSION,
            "files": [asdict(e) for e in sorted(self.entries.values(), key=lambda e: e.id)],
            "archived_courses": {str(k): v for k, v in sorted(self.archived.items())},
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


def append_log(save_dir: Path, record: dict) -> None:
    path = save_dir / STATE_DIR / "log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_log(save_dir: Path, limit: int | None = 10) -> list[dict]:
    """Sync records, oldest first; the last `limit` of them (all if None)."""
    path = save_dir / STATE_DIR / "log.jsonl"
    if not path.is_file():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in (lines if limit is None else lines[-limit:])]


class SyncLocked(Exception):
    """Another canvas-pull sync is already running on this save folder."""


@contextmanager
def sync_lock(save_dir: Path) -> Iterator[None]:
    """Exclusive, non-blocking lock on the save folder (flock on Unix, msvcrt on Windows)."""
    path = save_dir / STATE_DIR / "lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SyncLocked from None
        try:
            yield
        finally:
            if os.name == "nt":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
