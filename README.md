<h1 align="center">Canvas Pull</h1>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-green" />
  <img src="https://img.shields.io/badge/version-2.2.0-blue" />
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" />
</p>

Sync every file from your **active** Canvas LMS courses in one command. Later runs download
only what changed and tell you exactly which files were **added, modified, renamed/moved or
deleted** by your instructors.

```
$ canvas-pull sync

Signals & Systems
  + new      Week 7/lecture7.pdf
  ~ modified syllabus.pdf
  → moved    lecture1.pptx → Week 1/Lecture 01.pptx

Operating Systems
  - deleted  old-quiz.pdf

1 new, 1 modified, 1 moved, 1 deleted · ~/Canvas
```

[中文说明见下方](#中文说明)

## Features

- **Incremental sync.** Files are tracked by their Canvas file ID, so unchanged files are never
  downloaded again and renames are detected without re-downloading.
- **Change report.** `status` previews changes, `sync` applies them, `log` shows past syncs,
  and `log --since 7d` gives a merged weekly summary.
- **Automatic sync.** `schedule enable` runs a sync every day (or every few hours) and shows a
  desktop notification when something changes.
- **Old versions kept.** When a file changes, the previous copy is moved to
  `.canvas-pull/history/<timestamp>/`. Files deleted on Canvas stay on your disk.
- **Files tab by default, more on request.** Only the Files tab is synced by default (with
  Modules as a fallback when a course hides it). Opt in to the syllabus, assignment
  descriptions, pages, announcements and discussions through the `sources` setting.
- **Current courses, or everything.** Courses whose term has ended are skipped by default;
  `--completed` also archives concluded courses.
- **Safe token storage.** `init` stores your token in the system keychain, not in a text file.
- **Optional git.** `sync --git` commits each sync in the save folder.
- Parallel downloads, automatic retries with back-off, full pagination.

## Install

```bash
uv tool install git+https://github.com/gxxxr-111/canvas-pull
# or: pipx install git+https://github.com/gxxxr-111/canvas-pull
```

## Quick start

1. In Canvas, open **Account → Settings → New Access Token** and copy the token.
   Give it an expiry date. Never share it.
2. Run:

   ```bash
   canvas-pull init      # Canvas URL (e.g. oc.sjtu.edu.cn), save folder, token
   canvas-pull courses   # check which courses will be synced
   canvas-pull sync
   ```

## Commands

| Command | What it does |
| --- | --- |
| `canvas-pull init` | Set Canvas URL, save folder and token (token goes to the keychain). |
| `canvas-pull courses [--all] [--completed]` | List courses that would be synced. |
| `canvas-pull status [-c COURSE] [--completed]` | Show what changed on Canvas. Downloads nothing. |
| `canvas-pull sync [-n] [-c COURSE] [--completed] [--git] [--notify]` | Download changes. `-n` is a dry run. `-c` takes a course ID or part of its name and can be repeated. `--notify` shows a desktop notification. |
| `canvas-pull log [-n 10]` | Show recent syncs. |
| `canvas-pull log --since 7d` | Merged summary since `7d`, `24h`, `2w`, `today` or a date. |
| `canvas-pull schedule enable [--at 08:00 ...] [--every 6h]` | Sync automatically (launchd on macOS, systemd timer on Linux). Default: daily 09:00. |
| `canvas-pull schedule status \| run \| disable` | Show the schedule and its log, trigger it now, or remove it. |
| `canvas-pull config` | Show settings in use. |

## Configuration

`init` writes a config file (path shown by `canvas-pull config`). You can edit it:

```toml
website = "https://oc.sjtu.edu.cn"
save_dir = "/Users/me/Canvas"
concurrency = 4          # parallel downloads
keep_history = true      # keep replaced versions in .canvas-pull/history
git = false              # commit after every sync
exclude = ["PE", "12345"] # course IDs or name fragments to skip
include_completed = false # also sync concluded courses
sources = ["files"]       # default; add any of the sources below
```

Available sources: `files`, `modules`, `syllabus`, `assignments`, `pages`, `announcements`,
`discussions`. With the default, a course that hides its Files tab (or leaves it empty) falls
back to Modules automatically. To grab everything:

```toml
sources = ["files", "modules", "syllabus", "assignments", "pages", "announcements", "discussions"]
```

When a file appears in several places it keeps the path of the first source in that list, so a
file in the Files tab stays where it is even if an assignment links to it. Files found elsewhere
go under `Modules/<module>/`, `Assignments/<name>/`, `Pages/<title>/`, `Announcements/<title>/`,
`Discussions/<title>/` or `Syllabus/`.

### Automatic sync

```bash
canvas-pull schedule enable --at 08:00 --at 20:00   # or: --every 6h
canvas-pull schedule run      # try it once now
canvas-pull schedule status   # schedule + last log lines
```

The job runs `canvas-pull sync --notify` from the folder where you enabled it. A Mac that was
asleep at the scheduled time catches up when it wakes. Install with `uv tool install` first so
the job points at a stable path.

Environment variables override the file: `CANVAS_URL`, `CANVAS_TOKEN`, `CANVAS_SAVE_DIR`. A
`.env` file in the current folder is also read (v1 keys `WEBSITE`, `ACCESS_TOKEN`, `SAVE_DIR`
still work).

## How change detection works

Each synced file is recorded in `<save_dir>/.canvas-pull/manifest.json` with its Canvas ID,
path, size, last-modified time and SHA-256. On every run canvas-pull compares that record with
Canvas metadata:

| Canvas says | Result |
| --- | --- |
| ID not seen before | **new**: download |
| Size or modified time changed | **modified**: download; if the bytes are identical, nothing is reported |
| Same content, different name/folder | **moved**: renamed locally, no download |
| ID gone from a course that was read successfully | **deleted**: reported once; local copy kept |
| Recorded but missing on disk | **restored**: downloaded again |

Content-level diffs of PDF/PPTX/DOCX are out of scope for now.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

Tests run against an in-memory fake Canvas, so no account is needed.

---

## 中文说明

一条命令同步所有**正在进行的** Canvas 课程文件。之后每次运行只下载有变化的文件，并列出老师**新增、修改、改名/移动、删除**了哪些文件。

```bash
uv tool install git+https://github.com/gxxxr-111/canvas-pull
canvas-pull init      # 填学校 Canvas 域名（如 oc.sjtu.edu.cn）、保存目录、Access Token
canvas-pull status    # 只看变化，不下载
canvas-pull sync      # 下载变化
canvas-pull log --since 7d          # 本周汇总
canvas-pull schedule enable --at 08:00   # 每天早上自动同步，有变化时弹通知
```

- Token 在 Canvas 的「账户 → 设置 → 新建访问许可证」生成，`init` 会把它存进系统钥匙串。
- 文件被修改时，旧版本会移到 `.canvas-pull/history/`；Canvas 上删除的文件本地不会删除。
- 默认只同步「文件」栏目；课程隐藏了文件栏目时，会自动改从「模块」里找。
- 想要更多来源（课程大纲、作业、页面、公告、讨论里的附件和链接），在配置文件里设置 `sources`。
- `--completed` 可以把已结业的课程也一起归档下来。
- `sync --git` 会在保存目录里自动提交一次 git，方便用 `git log` 回看。
