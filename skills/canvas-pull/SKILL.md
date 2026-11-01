---
name: canvas-pull
description: Sync and inspect the user's Canvas LMS course files with the canvas-pull CLI. Use when the user asks what's new or changed in their courses, wants to download or sync course materials, find a lecture/homework file, summarize recently added slides, or review the week's course updates.
---

# canvas-pull

`canvas-pull` keeps a local copy of the user's Canvas course files and records what changed.
Always pass `--json` and read the JSON: stdout then holds exactly one JSON object, also on
errors.

## Before you start

1. Check it is installed: `canvas-pull --version`. If not, run it with `uvx canvas-pull ...`, or
   suggest `uv tool install canvas-pull`.
2. Check setup: `canvas-pull config --json`. If it exits with code 3 or `"token": "missing"`,
   the user must run `canvas-pull init` **themselves in a terminal**. The token prompt is
   hidden there.

**Never ask the user to paste their Canvas access token into the chat, and never put a token on
a command line.** If they do paste one, tell them to delete it in Canvas
(Account → Settings) and create a new one.

## Commands

| Goal | Command |
| --- | --- |
| What changed on Canvas (downloads nothing) | `canvas-pull status --json` |
| Download changes | `canvas-pull sync --json` |
| What changed recently (from the local log, no network) | `canvas-pull log --since 7d --json` (also `24h`, `2w`, `today`, `2026-10-01`) |
| List courses | `canvas-pull courses --json` |
| One course only | add `-c "<id or part of name>"` to `status` / `sync` |
| Include concluded courses | add `--completed` |

## JSON shapes

`status` / `sync`:

```json
{
  "ok": true,
  "dry_run": false,
  "save_dir": "/Users/me/Canvas",
  "summary": {"new": 2, "modified": 1},
  "changes": [
    {"kind": "new", "course": "Signals and Systems",
     "path": "Signals and Systems/Lectures/Lecture 03.pdf",
     "local_path": "/Users/me/Canvas/Signals and Systems/Lectures/Lecture 03.pdf"}
  ],
  "failed": [], "locked": [], "skipped_courses": [], "archived_courses_skipped": 0
}
```

- `kind` is one of `new`, `modified`, `moved` (has `old_path`), `deleted` (the local copy is kept), `restored`.
- `local_path` is where the file is on disk, so open it directly to read or summarize it.
- `log --since` returns the same `changes` list, merged over the window, with a `time` field on each change.

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | OK | |
| 1 | Network or Canvas error | Retry later; the user may be offline |
| 2 | Bad arguments | Fix the command |
| 3 | Not configured | The user runs `canvas-pull init` |
| 4 | Token rejected or expired | The user creates a new token and runs `canvas-pull init` |
| 5 | Partly done | Report `failed` and `skipped_courses`; the rest succeeded |
| 6 | Another sync is running | Wait and try again |

## Typical tasks

- **"Anything new in my courses?"** Run `canvas-pull sync --json` (or `status` if they only
  want to look). Answer per course from `changes`. Say how many files are `locked` (they
  download automatically once the instructor unlocks them).
- **"Summarize this week's new material."** Run `canvas-pull log --since 7d --json`, then read the
  `local_path` of `new` and `modified` files (PDF, PPTX, DOCX) and summarize them per course.
- **"Find the slides about X."** Search under `save_dir` (file names first, then contents).
- **Old versions** of modified files are in `<save_dir>/.canvas-pull/history/<timestamp>/`, so
  you can compare the old and new versions of a file.

## Ask before

- `canvas-pull schedule enable` (it installs a background job), `sync --refresh` on many
  completed courses (it is slow), and changing the config file.
- Deleting anything in the save folder.
