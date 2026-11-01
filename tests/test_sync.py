from __future__ import annotations

from pathlib import Path

import pytest

from canvas_pull.api import AuthError, CanvasClient
from canvas_pull.collector import (
    DEFAULT_SOURCES,
    SOURCES,
    course_dirnames,
    list_courses,
    sanitize,
    scan_course,
)
from canvas_pull.manifest import Manifest
from canvas_pull.sync import Kind, execute, make_plan

from .fake_canvas import BASE, FakeCanvas, FakeCourse, FakeFile


@pytest.fixture
def canvas() -> FakeCanvas:
    fake = FakeCanvas()
    course = FakeCourse(id=10, name="Signals & Systems")
    course.folders[2] = "course files/Week 1"
    course.files[100] = FakeFile(100, "syllabus.pdf", 1, b"syllabus v1")
    course.files[101] = FakeFile(101, "lecture1.pptx", 2, b"slides")
    fake.courses[10] = course
    return fake


def client_for(fake: FakeCanvas, token: str = "good-token") -> CanvasClient:
    return CanvasClient(BASE, token, transport=fake.transport(), backoff=0)


def run(fake: FakeCanvas, save_dir: Path, sources=DEFAULT_SOURCES, **kw):
    with client_for(fake) as client:
        courses = list_courses(client)
        names = course_dirnames(courses)
        scans = [scan_course(client, c, names[c.id], sources) for c in courses]
        manifest = Manifest.load(save_dir)
        plan = make_plan(scans, manifest, save_dir)
        result = execute(plan, client=client, manifest=manifest, save_dir=save_dir, **kw)
        manifest.save()
    return plan, result


def kinds(result) -> dict[str, str]:
    return {c.path: str(c.kind) for c in result.applied}


def test_first_sync_downloads_everything(canvas, tmp_path):
    _, result = run(canvas, tmp_path)
    assert kinds(result) == {
        "Signals & Systems/syllabus.pdf": "new",
        "Signals & Systems/Week 1/lecture1.pptx": "new",
    }
    assert (tmp_path / "Signals & Systems/Week 1/lecture1.pptx").read_bytes() == b"slides"


def test_second_sync_is_a_noop(canvas, tmp_path):
    run(canvas, tmp_path)
    canvas.downloads.clear()
    plan, result = run(canvas, tmp_path)
    assert plan.changes == [] and result.applied == []
    assert canvas.downloads == []


def test_modified_file_keeps_old_version_in_history(canvas, tmp_path):
    run(canvas, tmp_path)
    f = canvas.courses[10].files[100]
    f.content, f.modified_at = b"syllabus v2 (longer)", "2026-10-01T00:00:00Z"
    _, result = run(canvas, tmp_path)
    assert kinds(result) == {"Signals & Systems/syllabus.pdf": "modified"}
    assert (tmp_path / "Signals & Systems/syllabus.pdf").read_bytes() == b"syllabus v2 (longer)"
    history = list((tmp_path / ".canvas-pull/history").rglob("syllabus.pdf"))
    assert [h.read_bytes() for h in history] == [b"syllabus v1"]


def test_timestamp_bump_with_same_bytes_is_not_a_change(canvas, tmp_path):
    run(canvas, tmp_path)
    canvas.courses[10].files[100].modified_at = "2026-10-01T00:00:00Z"
    _, result = run(canvas, tmp_path)
    assert result.applied == [] and result.unchanged == 1
    _, again = run(canvas, tmp_path)
    assert again.unchanged == 0  # manifest picked up the new timestamp


def test_rename_is_a_move_without_download(canvas, tmp_path):
    run(canvas, tmp_path)
    canvas.downloads.clear()
    canvas.courses[10].files[101].name = "Lecture 01.pptx"
    _, result = run(canvas, tmp_path)
    assert [(c.kind, c.old_path, c.path) for c in result.applied] == [
        (
            Kind.MOVED,
            "Signals & Systems/Week 1/lecture1.pptx",
            "Signals & Systems/Week 1/Lecture 01.pptx",
        )
    ]
    assert canvas.downloads == []
    assert not (tmp_path / "Signals & Systems/Week 1/lecture1.pptx").exists()


def test_deleted_on_canvas_keeps_local_copy(canvas, tmp_path):
    run(canvas, tmp_path)
    del canvas.courses[10].files[101]
    _, result = run(canvas, tmp_path)
    assert kinds(result) == {"Signals & Systems/Week 1/lecture1.pptx": "deleted"}
    assert (tmp_path / "Signals & Systems/Week 1/lecture1.pptx").exists()
    plan, _ = run(canvas, tmp_path)
    assert plan.changes == []  # reported once only


