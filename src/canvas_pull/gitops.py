"""Optional: commit each sync in the save directory so `git log` shows history."""

from __future__ import annotations

import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .manifest import STATE_DIR
from .sync import Change

GITIGNORE = f"{STATE_DIR}/tmp/\n{STATE_DIR}/history/\n.DS_Store\n"


class GitError(Exception):
    pass


def _git(save_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(save_dir), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc


def commit_sync(save_dir: Path, changes: list[Change]) -> str | None:
    """Commit everything in save_dir. Returns the commit summary, or None if nothing changed."""
    if shutil.which("git") is None:
        raise GitError("git is not installed")
    if not (save_dir / ".git").exists():
        _git(save_dir, "init", "-q")
    ignore = save_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text(GITIGNORE, encoding="utf-8")

    _git(save_dir, "add", "-A")
    if not _git(save_dir, "status", "--porcelain").stdout.strip():
        return None

    counts = Counter(str(c.kind) for c in changes)
    summary = "sync: " + (", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "update")
    body = "\n".join(
        f"{c.kind}: {c.old_path} -> {c.path}"
        if c.old_path and c.old_path != c.path
        else f"{c.kind}: {c.path}"
        for c in changes
    )
    _git(save_dir, "commit", "-q", "-m", summary, "-m", body or "metadata update")
    return summary
