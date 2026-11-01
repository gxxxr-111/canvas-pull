import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from canvas_pull import cli
from canvas_pull.api import CanvasClient

from .fake_canvas import BASE, FakeCanvas, FakeCourse, FakeFile

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = FakeCanvas()
    course = FakeCourse(id=1, name="Algorithms")
    course.files[1] = FakeFile(1, "hw1.pdf", 1, b"one")
    fake.courses[1] = course

    def client(base_url, token, **kw):
        return CanvasClient(base_url, token, transport=fake.transport(), backoff=0)

    monkeypatch.setattr(cli, "CanvasClient", client)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CANVAS_URL", BASE)
    monkeypatch.setenv("CANVAS_TOKEN", "good-token")
    monkeypatch.setenv("CANVAS_SAVE_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(cli, "default_config_file", lambda: tmp_path / "none.toml")
    return fake, tmp_path / "out"


def test_status_sync_log(env):
    fake, out = env
    r = runner.invoke(cli.app, ["status"])
    assert r.exit_code == 0 and "hw1.pdf" in r.output and not out.exists()

    r = runner.invoke(cli.app, ["sync"])
    assert r.exit_code == 0, r.output
    assert (out / "Algorithms/hw1.pdf").read_bytes() == b"one"

    fake.courses[1].files[2] = FakeFile(2, "hw2.pdf", 1, b"two")
    runner.invoke(cli.app, ["sync"])
    r = runner.invoke(cli.app, ["log"])
    assert r.output.index("hw2.pdf") < r.output.index("hw1.pdf")  # newest first


def test_course_filter(env):
    r = runner.invoke(cli.app, ["status", "-c", "nothing-matches"])
    assert "No matching active courses" in r.output


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_commit(env, monkeypatch):
    _, out = env
    for k, v in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }.items():
        monkeypatch.setenv(k, v)
    r = runner.invoke(cli.app, ["sync", "--git"])
    assert r.exit_code == 0, r.output
    msg = subprocess.run(
        ["git", "-C", str(out), "log", "-1", "--format=%s"], capture_output=True, text=True
    ).stdout
    assert msg.strip() == "sync: 1 new"


def test_bad_token_message(env, monkeypatch):
    monkeypatch.setenv("CANVAS_TOKEN", "expired")
    r = runner.invoke(cli.app, ["sync"])
    assert r.exit_code == 1 and "token" in r.output.lower()


def test_notify_on_changes_and_on_bad_token(env, monkeypatch):
    sent = []
    monkeypatch.setattr(cli.notify, "send", lambda msg, title="": sent.append(msg) or True)
    runner.invoke(cli.app, ["sync", "--notify"])
    assert sent == ["Algorithms: 1 new"]
    runner.invoke(cli.app, ["sync", "--notify"])
    assert len(sent) == 1  # nothing changed, no notification
    monkeypatch.setenv("CANVAS_TOKEN", "expired")
    runner.invoke(cli.app, ["sync", "--notify"])
    assert "token" in sent[-1]


def test_log_since_summary(env):
    fake, _ = env
    runner.invoke(cli.app, ["sync"])
    fake.courses[1].files[1].content = b"one, edited"
    fake.courses[1].files[1].modified_at = "2026-10-01T00:00:00Z"
    runner.invoke(cli.app, ["sync"])
    r = runner.invoke(cli.app, ["log", "--since", "7d"])
    assert r.exit_code == 0, r.output
    assert "1 new" in r.output and "modified" not in r.output  # new then edited = new


def test_completed_courses_archived_after_first_sync(env):
    fake, out = env
    old = FakeCourse(id=2, name="Chemistry", completed=True)
    old.files[20] = FakeFile(20, "lab.pdf", 1, b"lab")
    fake.courses[2] = old

    r = runner.invoke(cli.app, ["sync", "--completed"])
    assert r.exit_code == 0, r.output
    assert (out / "Chemistry/lab.pdf").exists()

    fake.requests.clear()
    r = runner.invoke(cli.app, ["sync", "--completed"])
    assert "already archived" in r.output
    assert not any(p.startswith("/api/v1/courses/2/") for p in fake.requests)

    fake.requests.clear()
    runner.invoke(cli.app, ["sync", "--completed", "--refresh"])
    assert any(p.startswith("/api/v1/courses/2/") for p in fake.requests)


def test_adding_a_source_rescans_archived_course(env, monkeypatch, tmp_path):
    fake, _ = env
    old = FakeCourse(id=2, name="Chemistry", completed=True)
    old.files[20] = FakeFile(20, "lab.pdf", 1, b"lab")
    fake.courses[2] = old
    runner.invoke(cli.app, ["sync", "--completed"])

    cfg_file = tmp_path / "with-sources.toml"
    cfg_file.write_text('sources = ["files", "announcements"]\n')
    monkeypatch.setattr(cli, "load_config", _loader(cfg_file))
    fake.requests.clear()
    runner.invoke(cli.app, ["sync", "--completed"])
    assert any(p.startswith("/api/v1/courses/2/") for p in fake.requests)


def _loader(cfg_file):
    from canvas_pull.config import load_config

    return lambda **kw: load_config(config_file=cfg_file, use_keyring=False, **kw)
