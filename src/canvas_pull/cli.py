"""Command-line interface.

Every data command accepts --json: stdout then carries exactly one JSON object (also on
errors), and human-oriented output is switched off. Exit codes are stable; see `ExitCode`.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from contextlib import nullcontext
from enum import IntEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__, notify
from . import schedule as sched
from .api import AuthError, CanvasClient, CanvasError
from .collector import CourseScan, RemoteFile, course_dirnames, list_courses, scan_courses
from .config import (
    DEFAULT_SAVE_DIR,
    Config,
    ConfigError,
    default_config_file,
    load_config,
    normalize_website,
    save_config,
)
from .gitops import GitError, commit_sync
from .manifest import Manifest, SyncLocked, append_log, now_iso, read_log, sync_lock
from .models import Course
from .summary import parse_since, summarize
from .sync import Change, Kind, execute, make_plan

app = typer.Typer(
    help="Sync files from your active Canvas courses and see what changed.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console(highlight=False)
err = Console(stderr=True, highlight=False)


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1  # network or Canvas error
    USAGE = 2  # bad command-line arguments (Typer's own code)
    CONFIG = 3  # not configured, or invalid settings
    AUTH = 4  # Canvas rejected the token (invalid or expired)
    PARTIAL = 5  # finished, but some downloads or courses failed
    BUSY = 6  # another sync is running on the same folder


_ERROR_TYPES = {
    ExitCode.ERROR: "canvas",
    ExitCode.CONFIG: "config",
    ExitCode.AUTH: "auth",
    ExitCode.BUSY: "busy",
}

JsonOpt = Annotated[
    bool,
    typer.Option("--json", help="Print one JSON object on stdout (for scripts and AI agents)."),
]

_json_mode = False

STYLE = {
    Kind.NEW: ("+", "green"),
    Kind.MODIFIED: ("~", "yellow"),
    Kind.MOVED: ("→", "cyan"),
    Kind.DELETED: ("-", "red"),
    Kind.RESTORED: ("↺", "blue"),
}


def _start(json_: bool) -> None:
    """Called first by every command: in JSON mode, silence human output."""
    global _json_mode
    _json_mode = json_
    console.quiet = json_


def _emit(data: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def _fail(message: str, code: ExitCode = ExitCode.ERROR) -> typer.Exit:
    if _json_mode:
        kind = _ERROR_TYPES.get(code, "error")
        _emit({"ok": False, "error": {"type": kind, "message": message, "exit_code": int(code)}})
    else:
        err.print(f"[red]Error:[/] {message}")
    return typer.Exit(int(code))


def _pretty(path: Path) -> str:
    """Show paths under the home folder as ~/..."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def _load(require_token: bool = True) -> Config:
    try:
        return load_config(require_token=require_token)
    except ConfigError as exc:
        raise _fail(str(exc), ExitCode.CONFIG) from None


def _canvas_failure(exc: CanvasError) -> typer.Exit:
    code = ExitCode.AUTH if isinstance(exc, AuthError) else ExitCode.ERROR
    return _fail(str(exc), code)


def _matches(course: Course, patterns: list[str]) -> bool:
    label = course.label.lower()
    code = (course.course_code or "").lower()
    return any(p == str(course.id) or p.lower() in label or p.lower() in code for p in patterns)


