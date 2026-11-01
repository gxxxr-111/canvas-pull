"""Command-line interface."""

from __future__ import annotations

from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__, notify
from . import schedule as sched
from .api import AuthError, CanvasClient, CanvasError
from .collector import CourseScan, course_dirnames, list_courses, scan_course
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
from .sync import Change, Kind, execute, make_plan

app = typer.Typer(
    help="Sync files from your active Canvas courses and see what changed.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err = Console(stderr=True)

STYLE = {
    Kind.NEW: ("+", "green"),
    Kind.MODIFIED: ("~", "yellow"),
    Kind.MOVED: ("→", "cyan"),
    Kind.DELETED: ("-", "red"),
    Kind.RESTORED: ("↺", "blue"),
}


def _fail(message: str) -> typer.Exit:
    err.print(f"[red]Error:[/] {message}")
    return typer.Exit(1)


def _load(require_token: bool = True) -> Config:
    try:
        return load_config(require_token=require_token)
    except ConfigError as exc:
        raise _fail(str(exc)) from None


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
def init() -> None:
    """Set your Canvas URL, save folder and access token."""
    try:
        current = load_config(require_token=False)
    except ConfigError:
        current = None

    console.print(
        "Create a token in Canvas: [bold]Account → Settings → New Access Token[/].\n"
        "The token is stored in your system keychain.\n"
    )
    website = normalize_website(
        typer.prompt("Canvas URL", default=current.website if current else "oc.sjtu.edu.cn")
    )
    save_dir = (
        Path(
            typer.prompt(
                "Save files to", default=str(current.save_dir) if current else DEFAULT_SAVE_DIR
            )
        )
        .expanduser()
        .resolve()
    )
    token = typer.prompt("Access token", hide_input=True).strip()

    try:
        with CanvasClient(website, token) as client:
            me = client.get("users/self")
    except CanvasError as exc:
        raise _fail(f"Could not sign in to {website}: {exc}") from None

    cfg = current or Config(website=website, save_dir=save_dir)
    cfg.website, cfg.save_dir, cfg.token = website, save_dir, token
    where = save_config(cfg)
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
) -> None:
    """List the courses that would be synced."""
    cfg = _load()
    with_completed = cfg.include_completed if completed is None else completed
    try:
        with CanvasClient(cfg.website, cfg.token) as client:
            found = list_courses(client, include_past=all_, include_completed=with_completed)
    except CanvasError as exc:
        raise _fail(str(exc)) from None

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


def _scan(
    client: CanvasClient, cfg: Config, only: list[str], all_courses: bool, completed: bool
) -> list[CourseScan]:
    found = list_courses(client, include_past=all_courses, include_completed=completed)
    found = [c for c in found if not _matches(c, cfg.exclude)]
    if only:
        found = [c for c in found if _matches(c, only)]
    if not found:
        return []
    names = course_dirnames(found)
    scans: list[CourseScan] = []
    with console.status("") as status:
        for i, course in enumerate(found, 1):
            status.update(f"Scanning [{i}/{len(found)}] {course.label}")
            scans.append(scan_course(client, course, names[course.id], cfg.sources))
    return scans


