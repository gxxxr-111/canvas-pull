"""Find courses and list every downloadable file in them.

Files are gathered from several places in a course, in priority order. When the same
file shows up in more than one place it keeps the path from the first source, so a file
in the Files tab never moves just because an assignment also links to it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath

from .api import CanvasClient, CanvasError, ForbiddenError
from .models import CanvasFile, Course, Folder

SOURCES: tuple[str, ...] = (
    "files",
    "modules",
    "syllabus",
    "assignments",
    "pages",
    "announcements",
    "discussions",
)

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# Matches /files/123 and /courses/45/files/123 in links, images and API endpoints.
_FILE_LINK = re.compile(r"/(?:courses/(\d+)/)?files/(\d+)")


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
    source: str = "files"

    @property
    def locked(self) -> bool:
        return self.file.locked_for_user or not self.file.url


@dataclass
class CourseScan:
    course: Course
    files: list[RemoteFile] = field(default_factory=list)
    files_tab_hidden: bool = False
    error: str | None = None

    def count(self, source: str) -> int:
        return sum(1 for f in self.files if f.source == source)


# -- courses ------------------------------------------------------------------


def list_courses(
    client: CanvasClient,
    *,
    include_past: bool = False,
    include_completed: bool = False,
    now: datetime | None = None,
) -> list[Course]:
    """Active courses (minus ones whose term ended), optionally plus completed ones."""
    now = now or datetime.now(UTC)
    courses: dict[int, Course] = {}
    states = ["active"] + (["completed"] if include_completed else [])
    for state in states:
        params = {"enrollment_state": state, "include[]": ["term"]}
        for raw in client.paginate("courses", params):
            course = Course.model_validate(raw)
            if course.access_restricted_by_date or course.id in courses:
                continue
            if state == "completed":
                course = course.model_copy(update={"completed": True})
            elif not include_past and not include_completed:
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


# -- sources ------------------------------------------------------------------
# Each source yields (file, folder parts) or (file id, owning course id, folder parts)
# when only a link is known; links are resolved later, after deduplication.

Found = tuple[CanvasFile, list[str]]
Link = tuple[int, int, list[str]]


def file_links(html: str | None, course_id: int) -> list[tuple[int, int]]:
    """(file id, course id) pairs linked from a piece of Canvas HTML, in order, unique."""
    out: dict[int, int] = {}
    for cid, fid in _FILE_LINK.findall(html or ""):
        out.setdefault(int(fid), int(cid) if cid else course_id)
    return list(out.items())


def _files_tab(client: CanvasClient, course: Course) -> Iterator[Found]:
    folders = {
        f.id: [sanitize(p) for p in PurePosixPath(f.full_name).parts[1:]]
        for f in (Folder.model_validate(r) for r in client.paginate(f"courses/{course.id}/folders"))
    }
    for raw in client.paginate(f"courses/{course.id}/files"):
        f = CanvasFile.model_validate(raw)
        yield f, folders.get(f.folder_id or -1, [])


def _modules(client: CanvasClient, course: Course) -> Iterator[Link]:
    for module in client.paginate(f"courses/{course.id}/modules"):
        folder = ["Modules", sanitize(module.get("name") or f"Module {module.get('id')}")]
        for item in client.paginate(f"courses/{course.id}/modules/{module['id']}/items"):
            if item.get("type") == "File" and item.get("content_id"):
                yield int(item["content_id"]), course.id, folder


def _syllabus(client: CanvasClient, course: Course) -> Iterator[Link]:
    raw = client.get(f"courses/{course.id}", {"include[]": ["syllabus_body"]})
    for fid, cid in file_links(raw.get("syllabus_body"), course.id):
        yield fid, cid, ["Syllabus"]


def _html_items(
    client: CanvasClient, course: Course, path: str, params: dict, root: str, title: str, body: str
) -> Iterator[Found | Link]:
    for item in client.paginate(path, params):
        folder = [root, sanitize(str(item.get(title) or item.get("id")))]
        for att in item.get("attachments") or []:
            if isinstance(att, dict) and att.get("id"):
                yield CanvasFile.model_validate(att), folder
        for fid, cid in file_links(item.get(body), course.id):
            yield fid, cid, folder


def _assignments(client: CanvasClient, course: Course):
    path = f"courses/{course.id}/assignments"
    return _html_items(client, course, path, {}, "Assignments", "name", "description")


def _pages(client: CanvasClient, course: Course):
    params = {"include[]": ["body"], "published": "true"}
    return _html_items(
        client, course, f"courses/{course.id}/pages", params, "Pages", "title", "body"
    )


def _announcements(client: CanvasClient, course: Course):
    path = f"courses/{course.id}/discussion_topics"
    params = {"only_announcements": "true"}
    return _html_items(client, course, path, params, "Announcements", "title", "message")


def _discussions(client: CanvasClient, course: Course):
    path = f"courses/{course.id}/discussion_topics"
    return _html_items(client, course, path, {}, "Discussions", "title", "message")


_SCANNERS = {
    "files": _files_tab,
    "modules": _modules,
    "syllabus": _syllabus,
    "assignments": _assignments,
    "pages": _pages,
    "announcements": _announcements,
    "discussions": _discussions,
}


def _resolve(client: CanvasClient, fid: int, cid: int) -> CanvasFile | None:
    for path in (f"courses/{cid}/files/{fid}", f"files/{fid}"):
        try:
            return CanvasFile.model_validate(client.get(path))
        except ForbiddenError:
            continue
    return None


def _assign_paths(
    found: list[tuple[CanvasFile, list[str], str]], course: Course, root: str
) -> list[RemoteFile]:
    """Unique, case-insensitively distinct paths. Older files keep the plain name."""
    taken: set[str] = set()
    out: list[RemoteFile] = []
    for f, parts, source in sorted(found, key=lambda e: e[0].id):
        name = sanitize(f.display_name or f.filename or str(f.id))
        path = str(PurePosixPath(root, *parts, name))
        if path.lower() in taken:
            stem, dot, ext = name.rpartition(".")
            name = f"{stem} ({f.id}).{ext}" if dot and stem else f"{name} ({f.id})"
            path = str(PurePosixPath(root, *parts, name))
        taken.add(path.lower())
        out.append(RemoteFile(file=f, course=course, path=path, source=source))
    return out


def scan_course(
    client: CanvasClient, course: Course, dirname: str, sources: Sequence[str] = SOURCES
) -> CourseScan:
    """List a course's files from every enabled source.

    A hidden tab (401/403/404) just skips that source. Any other error marks the whole
    scan as failed, so no file of this course is reported as deleted.
    """
    scan = CourseScan(course=course)
    files: dict[int, tuple[CanvasFile, list[str], str]] = {}
    links: dict[int, tuple[int, list[str], str]] = {}
    try:
        for source in (s for s in SOURCES if s in sources):
            try:
                for entry in _SCANNERS[source](client, course):
                    if isinstance(entry[0], CanvasFile):
                        f, parts = entry  # type: ignore[misc]
                        if f.id not in files and f.id not in links:
                            files[f.id] = (f, parts, source)
                    else:
                        fid, cid, parts = entry  # type: ignore[misc]
                        if fid not in files and fid not in links:
                            links[fid] = (cid, parts, source)
            except ForbiddenError:
                if source == "files":
                    scan.files_tab_hidden = True
        for fid, (cid, parts, source) in links.items():
            f = _resolve(client, fid, cid)
            if f is not None:
                files[fid] = (f, parts, source)
        scan.files = _assign_paths(list(files.values()), course, dirname)
    except CanvasError as exc:
        scan.error = str(exc)
    return scan