def _version(value: bool) -> None:
    if value:
        console.print(f"canvas-pull {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    pass


@app.command()
def init(
    url: Annotated[
        str | None, typer.Option("--url", help="Canvas site, e.g. oc.sjtu.edu.cn.")
    ] = None,
    save_dir_opt: Annotated[
        Path | None, typer.Option("--save-dir", help="Folder to save files in.")
    ] = None,
    token_stdin: Annotated[
        bool, typer.Option("--token-stdin", help="Read the access token from stdin.")
    ] = False,
    no_input: Annotated[
        bool, typer.Option("--no-input", help="Never prompt; fail if something is missing.")
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Set your Canvas URL, save folder and access token.

    Non-interactive: canvas-pull init --url oc.sjtu.edu.cn --save-dir ~/Canvas --token-stdin
    (the token can also come from CANVAS_TOKEN).
    """
    _start(json_)
    interactive = not (no_input or json_ or token_stdin)
    try:
        current = load_config(require_token=False)
    except ConfigError:
        current = None

    def ask(label: str, default: str | None, hide: bool = False) -> str:
        if not interactive:
            if default:
                return default
            raise _fail(f"Missing {label}; pass it as an option.", ExitCode.CONFIG)
        return typer.prompt(label, default=default, hide_input=hide)

    if interactive:
        console.print(
            "Create a token in Canvas: [bold]Account → Settings → New Access Token[/].\n"
            "The token is stored in your system keychain.\n"
        )
    website_raw = url or ask(
        "Canvas URL", current.website if current else ("oc.sjtu.edu.cn" if interactive else None)
    )
    try:
        website = normalize_website(website_raw)
    except ConfigError as exc:
        raise _fail(str(exc), ExitCode.CONFIG) from None
    default_dir = str(current.save_dir) if current else DEFAULT_SAVE_DIR
    save_dir = Path(save_dir_opt or ask("Save files to", default_dir)).expanduser().resolve()

    if token_stdin:
        token = sys.stdin.readline().strip()
    else:
        token = os.environ.get("CANVAS_TOKEN", "") or ask("Access token", None, hide=True)
    token = token.strip()
    if not token:
        raise _fail("No access token given.", ExitCode.CONFIG)

    try:
        with CanvasClient(website, token) as client:
            me = client.get("users/self")
    except CanvasError as exc:
        code = ExitCode.AUTH if isinstance(exc, AuthError) else ExitCode.ERROR
        raise _fail(f"Could not sign in to {website}: {exc}", code) from None

    cfg = current or Config(website=website, save_dir=save_dir)
    cfg.website, cfg.save_dir, cfg.token = website, save_dir, token
    where = save_config(cfg)
    if json_:
        _emit(
            {
                "ok": True,
                "user": me.get("name"),
                "website": website,
                "save_dir": str(save_dir),
                "config_file": str(default_config_file()),
                "token_stored_in": where,
            }
        )
        return
    console.print(f"[green]✓[/] Signed in as [bold]{me.get('name', '?')}[/].")
    console.print(f"Settings: {default_config_file()}  ·  token: {where}")
    console.print("Next: [bold]canvas-pull sync[/]")


@app.command()
def courses(
    all_: Annotated[bool, typer.Option("--all", help="Include courses whose term ended.")] = False,
    completed: Annotated[
        bool | None,
        typer.Option("--completed/--no-completed", help="Include completed (concluded) courses."),
    ] = None,
    json_: JsonOpt = False,
) -> None:
    """List the courses that would be synced."""
    _start(json_)
    cfg = _load()
    with_completed = cfg.include_completed if completed is None else completed
    try:
        with _client(cfg) as client:
            found = list_courses(client, include_past=all_, include_completed=with_completed)
    except CanvasError as exc:
        raise _canvas_failure(exc) from None

    if json_:
        _emit(
            {
                "ok": True,
                "courses": [
                    {
                        "id": c.id,
                        "name": c.label,
                        "code": c.course_code,
                        "term": c.term.name if c.term else None,
                        "completed": c.completed,
                        "excluded": _matches(c, cfg.exclude),
                    }
                    for c in found
                ],
            }
        )
        return
    table = Table(box=None, header_style="bold")
    table.add_column("ID", justify="right")
    table.add_column("Course")
    table.add_column("Term")
    table.add_column("")
    for c in found:
        notes = ["[dim]completed[/]"] if c.completed else []
        if _matches(c, cfg.exclude):
            notes.append("[dim]excluded[/]")
        term = (c.term.name if c.term else "") or ""
        table.add_row(str(c.id), c.label, term, " ".join(notes))
    console.print(table)


def _client(cfg: Config) -> CanvasClient:
    return CanvasClient(cfg.website, cfg.token, max_concurrency=cfg.scan_concurrency)


def _scan(
    client: CanvasClient,
    cfg: Config,
    manifest: Manifest,
    only: list[str],
    all_courses: bool,
    completed: bool,
    refresh: bool,
) -> tuple[list[CourseScan], int]:
    """Scan matching courses in parallel. Returns (scans, number of archived courses skipped)."""
    found = list_courses(client, include_past=all_courses, include_completed=completed)
    found = [c for c in found if not _matches(c, cfg.exclude)]
    if only:
        found = [c for c in found if _matches(c, only)]
    # Folder names come from the full list so skipping courses never renames a folder.
    names = course_dirnames(found)
    todo = [
        c for c in found if refresh or not (c.completed and manifest.is_archived(c.id, cfg.sources))
    ]
    if not todo:
        return [], len(found)
    done = 0
    with console.status(f"Scanning {len(todo)} course(s)…") as status:

        def progress(scan: CourseScan) -> None:
            nonlocal done
            done += 1
            status.update(f"Scanning [{done}/{len(todo)}] {scan.course.label}")

        scans = scan_courses(
            client, todo, names, cfg.sources, workers=cfg.scan_concurrency, on_done=progress
        )
    return scans, len(found) - len(todo)


_KIND_ORDER = {Kind.NEW: 0, Kind.MODIFIED: 1, Kind.MOVED: 2, Kind.RESTORED: 3, Kind.DELETED: 4}


def _sorted(changes: list[Change]) -> list[Change]:
    return sorted(changes, key=lambda c: (c.course.lower(), _KIND_ORDER[c.kind], c.path.lower()))


def _print_changes(changes: list[Change]) -> None:
    by_course: dict[str, list[Change]] = defaultdict(list)
    for c in _sorted(changes):
        by_course[c.course].append(c)
    for course, items in by_course.items():
        console.print(f"\n[bold]{course}[/]")
        for c in items:
            mark, color = STYLE[c.kind]
            shown = c.path.split("/", 1)[-1]
            if c.old_path and c.old_path != c.path:
                shown = f"{c.old_path.split('/', 1)[-1]} → {shown}"
            console.print(f"  [{color}]{mark} {c.kind:<8}[/] {shown}")


def _summary(changes: list[Change]) -> str:
    counts: dict[Kind, int] = defaultdict(int)
    for c in changes:
        counts[c.kind] += 1
    parts = [f"[{STYLE[k][1]}]{n} {k}[/]" for k, n in counts.items()]
    return ", ".join(parts) if parts else "no changes"


def _counts(changes: list[Change]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for c in changes:
        counts[str(c.kind)] += 1
    return dict(counts)


def _change_json(c: Change, save_dir: Path) -> dict[str, Any]:
    d = c.to_dict()
    d["local_path"] = str(save_dir / c.path)
    return d


def _sync_json(
    *,
    dry_run: bool,
    save_dir: Path,
    changes: list[Change],
    failed: list[tuple[Change, str]] = (),  # type: ignore[assignment]
    locked: list[RemoteFile] = (),  # type: ignore[assignment]
    scans: list[CourseScan] = (),  # type: ignore[assignment]
    archived: int = 0,
) -> dict[str, Any]:
    skipped = [{"course": s.course.label, "error": s.error} for s in scans if s.error]
    return {
        "ok": not failed and not skipped,
        "dry_run": dry_run,
        "save_dir": str(save_dir),
        "summary": _counts(changes),
        "changes": [_change_json(c, save_dir) for c in _sorted(changes)],
        "failed": [{"course": c.course, "path": c.path, "error": e} for c, e in failed],
        "locked": [{"course": rf.course.label, "path": rf.path} for rf in locked],
        "skipped_courses": skipped,
        "archived_courses_skipped": archived,
    }


def _alert(enabled: bool, message: str) -> None:
    if enabled:
        notify.send(message)


@app.command()
def sync(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Only show what would change.")
    ] = False,
    course: Annotated[
        list[str] | None,
        typer.Option("--course", "-c", help="Only these courses (id or part of the name)."),
    ] = None,
    all_courses: Annotated[
        bool, typer.Option("--all-courses", help="Include courses whose term ended.")
    ] = False,
    completed: Annotated[
        bool | None,
        typer.Option("--completed/--no-completed", help="Include completed (concluded) courses."),
    ] = None,
    git: Annotated[
        bool | None, typer.Option("--git/--no-git", help="Commit the result in the save folder.")
    ] = None,
    notify_: Annotated[
        bool,
        typer.Option("--notify", help="Desktop notification when files change or sync fails."),
    ] = False,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Scan archived completed courses again.")
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Download new and changed files from your active courses."""
    _start(json_)
    if not console.is_terminal and not dry_run and not json_:
        console.print(f"--- {now_iso()} canvas-pull sync")  # separator in scheduled-run logs
    try:
        cfg = load_config()
    except ConfigError as exc:
        _alert(notify_, f"Sync failed: {exc}")
        raise _fail(str(exc), ExitCode.CONFIG) from None
    use_git = cfg.git if git is None else git
    with_completed = cfg.include_completed if completed is None else completed
    manifest = Manifest.load(cfg.save_dir)

    try:
        lock = nullcontext() if dry_run else sync_lock(cfg.save_dir)
        with lock, _client(cfg) as client:
            scans, archived = _scan(
                client, cfg, manifest, course or [], all_courses, with_completed, refresh
            )
            if archived:
                console.print(
                    f"[dim]Skipped {archived} completed course(s) already archived "
                    "(use --refresh to scan them again).[/]"
                )
            if not scans:
                if not archived:
                    console.print("No matching active courses.")
                if json_:
                    _emit(
                        _sync_json(
                            dry_run=dry_run, save_dir=cfg.save_dir, changes=[], archived=archived
                        )
                    )
                return
            plan = make_plan(scans, manifest, cfg.save_dir)

            for s in scans:
                if s.error:
                    if not json_:
                        err.print(f"[yellow]![/] Skipped {s.course.label}: {s.error}")
                elif s.files_tab_hidden:
                    console.print(
                        f"[dim]{s.course.label}: Files tab hidden; used Modules instead.[/]"
                    )
            scan_failed = any(s.error for s in scans)

            if dry_run:
                if json_:
                    _emit(
                        _sync_json(
                            dry_run=True,
                            save_dir=cfg.save_dir,
                            changes=plan.changes,
                            locked=plan.locked,
                            scans=scans,
                            archived=archived,
                        )
                    )
                else:
                    _print_changes(plan.changes)
                    console.print(f"\n{_summary(plan.changes)} · {_pretty(cfg.save_dir)}")
                    if plan.locked:
                        console.print(
                            f"[dim]{len(plan.locked)} locked file(s) not yet available.[/]"
                        )
                if scan_failed:
                    raise typer.Exit(int(ExitCode.PARTIAL))
                return

            total = sum(1 for c in plan.changes if c.needs_download)
            columns = (
                TextColumn("Downloading"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
            )
            with Progress(*columns, console=console, transient=True) as progress:
                task = progress.add_task("", total=total)
                try:
                    result = execute(
                        plan,
                        client=client,
                        manifest=manifest,
                        save_dir=cfg.save_dir,
                        keep_history=cfg.keep_history,
                        concurrency=cfg.concurrency,
                        on_progress=lambda _c: progress.advance(task),
                    )
                    failed_courses = {c.remote.course.id for c, _ in result.failed if c.remote}
                    for s in scans:
                        if s.course.completed and not s.error and s.course.id not in failed_courses:
                            manifest.mark_archived(s.course.id, cfg.sources)
                finally:
                    manifest.save()
    except SyncLocked:
        raise _fail("Another sync is already running for this folder.", ExitCode.BUSY) from None
    except AuthError as exc:
        _alert(notify_, "Sync failed: Canvas rejected your token. Run `canvas-pull init`.")
        raise _canvas_failure(exc) from None
    except CanvasError as exc:
        if exc.status is not None:  # stay quiet when simply offline
            _alert(notify_, f"Sync failed: {exc}")
        raise _canvas_failure(exc) from None

    if result.applied:
        record = {"time": now_iso(), "changes": [c.to_dict() for c in result.applied]}
        append_log(cfg.save_dir, record)
    git_summary = None
    if use_git:
        try:
            git_summary = commit_sync(cfg.save_dir, result.applied)
        except GitError as exc:
            if not json_:
                err.print(f"[yellow]git commit skipped:[/] {exc}")
    if result.applied or result.failed:
        message = notify.describe(result.applied)
        if result.failed:
            message = f"{message} · {len(result.failed)} failed".lstrip(" ·")
        _alert(notify_, message)

    if json_:
        data = _sync_json(
            dry_run=False,
            save_dir=cfg.save_dir,
            changes=result.applied,
            failed=result.failed,
            locked=plan.locked,
            scans=scans,
            archived=archived,
        )
        data["git_commit"] = git_summary
        _emit(data)
    else:
        _print_changes(result.applied)
        for change, reason in result.failed:
            err.print(f"[red]✗[/] {change.path}: {reason}")
        console.print(f"\n{_summary(result.applied)} · {_pretty(cfg.save_dir)}")
        if plan.locked:
            console.print(f"[dim]{len(plan.locked)} locked file(s) not yet available.[/]")
        if git_summary:
            console.print(f"[dim]git: {git_summary}[/]")
    if result.failed or scan_failed:
        raise typer.Exit(int(ExitCode.PARTIAL))


@app.command()
def status(
    course: Annotated[
        list[str] | None, typer.Option("--course", "-c", help="Only these courses.")
    ] = None,
    completed: Annotated[
        bool | None,
        typer.Option("--completed/--no-completed", help="Include completed (concluded) courses."),
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Scan archived completed courses again.")
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Show what changed on Canvas since the last sync (downloads nothing)."""
    sync(
        dry_run=True,
        course=course,
        all_courses=False,
        completed=completed,
        git=False,
        notify_=False,
        refresh=refresh,
        json_=json_,
    )


@app.command()
def log(
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many syncs to show.")] = 10,
    since: Annotated[
        str | None,
        typer.Option(
            "--since", "-s", help="Net summary since 7d, 24h, 2w, today or a date (2026-10-01)."
        ),
    ] = None,
    json_: JsonOpt = False,
) -> None:
    """Show recent syncs, or a merged summary with --since (e.g. --since 7d)."""
    _start(json_)
    cfg = _load(require_token=False)
    if since:
        try:
            start = parse_since(since)
        except ValueError as exc:
            raise _fail(str(exc), ExitCode.USAGE) from None
        net = summarize(read_log(cfg.save_dir, None), start)
        if json_:
            counts: dict[str, int] = defaultdict(int)
            for n in net:
                counts[n.kind] += 1
            _emit(
                {
                    "ok": True,
                    "since": start.isoformat(),
                    "save_dir": str(cfg.save_dir),
                    "summary": dict(counts),
                    "changes": [
                        {
                            "kind": n.kind,
                            "course": n.course,
                            "path": n.path,
                            **({"old_path": n.old_path} if n.old_path else {}),
                            "time": n.time,
                            "local_path": str(cfg.save_dir / n.path),
                        }
                        for n in net
                    ],
                }
            )
            return
        local = start.astimezone().strftime("%Y-%m-%d %H:%M")
        if not net:
            console.print(f"No changes since {local}.")
            return
        console.print(f"[bold]Changes since {local}[/]")
        by_course: dict[str, list] = defaultdict(list)
        for n in net:
            by_course[n.course].append(n)
        for course_name, items in by_course.items():
            tally_counts: dict[str, int] = defaultdict(int)
            for n in items:
                tally_counts[n.kind] += 1
            tally = ", ".join(f"{v} {k}" for k, v in tally_counts.items())
            console.print(f"\n[bold]{course_name}[/]  [dim]{tally}[/]")
            for n in items:
                mark, color = STYLE[Kind(n.kind)]
                shown = n.path.split("/", 1)[-1]
                if n.old_path:
                    shown = f"{n.old_path.split('/', 1)[-1]} → {shown}"
                console.print(f"  [{color}]{mark} {n.kind:<8}[/] {shown}")
        return

    records = read_log(cfg.save_dir, limit)
    if json_:
        _emit({"ok": True, "save_dir": str(cfg.save_dir), "syncs": list(reversed(records))})
        return
    if not records:
        console.print("No syncs recorded yet.")
        return
    for rec in reversed(records):
        console.print(f"\n[bold]{rec['time']}[/]")
        for c in rec["changes"]:
            kind = Kind(c["kind"])
            mark, color = STYLE[kind]
            path = c["path"] if "old_path" not in c else f"{c['old_path']} → {c['path']}"
            console.print(f"  [{color}]{mark} {kind:<8}[/] {path}")


@app.command("config")
def show_config(json_: JsonOpt = False) -> None:
    """Show the settings in use (token hidden)."""
    _start(json_)
    cfg = _load(require_token=False)
    values: dict[str, Any] = {
        "config_file": str(default_config_file()),
        "website": cfg.website,
        "save_dir": str(cfg.save_dir),
        "token": "set" if cfg.token else "missing",
        "concurrency": cfg.concurrency,
        "scan_concurrency": cfg.scan_concurrency,
        "keep_history": cfg.keep_history,
        "git": cfg.git,
        "exclude": cfg.exclude,
        "include_completed": cfg.include_completed,
        "sources": cfg.sources,
    }
    if json_:
        _emit({"ok": True, **values})
        return
    table = Table(box=None, show_header=False)
    for key, value in values.items():
        if key == "save_dir":
            value = _pretty(cfg.save_dir)
        elif isinstance(value, list):
            value = ", ".join(value) or "-"
        label = "config file" if key == "config_file" else key
        table.add_row(f"[bold]{label}[/]", str(value))
    console.print(table)


schedule_app = typer.Typer(
    help="Sync automatically on a schedule, with desktop notifications.",
    no_args_is_help=True,
)
app.add_typer(schedule_app, name="schedule")


@schedule_app.command("enable")
def schedule_enable(
    at: Annotated[
        list[str] | None,
        typer.Option("--at", help="Time of day, HH:MM. Repeat for several times. Default 09:00."),
    ] = None,
    every: Annotated[
        str | None, typer.Option("--every", help="Interval instead, e.g. 6h or 30m.")
    ] = None,
) -> None:
    """Install (or replace) the automatic sync."""
    _start(False)
    _load()  # fail now, not silently at 9am
    try:
        spec = sched.parse_spec(at or [], every)
        path = sched.enable(spec, workdir=Path.cwd())
    except sched.ScheduleError as exc:
        raise _fail(str(exc), ExitCode.CONFIG) from None
    cmd = sched.command()
    console.print(f"[green]✓[/] canvas-pull will sync [bold]{spec.describe()}[/].")
    console.print(f"[dim]Job: {path}\nCommand: {' '.join(cmd)}\nLog: {sched.log_file()}[/]")
    if "/.venv/" in cmd[0]:
        err.print(
            "[yellow]Note:[/] this points at a project virtualenv. For a stable path, install "
            "with `uv tool install canvas-pull` and run `canvas-pull schedule enable` again."
        )


@schedule_app.command("disable")
def schedule_disable() -> None:
    """Remove the automatic sync."""
    if sched.disable():
        console.print("[green]✓[/] Automatic sync removed.")
    else:
        console.print("No automatic sync was installed.")


@schedule_app.command("status")
def schedule_status(
    lines: Annotated[int, typer.Option("--lines", "-n", help="Log lines to show.")] = 15,
) -> None:
    """Show the schedule and the end of its log."""
    desc = sched.current()
    console.print(f"Schedule: [bold]{desc}[/]" if desc else "No automatic sync installed.")
    log_path = sched.log_file()
    if log_path.is_file():
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        console.print(f"[dim]{log_path}[/]")
        console.print("\n".join(tail), markup=False, highlight=False)


@schedule_app.command("run")
def schedule_run() -> None:
    """Trigger the scheduled sync once now (output goes to the log)."""
    try:
        sched.run_now()
    except sched.ScheduleError as exc:
        raise _fail(str(exc)) from None
    console.print(f"Started. Check with [bold]canvas-pull schedule status[/] ({sched.log_file()}).")
