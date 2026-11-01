"""Run `canvas-pull sync --notify` on a schedule.

macOS: a launchd user agent in ~/Library/LaunchAgents.
Linux: a systemd user timer in ~/.config/systemd/user.
A Mac that was asleep at the scheduled time runs the missed sync when it wakes.
"""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

LABEL = "io.github.gxxxr-111.canvas-pull"
UNIT = "canvas-pull"
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class ScheduleError(Exception):
    pass


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


@dataclass
class Spec:
    times: list[tuple[int, int]]  # (hour, minute) every day
    every: int | None = None  # seconds; used instead of times

    def describe(self) -> str:
        if self.every:
            mins = self.every // 60
            return f"every {mins // 60}h" if mins % 60 == 0 else f"every {mins}m"
        return "daily at " + ", ".join(f"{h:02d}:{m:02d}" for h, m in self.times)


def parse_spec(at: Sequence[str], every: str | None) -> Spec:
    if every and at:
        raise ScheduleError("Use either --at or --every, not both.")
    if every:
        m = re.fullmatch(r"\s*(\d+)\s*([mh])\s*", every.lower())
        if not m:
            raise ScheduleError("--every looks like 30m or 6h.")
        seconds = int(m[1]) * (60 if m[2] == "m" else 3600)
        if seconds < 15 * 60:
            raise ScheduleError("Please sync at most every 15 minutes.")
        return Spec(times=[], every=seconds)
    times = []
    for value in at or ["09:00"]:
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
        if not m or int(m[1]) > 23 or int(m[2]) > 59:
            raise ScheduleError(f"Invalid time {value!r}; use HH:MM, e.g. 08:30.")
        times.append((int(m[1]), int(m[2])))
    return Spec(times=sorted(set(times)))


def command() -> list[str]:
    """How the scheduler should start canvas-pull: the same install that is running now."""
    argv0 = Path(sys.argv[0])
    if argv0.name == "canvas-pull" and argv0.exists():
        return [str(argv0.resolve()), "sync", "--notify"]
    return [sys.executable, "-m", "canvas_pull", "sync", "--notify"]


def log_file() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Logs/canvas-pull.log"
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return state / "canvas-pull/canvas-pull.log"


# -- macOS ---------------------------------------------------------------------


def plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def build_plist(spec: Spec, cmd: list[str], workdir: Path, log: Path) -> dict:
    plist: dict = {
        "Label": LABEL,
        "ProgramArguments": cmd,
        "WorkingDirectory": str(workdir),
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "EnvironmentVariables": {"PATH": DEFAULT_PATH, "PYTHONUNBUFFERED": "1"},
        "ProcessType": "Background",
        "RunAtLoad": False,
    }
    if spec.every:
        plist["StartInterval"] = spec.every
    else:
        plist["StartCalendarInterval"] = [{"Hour": h, "Minute": m} for h, m in spec.times]
    return plist


# -- Linux ---------------------------------------------------------------------


def systemd_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "systemd/user"


def build_systemd(spec: Spec, cmd: list[str], workdir: Path, log: Path) -> tuple[str, str]:
    service = (
        "[Unit]\nDescription=Sync Canvas course files\n"
        "Wants=network-online.target\nAfter=network-online.target\n\n"
        "[Service]\nType=oneshot\n"
        f"WorkingDirectory={workdir}\n"
        f"ExecStart={shlex.join(cmd)}\n"
        f"StandardOutput=append:{log}\nStandardError=append:{log}\n"
    )
    if spec.every:
        when = f"OnBootSec=5min\nOnUnitActiveSec={spec.every}s\n"
    else:
        when = "".join(f"OnCalendar=*-*-* {h:02d}:{m:02d}:00\n" for h, m in spec.times)
    timer = (
        f"[Unit]\nDescription=Run canvas-pull ({spec.describe()})\n\n"
        f"[Timer]\n{when}Persistent=true\n\n[Install]\nWantedBy=timers.target\n"
    )
    return service, timer


# -- public --------------------------------------------------------------------


def _check(proc: subprocess.CompletedProcess[str], what: str) -> None:
    if proc.returncode != 0:
        raise ScheduleError(f"{what} failed: {(proc.stderr or proc.stdout).strip()}")


def enable(spec: Spec, *, workdir: Path, run: Runner = _run) -> Path:
    """Install (or replace) the schedule. Returns the file written."""
    cmd, log = command(), log_file()
    log.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        path = plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        domain = f"gui/{os.getuid()}"
        run(["launchctl", "bootout", domain, str(path)])  # ignore "not loaded"
        path.write_bytes(plistlib.dumps(build_plist(spec, cmd, workdir, log)))
        _check(run(["launchctl", "bootstrap", domain, str(path)]), "launchctl bootstrap")
        return path
    if sys.platform.startswith("linux"):
        d = systemd_dir()
        d.mkdir(parents=True, exist_ok=True)
        service, timer = build_systemd(spec, cmd, workdir, log)
        (d / f"{UNIT}.service").write_text(service, encoding="utf-8")
        (d / f"{UNIT}.timer").write_text(timer, encoding="utf-8")
        _check(run(["systemctl", "--user", "daemon-reload"]), "systemctl daemon-reload")
        _check(run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"]), "systemctl enable")
        return d / f"{UNIT}.timer"
    raise ScheduleError(
        "Scheduling is supported on macOS and Linux. On Windows use Task Scheduler."
    )


def disable(*, run: Runner = _run) -> bool:
    """Remove the schedule. Returns False if none was installed."""
    if sys.platform == "darwin":
        path = plist_path()
        if not path.exists():
            return False
        run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)])
        path.unlink()
        return True
    if sys.platform.startswith("linux"):
        d = systemd_dir()
        timer = d / f"{UNIT}.timer"
        if not timer.exists():
            return False
        run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"])
        timer.unlink()
        (d / f"{UNIT}.service").unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"])
        return True
    return False


def current() -> str | None:
    """Human-readable description of the installed schedule, or None."""
    if sys.platform == "darwin" and plist_path().exists():
        data = plistlib.loads(plist_path().read_bytes())
        if "StartInterval" in data:
            return Spec(times=[], every=int(data["StartInterval"])).describe()
        cal = data.get("StartCalendarInterval", [])
        cal = cal if isinstance(cal, list) else [cal]
        return Spec(times=[(c.get("Hour", 0), c.get("Minute", 0)) for c in cal]).describe()
    timer = systemd_dir() / f"{UNIT}.timer"
    if sys.platform.startswith("linux") and timer.exists():
        m = re.search(r"Description=Run canvas-pull \((.*)\)", timer.read_text())
        return m[1] if m else "installed"
    return None


def run_now(*, run: Runner = _run) -> None:
    """Trigger the scheduled job once, as the scheduler would."""
    if sys.platform == "darwin":
        target = f"gui/{os.getuid()}/{LABEL}"
        _check(run(["launchctl", "kickstart", target]), "launchctl kickstart")
    elif sys.platform.startswith("linux"):
        _check(run(["systemctl", "--user", "start", f"{UNIT}.service"]), "systemctl start")
    else:
        raise ScheduleError("No schedule support on this platform.")
