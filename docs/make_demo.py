"""Render docs/demo.svg: real canvas-pull output against the fake Canvas used in tests.

Run from the repo root:  uv run python docs/make_demo.py
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fake_canvas import BASE, FakeCanvas, FakeCourse, FakeFile  # noqa: E402

from canvas_pull import cli, config  # noqa: E402
from canvas_pull.api import CanvasClient  # noqa: E402


class _Quiet:
    """Stand-in for spinners and progress bars, which don't belong in a still image."""

    def __init__(self, *a, **k): ...
    def __enter__(self):
        return self

    def __exit__(self, *a): ...
    def update(self, *a, **k): ...
    def add_task(self, *a, **k):
        return 0

    def advance(self, *a, **k): ...


class DemoConsole(Console):
    def status(self, *a, **k):  # type: ignore[override]
        return _Quiet()


def build_canvas() -> FakeCanvas:
    fake = FakeCanvas()
    sig = FakeCourse(id=1, name="Signals and Systems")
    sig.folders |= {2: "course files/Lectures", 3: "course files/Homework"}
    sig.files |= {
        1: FakeFile(1, "Syllabus.pdf", 1, b"syllabus"),
        2: FakeFile(2, "Lecture 01 - Intro.pdf", 2, b"l1"),
        3: FakeFile(3, "Lecture 02 - LTI Systems.pdf", 2, b"l2"),
        4: FakeFile(4, "hw1.pdf", 3, b"hw1"),
    }
    os_ = FakeCourse(id=2, name="Operating Systems")
    os_.folders |= {2: "course files/Slides"}
    os_.files |= {
        10: FakeFile(10, "ch1-overview.pptx", 2, b"ch1"),
        11: FakeFile(11, "ch2-processes.pptx", 2, b"ch2"),
        12: FakeFile(12, "quiz-sample.pdf", 1, b"quiz"),
    }
    fake.courses |= {1: sig, 2: os_}
    return fake


def instructors_update(fake: FakeCanvas) -> None:
    sig, os_ = fake.courses[1], fake.courses[2]
    sig.files[5] = FakeFile(5, "Lecture 03 - Fourier Series.pdf", 2, b"l3")
    sig.files[6] = FakeFile(6, "hw2.pdf", 3, b"hw2")
    sig.files[1].content, sig.files[1].modified_at = b"syllabus v2", "2026-10-30T00:00:00Z"
    sig.files[4].name = "Homework 1.pdf"
    os_.files[13] = FakeFile(13, "ch3-threads.pptx", 2, b"ch3")
    del os_.files[12]


def main() -> None:
    fake = build_canvas()
    rec = DemoConsole(
        record=True, width=82, highlight=False, force_terminal=True, color_system="truecolor"
    )
    cli.console = rec
    cli.err = rec
    cli.Progress = _Quiet  # type: ignore[assignment]
    cli.CanvasClient = lambda url, token, **kw: CanvasClient(
        url, token, transport=fake.transport(), backoff=0
    )
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmp:
        cfg_file = Path(tmp) / "none.toml"
        config.default_config_file = lambda: cfg_file  # type: ignore[assignment]
        # A throwaway home folder, so the save folder prints as ~/Canvas.
        os.environ.update(
            HOME=tmp, CANVAS_URL=BASE, CANVAS_TOKEN="good-token", CANVAS_SAVE_DIR=f"{tmp}/Canvas"
        )
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            runner.invoke(cli.app, ["sync"])  # first run: everything is new
        rec.export_text(clear=True)  # drop the first run from the picture
        # Pretend the first download happened two weeks ago.
        log = Path(tmp) / "Canvas/.canvas-pull/log.jsonl"
        log.write_text(
            re.sub(
                r'"time": "[^"]+"',
                '"time": "2026-10-15T08:00:00+00:00"',
                log.read_text(encoding="utf-8"),
            ),
            encoding="utf-8",
        )
        instructors_update(fake)

        def show(cmd: list[str]) -> None:
            rec.print(f"[bold green]$[/] [bold]canvas-pull {' '.join(cmd)}[/]", highlight=False)
            result = runner.invoke(cli.app, cmd)
            assert result.exit_code == 0, result.output

        show(["sync"])
        # A later sync, not shown: the new chapter gets renamed. The summary merges it.
        fake.courses[2].files[13].name = "ch3-threads-and-locks.pptx"
        cli.console = cli.err = Console(file=io.StringIO())
        runner.invoke(cli.app, ["sync"])
        cli.console = cli.err = rec
        rec.print()
        show(["log", "--since", "7d"])
        text = rec.export_svg(title="canvas-pull")
    out = ROOT / "docs" / "demo.svg"
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
