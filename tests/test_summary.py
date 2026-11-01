from datetime import UTC, datetime

import pytest

from canvas_pull.summary import parse_since, summarize

NOW = datetime(2026, 11, 1, tzinfo=UTC)
START = datetime(2026, 10, 25, tzinfo=UTC)


def rec(day: int, *changes: dict) -> dict:
    return {"time": f"2026-10-{day:02d}T08:00:00+00:00", "changes": list(changes)}


def ch(kind, path, old=None, course="Algo"):
    d = {"kind": kind, "path": path, "course": course}
    if old:
        d["old_path"] = old
    return d


def net(records):
    return [(n.kind, n.path, n.old_path) for n in summarize(records, START)]


def test_parse_since():
    assert parse_since("7d", NOW) == START
    assert parse_since("24h", NOW) == datetime(2026, 10, 31, tzinfo=UTC)
    assert parse_since("2026-10-01T00:00:00+00:00") == datetime(2026, 10, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        parse_since("last week")


def test_old_records_ignored():
    assert net([rec(1, ch("new", "a.pdf"))]) == []


def test_new_then_modified_then_moved_is_new():
    records = [
        rec(26, ch("new", "a.pdf")),
        rec(27, ch("modified", "a.pdf")),
        rec(28, ch("moved", "b.pdf", "a.pdf")),
    ]
    assert net(records) == [("new", "b.pdf", None)]


def test_new_then_deleted_vanishes():
    assert net([rec(26, ch("new", "a.pdf")), rec(27, ch("deleted", "a.pdf"))]) == []


def test_moves_chain_to_first_name():
    records = [rec(26, ch("moved", "b", "a")), rec(27, ch("moved", "c", "b"))]
    assert net(records) == [("moved", "c", "a")]
    assert net(records + [rec(28, ch("moved", "a", "c"))]) == []


def test_modified_then_moved_stays_modified():
    records = [rec(26, ch("modified", "a")), rec(27, ch("moved", "b", "a"))]
    assert net(records) == [("modified", "b", "a")]


def test_sorted_by_course_then_kind():
    records = [
        rec(26, ch("deleted", "x", course="OS"), ch("new", "z", course="OS")),
        rec(27, ch("new", "y", course="Algo")),
    ]
    assert [(n.course, n.kind) for n in summarize(records, START)] == [
        ("Algo", "new"),
        ("OS", "new"),
        ("OS", "deleted"),
    ]