def test_missing_local_file_is_restored(canvas, tmp_path):
    run(canvas, tmp_path)
    (tmp_path / "Signals & Systems/syllabus.pdf").unlink()
    _, result = run(canvas, tmp_path)
    assert kinds(result) == {"Signals & Systems/syllabus.pdf": "restored"}


def test_pagination_collects_all_files(tmp_path):
    fake = FakeCanvas()
    course = FakeCourse(id=1, name="Big")
    for i in range(250):
        course.files[i + 1] = FakeFile(i + 1, f"f{i}.txt", 1, b"x")
    fake.courses[1] = course
    _, result = run(fake, tmp_path)
    assert len(result.applied) == 250


def test_hidden_files_tab_falls_back_to_modules(tmp_path):
    fake = FakeCanvas()
    course = FakeCourse(id=5, name="Hidden", files_tab_hidden=True)
    course.files[7] = FakeFile(7, "notes.pdf", 1, b"n")
    course.modules = {"Week 1: Intro": [7], "Again": [7]}
    fake.courses[5] = course
    _, result = run(fake, tmp_path)
    assert kinds(result) == {"Hidden/Modules/Week 1_ Intro/notes.pdf": "new"}


def test_locked_files_are_skipped_not_deleted(canvas, tmp_path):
    canvas.courses[10].files[102] = FakeFile(102, "exam.pdf", 1, b"later", locked=True)
    plan, result = run(canvas, tmp_path)
    assert [rf.file.id for rf in plan.locked] == [102]
    assert "Signals & Systems/exam.pdf" not in kinds(result)


def test_duplicate_names_get_unique_paths(tmp_path):
    fake = FakeCanvas()
    course = FakeCourse(id=1, name="Dup")
    course.files[1] = FakeFile(1, "Notes.pdf", 1, b"a")
    course.files[2] = FakeFile(2, "notes.pdf", 1, b"b")
    fake.courses[1] = course
    _, result = run(fake, tmp_path)
    assert set(kinds(result)) == {"Dup/Notes.pdf", "Dup/notes (2).pdf"}


def test_past_term_courses_are_filtered(canvas, tmp_path):
    canvas.courses[11] = FakeCourse(id=11, name="Old", term_end="2020-01-01T00:00:00Z")
    with client_for(canvas) as client:
        assert [c.id for c in list_courses(client)] == [10]
        assert [c.id for c in list_courses(client, include_past=True)] == [11, 10]


def test_retries_transient_errors(canvas, tmp_path):
    canvas.flaky["/api/v1/courses"] = 2
    _, result = run(canvas, tmp_path)
    assert len(result.applied) == 2


def test_bad_token_raises_auth_error(canvas):
    with client_for(canvas, token="nope") as client, pytest.raises(AuthError):
        list_courses(client)


def test_failed_course_does_not_mark_files_deleted(canvas, tmp_path):
    run(canvas, tmp_path)
    canvas.flaky["/api/v1/courses/10/folders"] = 99
    plan, _ = run(canvas, tmp_path)
    assert plan.changes == []


def test_sanitize():
    assert sanitize('a/b:c*?"<>|.') == "a_b_c______"
    assert sanitize("   ") == "_"
    long = "x" * 300 + ".pdf"
    assert sanitize(long).endswith(".pdf") and len(sanitize(long)) <= 150


def test_files_linked_from_other_places(tmp_path):
    fake = FakeCanvas()
    course = FakeCourse(id=3, name="Bio", files_tab_hidden=True)
    for fid, name in [(1, "syllabus.pdf"), (2, "hw.pdf"), (3, "notes.pdf"), (4, "ann.pdf")]:
        course.files[fid] = FakeFile(fid, name, 1, name.encode())
    course.syllabus = '<a href="/courses/3/files/1/download">Syllabus</a>'
    course.assignments = [{"id": 1, "name": "HW 1", "description": '<a href="/files/2">hw</a>'}]
    course.pages = [
        {"title": "Week 1", "body": '<img src="https://canvas.test/courses/3/files/3/preview">'},
        {"title": "Dup", "body": '<a href="/courses/3/files/2">again</a>'},
    ]
    course.announcements = [
        {
            "id": 9,
            "title": "Welcome",
            "message": "",
            "attachments": [fake._file_json(course.files[4])],
        }
    ]
    course.discussions = [{"id": 8, "title": "Q&A", "message": '<a href="/files/999">gone</a>'}]
    fake.courses[3] = course
    plan, result = run(fake, tmp_path, sources=SOURCES)
    assert kinds(result) == {
        "Bio/Syllabus/syllabus.pdf": "new",
        "Bio/Assignments/HW 1/hw.pdf": "new",
        "Bio/Pages/Week 1/notes.pdf": "new",
        "Bio/Announcements/Welcome/ann.pdf": "new",
    }


