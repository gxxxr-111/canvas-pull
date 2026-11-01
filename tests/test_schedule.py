import plistlib
import subprocess
from pathlib import Path

import pytest

from canvas_pull import schedule as sched
from canvas_pull.manifest import SyncLocked, sync_lock
from canvas_pull.notify import describe
from canvas_pull.sync import Change, Kind


def test_parse_spec():
    assert sched.parse_spec([], None).times == [(9, 0)]
    assert sched.parse_spec(["20:15", "8:00"], None).describe() == "daily at 08:00, 20:15"
    assert sched.parse_spec([], "6h").describe() == "every 6h"
    assert sched.parse_spec([], "90m").describe() == "every 90m"
    for at, every in [(["25:00"], None), ([], "5m"), ([], "soon"), (["08:00"], "1h")]:
        with pytest.raises(sched.ScheduleError):
            sched.parse_spec(at, every)


def test_build_plist():
    spec = sched.parse_spec(["08:30"], None)
    plist = sched.build_plist(spec, ["/bin/canvas-pull", "sync"], Path("/w"), Path("/l.log"))
    assert plist["StartCalendarInterval"] == [{"Hour": 8, "Minute": 30}]
    assert plist["ProgramArguments"] == ["/bin/canvas-pull", "sync"]
    plistlib.dumps(plist)  # serializable
    every = sched.build_plist(sched.parse_spec([], "2h"), [], Path("/w"), Path("/l"))
    assert every["StartInterval"] == 7200 and "StartCalendarInterval" not in every


def test_build_systemd():
    service, timer = sched.build_systemd(
        sched.parse_spec(["07:00", "19:00"], None), ["/x y/cp", "sync"], Path("/w"), Path("/l")
    )
    assert "ExecStart='/x y/cp' sync" in service
    assert "OnCalendar=*-*-* 07:00:00\nOnCalendar=*-*-* 19:00:00" in timer
    assert "Persistent=true" in timer


def test_enable_disable_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(sched.sys, "platform", "darwin")
    monkeypatch.setattr(sched.Path, "home", lambda: tmp_path)
    calls = []

    def fake_run(cmd):
        calls.append(cmd[:2])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    path = sched.enable(sched.parse_spec(["09:00"], None), workdir=tmp_path, run=fake_run)
    assert path.exists() and ["launchctl", "bootstrap"] in calls
    assert sched.current() == "daily at 09:00"
    assert sched.disable(run=fake_run) and not path.exists()
    assert sched.current() is None and not sched.disable(run=fake_run)


def test_enable_reports_launchctl_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(sched.sys, "platform", "darwin")
    monkeypatch.setattr(sched.Path, "home", lambda: tmp_path)

    def failing(cmd):
        return subprocess.CompletedProcess(cmd, 5, "", "Bootstrap failed: 5")

    with pytest.raises(sched.ScheduleError, match="Bootstrap failed"):
        sched.enable(sched.parse_spec([], None), workdir=tmp_path, run=failing)


def test_describe_changes():
    changes = [
        Change(Kind.NEW, "a/1", "Algo"),
        Change(Kind.NEW, "a/2", "Algo"),
        Change(Kind.DELETED, "o/1", "OS"),
    ]
    assert describe(changes) == "Algo: 2 new · OS: 1 deleted"
    many = [Change(Kind.NEW, f"c{i}/f", f"Course {i}") for i in range(30)]
    assert len(describe(many, limit=40)) == 40


def test_sync_lock(tmp_path):
    with sync_lock(tmp_path), pytest.raises(SyncLocked), sync_lock(tmp_path):
        pass
    with sync_lock(tmp_path):  # released again
        pass
