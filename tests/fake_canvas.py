"""A tiny in-memory Canvas used by the tests via httpx.MockTransport."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

BASE = "https://canvas.test"


@dataclass
class FakeFile:
    id: int
    name: str
    folder_id: int
    content: bytes
    modified_at: str = "2026-09-01T00:00:00Z"
    locked: bool = False


@dataclass
class FakeCourse:
    id: int
    name: str
    folders: dict[int, str] = field(default_factory=lambda: {1: "course files"})
    files: dict[int, FakeFile] = field(default_factory=dict)
    files_tab_hidden: bool = False
    modules: dict[str, list[int]] = field(default_factory=dict)
    term_end: str | None = None


class FakeCanvas:
    def __init__(self) -> None:
        self.courses: dict[int, FakeCourse] = {}
        self.downloads: list[int] = []
        self.flaky: dict[str, int] = {}  # path -> remaining 503s
        self.token = "good-token"

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _file_json(self, f: FakeFile) -> dict:
        return {
            "id": f.id,
            "display_name": f.name,
            "filename": f.name,
            "folder_id": f.folder_id,
            "size": len(f.content),
            "modified_at": f.modified_at,
            "updated_at": f.modified_at,
            "url": "" if f.locked else f"{BASE}/files/{f.id}/download?verifier=x",
            "locked_for_user": f.locked,
        }

    def _page(self, request: httpx.Request, items: list) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)
        per_page = int(q.get("per_page", ["10"])[0])
        page = int(q.get("page", ["1"])[0])
        chunk = items[(page - 1) * per_page : page * per_page]
        headers = {}
        if page * per_page < len(items):
            nq = {k: v[0] for k, v in q.items()} | {"page": page + 1}
            headers["Link"] = f'<{request.url.copy_with(query=urlencode(nq).encode())}>; rel="next"'
        return httpx.Response(200, json=chunk, headers=headers)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.flaky.get(path, 0) > 0:
            self.flaky[path] -= 1
            return httpx.Response(503)
        if path.startswith("/files/"):
            fid = int(path.split("/")[2])
            for c in self.courses.values():
                if fid in c.files:
                    self.downloads.append(fid)
                    return httpx.Response(200, content=c.files[fid].content)
            return httpx.Response(404)

        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(
                401,
                json={"errors": [{"message": "Invalid access token."}]},
                headers={"WWW-Authenticate": 'Bearer realm="canvas-lms"'},
            )

        parts = path.removeprefix("/api/v1/").split("/")
        if parts == ["users", "self"]:
            return httpx.Response(200, json={"id": 1, "name": "Student"})
        if parts == ["courses"]:
            items = [
                {"id": c.id, "name": c.name, "term": {"name": "Fall", "end_at": c.term_end}}
                for c in self.courses.values()
            ]
            return self._page(request, items)

        course = self.courses[int(parts[1])]
        unauthorized = httpx.Response(
            401, json={"status": "unauthorized", "errors": [{"message": "user not authorized"}]}
        )
        rest = parts[2:]
        if rest == ["folders"]:
            if course.files_tab_hidden:
                return unauthorized
            return self._page(
                request, [{"id": i, "full_name": n} for i, n in course.folders.items()]
            )
        if rest == ["files"]:
            if course.files_tab_hidden:
                return unauthorized
            return self._page(request, [self._file_json(f) for f in course.files.values()])
        if len(rest) == 2 and rest[0] == "files":
            return httpx.Response(200, json=self._file_json(course.files[int(rest[1])]))
        if rest == ["modules"]:
            mods = [{"id": i, "name": n} for i, n in enumerate(course.modules, 1)]
            return self._page(request, mods)
        if len(rest) == 3 and rest[0] == "modules" and rest[2] == "items":
            name = list(course.modules)[int(rest[1]) - 1]
            items = [{"type": "File", "content_id": fid} for fid in course.modules[name]]
            items.append({"type": "Page", "page_url": "intro"})
            return self._page(request, items)
        return httpx.Response(404, content=json.dumps({"errors": [{"message": "not found"}]}))
