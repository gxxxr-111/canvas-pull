"""Compare Canvas with the manifest, then apply the differences."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .api import CanvasClient, CanvasError
from .collector import CourseScan, RemoteFile
from .manifest import STATE_DIR, Entry, Manifest, now_iso


class Kind(StrEnum):
    NEW = "new"
    MODIFIED = "modified"
    MOVED = "moved"
    DELETED = "deleted"
    RESTORED = "restored"  # in the manifest but missing on disk


@dataclass
class Change:
    kind: Kind
    path: str
    course: str
    old_path: str | None = None
    remote: RemoteFile | None = None
    old: Entry | None = None

    @property
    def needs_download(self) -> bool:
        return self.kind in (Kind.NEW, Kind.MODIFIED, Kind.RESTORED)

    def to_dict(self) -> dict:
        d = {"kind": str(self.kind), "course": self.course, "path": self.path}
        if self.old_path and self.old_path != self.path:
            d["old_path"] = self.old_path
        return d


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    locked: list[RemoteFile] = field(default_factory=list)


@dataclass
class SyncResult:
    applied: list[Change] = field(default_factory=list)
    failed: list[tuple[Change, str]] = field(default_factory=list)
    unchanged: int = 0  # reported as modified by Canvas but byte-identical


def make_plan(scans: Iterable[CourseScan], manifest: Manifest, save_dir: Path) -> Plan:
    plan = Plan()
    seen: set[int] = set()
    scanned: set[int] = set()
    for scan in scans:
        if scan.error:
            continue  # never infer deletions from a course we could not read
        scanned.add(scan.course.id)
        for rf in scan.files:
            seen.add(rf.file.id)
            if rf.locked:
                plan.locked.append(rf)
                continue
            old = manifest.get(rf.file.id)
            label = rf.course.label
            if old is None or old.deleted:
                plan.changes.append(Change(Kind.NEW, rf.path, label, remote=rf))
            elif old.size != rf.file.size or old.stamp != rf.file.stamp:
                plan.changes.append(
                    Change(Kind.MODIFIED, rf.path, label, old_path=old.path, remote=rf, old=old)
                )
            elif old.path != rf.path:
                plan.changes.append(
                    Change(Kind.MOVED, rf.path, label, old_path=old.path, remote=rf, old=old)
                )
            elif not (save_dir / old.path).is_file():
                plan.changes.append(Change(Kind.RESTORED, rf.path, label, remote=rf, old=old))

    for entry in manifest.entries.values():
        if not entry.deleted and entry.course_id in scanned and entry.id not in seen:
            plan.changes.append(Change(Kind.DELETED, entry.path, entry.course, old=entry))
    plan.changes.sort(key=lambda c: (c.course.lower(), c.path.lower()))
    return plan


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(rf: RemoteFile, size: int, sha: str) -> Entry:
    return Entry(
        id=rf.file.id,
        course_id=rf.course.id,
        course=rf.course.label,
        path=rf.path,
        size=size,
        stamp=rf.file.stamp,
        sha256=sha,
        synced_at=now_iso(),
    )


class _Applier:
    def __init__(self, save_dir: Path, keep_history: bool) -> None:
        self.save_dir = save_dir
        self.keep_history = keep_history
        self.state = save_dir / STATE_DIR
        self.tmp = self.state / "tmp"
        self.history = self.state / "history" / datetime.now().strftime("%Y%m%d-%H%M%S")

    def retire(self, rel: str) -> None:
        """Move an outdated local file into history (or drop it when history is off)."""
        src = self.save_dir / rel
        if not src.is_file():
            return
        if self.keep_history:
            dst = self.history / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
        else:
            src.unlink()

    def place(self, tmp: Path, rel: str, sha: str) -> None:
        target = self.save_dir / rel
        if target.is_file() and _sha256(target) != sha:
            self.retire(rel)  # e.g. a file downloaded by v1, or edited locally
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, target)

    def move(self, old_rel: str, new_rel: str) -> bool:
        src, dst = self.save_dir / old_rel, self.save_dir / new_rel
        if not src.is_file():
            return False
        if dst.exists():
            self.retire(new_rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)
        return True


def execute(
    plan: Plan,
    *,
    client: CanvasClient,
    manifest: Manifest,
    save_dir: Path,
    keep_history: bool = True,
    concurrency: int = 4,
    on_progress: Callable[[Change], None] | None = None,
) -> SyncResult:
    result = SyncResult()
    fs = _Applier(save_dir, keep_history)
    downloads: list[Change] = []

    for change in plan.changes:
        if change.kind is Kind.DELETED:
            assert change.old
            manifest.mark_deleted(change.old.id)  # the local copy is kept on purpose
            result.applied.append(change)
        elif change.kind is Kind.MOVED:
            assert change.remote and change.old and change.old_path
            if fs.move(change.old_path, change.path):
                old = change.old
                manifest.upsert(_entry(change.remote, old.size, old.sha256))
                result.applied.append(change)
            else:
                downloads.append(change)
        else:
            downloads.append(change)

    if not downloads:
        return result

    fs.tmp.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(client.download, c.remote.file.url, fs.tmp / f"{c.remote.file.id}.part"): c
            for c in downloads
            if c.remote
        }
        for future in as_completed(futures):
            change = futures[future]
            rf = change.remote
            assert rf is not None
            tmp = fs.tmp / f"{rf.file.id}.part"
            try:
                size, sha = future.result()
            except CanvasError as exc:
                tmp.unlink(missing_ok=True)
                result.failed.append((change, str(exc)))
                continue

            old = change.old
            same_bytes = old is not None and old.sha256 == sha
            if change.old_path and change.old_path != change.path:
                if same_bytes:
                    (save_dir / change.old_path).unlink(missing_ok=True)
                else:
                    fs.retire(change.old_path)
            fs.place(tmp, change.path, sha)
            manifest.upsert(_entry(rf, size, sha))

            if change.kind is Kind.MODIFIED and same_bytes:
                if change.old_path and change.old_path != change.path:
                    change.kind = Kind.MOVED  # only the name changed
                    result.applied.append(change)
                else:
                    result.unchanged += 1
            else:
                result.applied.append(change)
            if on_progress:
                on_progress(change)

    with contextlib.suppress(OSError):
        fs.tmp.rmdir()
    return result
