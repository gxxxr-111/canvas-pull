# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.4.0] - 2026-11-01

First release on PyPI: `uv tool install canvas-pull` or `pipx install canvas-pull`.

### Added
- Windows: the sync lock now works there too (`msvcrt`), so a manual and a background sync can't
  run at the same time.
- CI on macOS, Linux and Windows with Python 3.11–3.13; tagged releases publish to PyPI
  automatically.
- Contributor guide, security policy, issue templates and a list of tested schools.

### Changed
- Paths under your home folder print as `~/...`.
- `sync` lists changes sorted by course, change type and path, not in download order.

### Fixed
- File and folder names that are reserved on Windows (`CON`, `NUL`, `COM1`, …) get a `_` prefix
  so they can be saved.

## [2.3.0] - 2026-11-01

### Changed
- Much faster scanning. Courses are scanned in parallel, folder and file lists are fetched at
  the same time, and linked files are resolved in parallel. On a 32-course account: 75 s → 10.6 s
  (Files tab only), 410 s → 60 s (all sources).
- Module items are requested inline (`include[]=items`).

### Added
- `scan_concurrency` setting (default 8) caps the number of API calls in flight.
- Completed courses are scanned once. After a clean sync they are skipped; use `--refresh` to
  scan them again. Enabling a new source rescans them automatically.

## [2.2.0] - 2026-11-01

### Changed
- Only the Files tab is synced by default. Other sources are opt-in through `sources`. When the
  Files tab is hidden or empty, Modules is still used as a fallback.

## [2.1.0] - 2026-11-01

### Added
- `canvas-pull schedule enable|disable|status|run`: automatic sync through launchd (macOS) or a
  systemd user timer (Linux).
- `sync --notify` shows desktop notifications for changes, failed downloads and a rejected
  token.
- More file sources: syllabus, assignment descriptions, pages, announcements, discussions.
- `--completed` archives concluded courses.
- `log --since 7d`: merged summary of all changes in a time window.

## [2.0.0] - 2026-11-01

Complete rewrite as an installable CLI.

### Added
- Commands: `init`, `courses`, `status`, `sync`, `log`, `config`.
- Incremental sync tracked by Canvas file ID. It reports new, modified, moved, deleted and
  restored files, and detects renames without downloading again.
- Replaced versions are kept in `.canvas-pull/history/`.
- Token stored in the system keychain.
- Optional `--git` commit after each sync.

### Fixed
- Pagination is followed, so courses with more than 10 files are complete.
- Unchanged files are no longer downloaded again on every run.
- HTTP errors are reported instead of ignored.

## [1.0.0] - 2025-03-02

- First version: download all files of enrolled courses.

[Unreleased]: https://github.com/gxxxr-111/canvas-pull/compare/v2.4.0...HEAD
[2.4.0]: https://github.com/gxxxr-111/canvas-pull/compare/v1.0.0...v2.4.0
[1.0.0]: https://github.com/gxxxr-111/canvas-pull/releases/tag/v1.0.0