def _print_changes(changes: list[Change]) -> None:
    by_course: dict[str, list[Change]] = defaultdict(list)
    for c in changes:
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
) -> None:
    """Download new and changed files from your active courses."""
    if not console.is_terminal and not dry_run:
        console.print(f"--- {now_iso()} canvas-pull sync")  # separator in scheduled-run logs
    try:
        cfg = load_config()
    except ConfigError as exc:
        _alert(notify_, f"Sync failed: {exc}")
        raise _fail(str(exc)) from None
    use_git = cfg.git if git is None else git
    with_completed = cfg.include_completed if completed is None else completed
    manifest = Manifest.load(cfg.save_dir)

    try:
        lock = nullcontext() if dry_run else sync_lock(cfg.save_dir)
        with lock, CanvasClient(cfg.website, cfg.token) as client:
            scans = _scan(client, cfg, course or [], all_courses, with_completed)
            if not scans:
                console.print("No matching active courses.")
                return
            plan = make_plan(scans, manifest, cfg.save_dir)

            for s in scans:
                if s.error:
                    err.print(f"[yellow]![/] Skipped {s.course.label}: {s.error}")
                elif s.files_tab_hidden:
                    console.print(
                        f"[dim]{s.course.label}: Files tab hidden; "
                        "found files through modules, assignments and pages.[/]"
                    )

            if dry_run:
                _print_changes(plan.changes)
                console.print(f"\n{_summary(plan.changes)} · {cfg.save_dir}")
                if plan.locked:
                    console.print(f"[dim]{len(plan.locked)} locked file(s) not yet available.[/]")
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
                finally:
                    manifest.save()
    except SyncLocked:
        raise _fail("Another sync is already running for this folder.") from None
    except AuthError as exc:
        _alert(notify_, "Sync failed: Canvas rejected your token. Run `canvas-pull init`.")
        raise _fail(str(exc)) from None
    except CanvasError as exc:
        if exc.status is not None:  # stay quiet when simply offline
            _alert(notify_, f"Sync failed: {exc}")
        raise _fail(str(exc)) from None

    _print_changes(result.applied)
    for change, reason in result.failed:
        err.print(f"[red]✗[/] {change.path}: {reason}")
    console.print(f"\n{_summary(result.applied)} · {cfg.save_dir}")
    if plan.locked:
        console.print(f"[dim]{len(plan.locked)} locked file(s) not yet available.[/]")

    if result.applied:
        record = {"time": now_iso(), "changes": [c.to_dict() for c in result.applied]}
        append_log(cfg.save_dir, record)
    if use_git:
        try:
            summary = commit_sync(cfg.save_dir, result.applied)
            if summary:
                console.print(f"[dim]git: {summary}[/]")
        except GitError as exc:
            err.print(f"[yellow]git commit skipped:[/] {exc}")
    if result.applied or result.failed:
        message = notify.describe(result.applied)
        if result.failed:
            message = f"{message} · {len(result.failed)} failed".lstrip(" ·")
        _alert(notify_, message)
    if result.failed:
        raise typer.Exit(1)


@app.command()
def status(
    course: Annotated[
        list[str] | None, typer.Option("--course", "-c", help="Only these courses.")
    ] = None,
    completed: Annotated[
        bool | None,
        typer.Option("--completed/--no-completed", help="Include completed (concluded) courses."),
    ] = None,
) -> None:
    """Show what changed on Canvas since the last sync (downloads nothing)."""
    sync(dry_run=True, course=course, all_courses=False, completed=completed, git=False)


@app.command()
def log(
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many syncs to show.")] = 10,
) -> None:
    """Show recent syncs and what each one changed."""
    cfg = _load(require_token=False)
    records = read_log(cfg.save_dir, limit)
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
def show_config() -> None:
    """Show the settings in use (token hidden)."""
    cfg = _load(require_token=False)
    token = "set" if cfg.token else "missing"
    table = Table(box=None, show_header=False)
    for key, value in [
        ("config file", str(default_config_file())),
        ("website", cfg.website),
        ("save_dir", str(cfg.save_dir)),
        ("token", token),
        ("concurrency", str(cfg.concurrency)),
        ("keep_history", str(cfg.keep_history)),
        ("git", str(cfg.git)),
        ("exclude", ", ".join(cfg.exclude) or "-"),
        ("include_completed", str(cfg.include_completed)),
        ("sources", ", ".join(cfg.sources)),
    ]:
        table.add_row(f"[bold]{key}[/]", value)
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
    _load()  # fail now, not silently at 9am
    try:
        spec = sched.parse_spec(at or [], every)
        path = sched.enable(spec, workdir=Path.cwd())
    except sched.ScheduleError as exc:
        raise _fail(str(exc)) from None
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
