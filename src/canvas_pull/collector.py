"""Find active courses and list every downloadable file in them."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath

from .api import CanvasClient, CanvasError, ForbiddenError
from .models import CanvasFile, Course, Folder

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize(name: str, max_len: int = 150) -> str:
    """Make a Canvas name safe as a single path component on macOS/Linux/Windows."""
    name = unicodedata.normalize("NFC", name)
    name = _UNSAFE.sub("_", name).strip().rstrip(". ")
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            name = stem[: max_len - len(ext) - 1].rstrip() + "." + ext
        else:
            name = name[:max_len].rstrip()
    return name or "_"


@dataclass(frozen=True)
class RemoteFile:
    file: CanvasFile
    course: Course
    path: str  # POSIX path relative to the save directory

    @property
    def locked(self) -> bool:
        return self.file.locked_for_user or not self.file.url


@dataclass
class CourseScan:
    course: Course
    files: list[RemoteFile] = field(default_factory=list)
    source: str = "files"  # "files" or "modules"
    error: str | None = None


def list_courses(
    client: CanvasClient, *, include_past: bool = False, now: datetime | None = None
) -> list[Course]:
    """Courses the user is actively enrolled in, minus ones whose term has ended."""
    now = now or datetime.now(UTC)
    params = {"enrollment_state": "active", "include[]": ["term"]}
    courses: dict[int, Course] = {}
    for raw in client.paginate("courses", params):
        course = Course.model_validate(raw)
        if course.access_restricted_by_date:
            continue
        if not include_past:
            end = (course.term.end_at if course.term else None) or course.end_at
            if end and end < now:
                continue
        courses[course.id] = course
    return sorted(courses.values(), key=lambda c: c.label.lower())


def course_dirnames(courses: Iterable[Course]) -> dict[int, str]:
    """One folder name per course; disambiguates courses that share a name."""
    courses = list(courses)
    base = {c.id: sanitize(c.label) for c in courses}
    counts: dict[str, int] = {}
    for name in base.values():
        counts[name.lower()] = counts.get(name.lower(), 0) + 1
    return {
        cid: (f"{name} ({cid})" if counts[name.lower()] > 1 else name) for cid, name in base.items()
    }


def _folder_paths(folders: Iterable[Folder]) -> dict[int, list[str]]:
    # full_name looks like "course files/Week 1/Slides"; drop the root.
    return {f.id: [sanitize(p) for p in PurePosixPath(f.full_name).parts[1:]] for f in folders}


def _dedupe(entries: list[tuple[CanvasFile, list[str]]], course: Course, root: str):
    """Build unique, case-insensitively distinct paths. Older files keep the plain name."""
    taken: set[str] = set()
    out: list[RemoteFile] = []
    for f, parts in sorted(entries, key=lambda e: e[0].id):
        name = sanitize(f.display_name or f.filename or str(f.id))
        path = str(PurePosixPath(root, *parts, name))
        if path.lower() in taken:
            stem, dot, ext = name.rpartition(".")
            name = f"{stem} ({f.id}).{ext}" if dot and stem else f"{name} ({f.id})"
            path = str(PurePosixPath(root, *parts, name))
        taken.add(path.lower())
        out.append(RemoteFile(file=f, course=course, path=path))
    return out


def _scan_files_tab(client: CanvasClient, course: Course) -> list[tuple[CanvasFile, list[str]]]:
    folders = _folder_paths(
        Folder.model_validate(f) for f in client.paginate(f"courses/{course.id}/folders")
    )
    return [
        (f, folders.get(f.folder_id or -1, []))
        for f in (
            CanvasFile.model_validate(r) for r in client.paginate(f"courses/{course.id}/files")
        )
    ]


def _scan_modules(client: CanvasClient, course: Course) -> list[tuple[CanvasFile, list[str]]]:
    seen: set[int] = set()
    out: list[tuple[CanvasFile, list[str]]] = []
    for module in client.paginate(f"courses/{course.id}/modules"):
        module_dir = sanitize(module.get("name") or f"Module {module.get('id')}")
        for item in client.paginate(f"courses/{course.id}/modules/{module['id']}/items"):
            fid = item.get("content_id")
            if item.get("type") != "File" or not fid or fid in seen:
                continue
            seen.add(fid)
            try:
                raw = client.get(f"courses/{course.id}/files/{fid}")
            except ForbiddenError:
                continue
            out.append((CanvasFile.model_validate(raw), ["Modules", module_dir]))
    return out


def scan_course(client: CanvasClient, course: Course, dirname: str) -> CourseScan:
    """List a course's files from the Files tab, falling back to Modules when it is hidden."""
    scan = CourseScan(course=course)
    try:
        try:
            entries = _scan_files_tab(client, course)
        except ForbiddenError:
            entries = []
        if not entries:
            try:
                module_entries = _scan_modules(client, course)
            except ForbiddenError:
                module_entries = []
            if module_entries:
                entries, scan.source = module_entries, "modules"
        scan.files = _dedupe(entries, course, dirname)
    except CanvasError as exc:
        scan.error = str(exc)
    return scan