def test_files_tab_wins_over_links(canvas, tmp_path):
    canvas.courses[10].assignments = [
        {"id": 1, "name": "HW", "description": '<a href="/courses/10/files/100">x</a>'}
    ]
    _, result = run(canvas, tmp_path, sources=SOURCES)
    assert "Signals & Systems/syllabus.pdf" in kinds(result)


def test_completed_courses_are_optional(canvas):
    canvas.courses[20] = FakeCourse(id=20, name="Chemistry", completed=True)
    with client_for(canvas) as client:
        assert [c.id for c in list_courses(client)] == [10]
        found = list_courses(client, include_completed=True)
        assert [(c.id, c.completed) for c in found] == [(20, True), (10, False)]


def test_file_links():
    from canvas_pull.collector import file_links

    html = '<a href="/courses/5/files/7/download"></a><img src="/files/8"><a href="/files/7">'
    assert file_links(html, 1) == [(7, 5), (8, 1)]
    assert file_links(None, 1) == []


def test_default_is_files_tab_only(canvas, tmp_path):
    canvas.courses[10].files[200] = FakeFile(200, "extra.pdf", 99, b"e")
    canvas.courses[10].pages = [{"title": "P", "body": '<a href="/files/200">x</a>'}]
    del canvas.courses[10].files[200]  # linked from a page only, not in the Files tab
    course = canvas.courses[10]
    course.announcements = [
        {
            "id": 1,
            "title": "A",
            "message": "",
            "attachments": [canvas._file_json(FakeFile(300, "ann.pdf", 1, b"a"))],
        }
    ]
    _, result = run(canvas, tmp_path)
    assert set(kinds(result)) == {
        "Signals & Systems/syllabus.pdf",
        "Signals & Systems/Week 1/lecture1.pptx",
    }


def test_modules_fallback_only_when_files_tab_unusable(canvas, tmp_path):
    canvas.courses[10].modules = {"Extra": [100]}
    _, result = run(canvas, tmp_path)
    assert not any("Modules" in p for p in kinds(result))  # Files tab works: no fallback


def test_modules_items_come_inline(tmp_path):
    fake = FakeCanvas()
    course = FakeCourse(id=5, name="Hidden", files_tab_hidden=True)
    course.files[7] = FakeFile(7, "notes.pdf", 1, b"n")
    course.modules = {"Week 1": [7]}
    fake.courses[5] = course
    _, result = run(fake, tmp_path)
    assert kinds(result) == {"Hidden/Modules/Week 1/notes.pdf": "new"}
    assert not any(r.endswith("/items") for r in fake.requests)


def test_modules_items_fallback_when_not_inline(tmp_path):
    fake = FakeCanvas()
    course = FakeCourse(id=5, name="Hidden", files_tab_hidden=True, module_items_inline=False)
    course.files[7] = FakeFile(7, "notes.pdf", 1, b"n")
    course.modules = {"Week 1": [7]}
    fake.courses[5] = course
    _, result = run(fake, tmp_path)
    assert kinds(result) == {"Hidden/Modules/Week 1/notes.pdf": "new"}


def test_scan_courses_parallel_keeps_order(tmp_path):
    from canvas_pull.collector import scan_courses

    fake = FakeCanvas()
    for i in range(1, 13):
        c = FakeCourse(id=i, name=f"Course {i:02d}")
        c.files[i * 100] = FakeFile(i * 100, "a.pdf", 1, b"x")
        fake.courses[i] = c
    with client_for(fake) as client:
        courses = list_courses(client)
        scans = scan_courses(client, courses, course_dirnames(courses), workers=6)
    assert [s.course.id for s in scans] == [c.id for c in courses]
    assert all(len(s.files) == 1 and not s.error for s in scans)


def test_client_caps_concurrent_requests():
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    lock, live, peak = threading.Lock(), [0], [0]

    def handler(request):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.02)
        with lock:
            live[0] -= 1
        return httpx.Response(200, json={})

    client = CanvasClient(BASE, "t", transport=httpx.MockTransport(handler), max_concurrency=3)
    with ThreadPoolExecutor(12) as pool:
        list(pool.map(lambda _: client.get("users/self"), range(24)))
    assert peak[0] <= 3
