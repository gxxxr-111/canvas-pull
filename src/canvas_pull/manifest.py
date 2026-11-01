"""Local record of what has been synced, keyed by Canvas file id.

Stored as JSON in <save_dir>/.canvas-pull/ so it is readable and diffs well in git.
"""

from __future__ import annotations

import json
import os
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
    def __init__(self, path: Path, entries: dict[int, Entry] | None = None) -> None:
        self.path = path
        self.entries: dict[int, Entry] = entries or {}

    @classmethod
    def load(cls, save_dir: Path) -> Manifest:
        path = save_dir / STATE_DIR / "manifest.json"
        if not path.is_file():
            return cls(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = {e["id"]: Entry(**e) for e in data.get("files", [])}
        return cls(path, entries)

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
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


def append_log(save_dir: Path, record: dict) -> None:
    path = save_dir / STATE_DIR / "log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_log(save_dir: Path, limit: int = 10) -> list[dict]:
    path = save_dir / STATE_DIR / "log.jsonl"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()]
