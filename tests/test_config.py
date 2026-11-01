from pathlib import Path

import pytest

from canvas_pull.config import Config, ConfigError, load_config, normalize_website, save_config


def test_normalize_website():
    assert normalize_website("oc.sjtu.edu.cn/") == "https://oc.sjtu.edu.cn"
    assert normalize_website("https://x.edu/api/v1") == "https://x.edu"
    assert normalize_website("http://localhost:3000") == "http://localhost:3000"


def test_precedence_file_then_dotenv_then_env(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    data_dir = (tmp_path / "data").as_posix()
    cfg_file.write_text(f'website = "a.edu"\nsave_dir = "{data_dir}"\ntoken = "file"\n')
    (tmp_path / ".env").write_text("WEBSITE=b.edu\nACCESS_TOKEN='dotenv'\n")
    cfg = load_config(
        config_file=cfg_file, cwd=tmp_path, environ={"CANVAS_TOKEN": "env"}, use_keyring=False
    )
    assert (cfg.website, cfg.token, cfg.save_dir) == ("https://b.edu", "env", Path(data_dir))


def test_relative_save_dir_resolves_against_cwd(tmp_path: Path):
    (tmp_path / ".env").write_text("WEBSITE=a.edu\nACCESS_TOKEN=t\nSAVE_DIR=.\n")
    cfg = load_config(config_file=tmp_path / "none.toml", cwd=tmp_path, environ={})
    assert cfg.save_dir == tmp_path.resolve()


def test_missing_token_is_an_error(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_config(
            config_file=tmp_path / "none.toml",
            cwd=tmp_path,
            environ={"CANVAS_URL": "a.edu"},
            use_keyring=False,
        )


def test_save_roundtrip(tmp_path: Path, monkeypatch):
    import keyring

    store: dict = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    cfg_file = tmp_path / "c.toml"
    cfg = Config(website="https://a.edu", save_dir=tmp_path, token="secret", exclude=['x "y"'])
    assert save_config(cfg, config_file=cfg_file) == "keychain"
    assert "secret" not in cfg_file.read_text()
    loaded = load_config(config_file=cfg_file, cwd=tmp_path, environ={})
    assert (loaded.token, loaded.exclude) == ("secret", ['x "y"'])
