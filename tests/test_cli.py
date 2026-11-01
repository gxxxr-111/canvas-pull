import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from canvas_pull import cli, config
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
    # Never read the developer's real config file during tests.
    monkeypatch.setattr(cli, "default_config_file", lambda: tmp_path / "none.toml")
    monkeypatch.setattr(config, "default_config_file", lambda: tmp_path / "none.toml")
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
    assert r.exit_code == 4 and "token" in r.output.lower()


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


def _json(result):
    import json

    return json.loads(result.stdout)


def test_json_status_and_sync(env):
    fake, out = env
    r = runner.invoke(cli.app, ["status", "--json"])
    assert r.exit_code == 0, r.output
    data = _json(r)
    assert data["ok"] and data["dry_run"] and data["summary"] == {"new": 1}
    assert data["changes"][0]["path"] == "Algorithms/hw1.pdf"

    r = runner.invoke(cli.app, ["sync", "--json"])
    data = _json(r)
    assert data["ok"] and not data["dry_run"]
    change = data["changes"][0]
    assert change["kind"] == "new" and Path(change["local_path"]).read_bytes() == b"one"


def test_json_log_courses_config(env):
    fake, _ = env
    runner.invoke(cli.app, ["sync"])
    assert _json(runner.invoke(cli.app, ["log", "--since", "7d", "--json"]))["summary"] == {
        "new": 1
    }
    assert len(_json(runner.invoke(cli.app, ["log", "--json"]))["syncs"]) == 1
    courses = _json(runner.invoke(cli.app, ["courses", "--json"]))["courses"]
    assert courses == [
        {
            "id": 1,
            "name": "Algorithms",
            "code": None,
            "term": "Fall",
            "completed": False,
            "excluded": False,
        }
    ]
    cfg = _json(runner.invoke(cli.app, ["config", "--json"]))
    assert cfg["token"] == "set" and "good-token" not in json.dumps(cfg)


def test_json_errors_and_exit_codes(env, monkeypatch):
    monkeypatch.setenv("CANVAS_TOKEN", "expired")
    r = runner.invoke(cli.app, ["status", "--json"])
    assert r.exit_code == 4
    assert _json(r)["error"]["type"] == "auth"

    r = runner.invoke(cli.app, ["log", "--since", "soon", "--json"])
    assert r.exit_code == 2

    monkeypatch.delenv("CANVAS_URL")
    r = runner.invoke(cli.app, ["courses", "--json"])
    assert r.exit_code == 3 and _json(r)["error"]["type"] == "config"


def test_partial_failure_exit_code(env):
    fake, _ = env
    fake.flaky["/api/v1/courses/1/folders"] = 99  # this course can't be scanned
    r = runner.invoke(cli.app, ["sync", "--json"])
    assert r.exit_code == 5
    data = _json(r)
    assert not data["ok"] and data["skipped_courses"][0]["course"] == "Algorithms"


def test_busy_exit_code(env):
    from canvas_pull.manifest import sync_lock

    _, out = env
    with sync_lock(out):
        r = runner.invoke(cli.app, ["sync", "--json"])
    assert r.exit_code == 6 and _json(r)["error"]["type"] == "busy"


def test_init_non_interactive(env, monkeypatch, tmp_path):
    import keyring

    store = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__(u, p))
    cfg_file = tmp_path / "written.toml"
    monkeypatch.setattr(config, "default_config_file", lambda: cfg_file)
    monkeypatch.setattr(cli, "default_config_file", lambda: cfg_file)
    monkeypatch.delenv("CANVAS_TOKEN")
    r = runner.invoke(
        cli.app,
        ["init", "--url", BASE, "--save-dir", str(tmp_path / "c"), "--token-stdin", "--json"],
        input="good-token\n",
    )
    assert r.exit_code == 0, r.output
    data = _json(r)
    assert data["user"] == "Student" and data["token_stored_in"] == "keychain"
    assert store == {BASE: "good-token"} and "good-token" not in cfg_file.read_text()


def test_init_no_input_reports_missing(env, monkeypatch):
    monkeypatch.delenv("CANVAS_URL")
    r = runner.invoke(cli.app, ["init", "--no-input", "--json"])
    assert r.exit_code == 3 and "Canvas URL" in _json(r)["error"]["message"]
