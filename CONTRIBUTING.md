# Contributing

Thanks for helping! Bug reports, school reports and pull requests are all welcome.

## Report how it works at your school

canvas-pull should work with any Canvas LMS site, but schools configure Canvas differently.
If you try it, please open a **School report** issue, whether it worked or not. Working schools
are added to the list in the README.

## Development setup

```bash
git clone https://github.com/gxxxr-111/canvas-pull
cd canvas-pull
uv sync              # creates .venv with dev dependencies
uv run canvas-pull --help
```

Before opening a pull request:

```bash
uv run ruff format src tests
uv run ruff check src tests
uv run pytest
```

CI runs the same checks on macOS, Linux and Windows with Python 3.11–3.13.

## Tests

Tests run against an in-memory fake Canvas (`tests/fake_canvas.py`) through
`httpx.MockTransport`, so no account or network is needed. If you add support for a new Canvas
endpoint, extend the fake and add a test.

**Never commit real tokens, cookies or course files**, including in test fixtures or issue
logs.

## Project layout

| Module | Responsibility |
| --- | --- |
| `api.py` | HTTP client: retries, pagination, concurrency cap, streaming downloads |
| `collector.py` | Find courses and list their files from each source |
| `sync.py` | Compare Canvas with the manifest (`make_plan`) and apply changes (`execute`) |
| `manifest.py` | Local state in `.canvas-pull/`: manifest, log, lock |
| `cli.py` | Typer commands |
| `schedule.py`, `notify.py`, `gitops.py`, `summary.py` | Automatic sync, notifications, git, summaries |

## Pull requests

- Keep each PR to one change and explain why it is needed.
- Add an entry under `## [Unreleased]` in `CHANGELOG.md` for anything users will notice.
- Follow the existing style: small functions, type hints, comments that explain *why*.

## Releasing (maintainers)

1. Move the `Unreleased` entries in `CHANGELOG.md` under a new version heading.
2. Bump `version` in `pyproject.toml` and `src/canvas_pull/__init__.py`.
3. Merge to `main`, then tag and push: `git tag v2.4.0 && git push origin v2.4.0`.
   The Release workflow publishes to PyPI and creates the GitHub release.
