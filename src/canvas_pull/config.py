"""Load and save settings.

Sources, lowest to highest priority:
  1. ~/.config/canvas-pull/config.toml (written by `canvas-pull init`)
  2. a `.env` file in the current directory
  3. environment variables

The token is read from those sources first, then from the system keychain.
Legacy keys from v1 (WEBSITE, ACCESS_TOKEN, SAVE_DIR) are still accepted.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_config_path

from .collector import DEFAULT_SOURCES, SOURCES

APP_NAME = "canvas-pull"
KEYRING_SERVICE = "canvas-pull"
DEFAULT_SAVE_DIR = "~/Canvas"

# setting name -> accepted environment / .env keys (first match wins)
ENV_KEYS: dict[str, tuple[str, ...]] = {
    "website": ("CANVAS_URL", "WEBSITE"),
    "token": ("CANVAS_TOKEN", "ACCESS_TOKEN"),
    "save_dir": ("CANVAS_SAVE_DIR", "SAVE_DIR"),
}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    website: str
    save_dir: Path
    token: str = ""
    concurrency: int = 4
    scan_concurrency: int = 8
    keep_history: bool = True
    git: bool = False
    exclude: list[str] = field(default_factory=list)
    include_completed: bool = False
    sources: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))


def default_config_file() -> Path:
    return user_config_path(APP_NAME) / "config.toml"


def normalize_website(value: str) -> str:
    """'oc.sjtu.edu.cn/' -> 'https://oc.sjtu.edu.cn'"""
    url = value.strip().rstrip("/")
    if not url:
        raise ConfigError("Canvas URL is empty.")
    if "://" not in url:
        url = "https://" + url
    if url.endswith("/api/v1"):
        url = url[: -len("/api/v1")]
    return url


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def _from_env(source: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, keys in ENV_KEYS.items():
        for key in keys:
            if source.get(key):
                out[name] = source[key]
                break
    return out


def _keyring_get(website: str) -> str:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, website) or ""
    except Exception:  # no backend, locked keychain, etc.
        return ""


def load_config(
    *,
    require_token: bool = True,
    config_file: Path | None = None,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
    use_keyring: bool = True,
) -> Config:
    config_file = config_file or default_config_file()
    cwd = cwd or Path.cwd()
    environ = os.environ if environ is None else environ

    data: dict = {}
    if config_file.is_file():
        try:
            data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Cannot parse {config_file}: {exc}") from exc
    data.update(_from_env(parse_dotenv(cwd / ".env")))
    data.update(_from_env(environ))

    if not data.get("website"):
        raise ConfigError("No Canvas URL configured. Run `canvas-pull init` first.")
    website = normalize_website(str(data["website"]))

    save_dir = Path(str(data.get("save_dir") or DEFAULT_SAVE_DIR)).expanduser()
    if not save_dir.is_absolute():
        save_dir = (cwd / save_dir).resolve()

    token = str(data.get("token") or "")
    if not token and use_keyring:
        token = _keyring_get(website)
    if require_token and not token:
        raise ConfigError("No access token found. Run `canvas-pull init`, or set CANVAS_TOKEN.")

    try:
        return Config(
            website=website,
            save_dir=save_dir,
            token=token,
            concurrency=max(1, int(data.get("concurrency", 4))),
            scan_concurrency=max(1, min(16, int(data.get("scan_concurrency", 8)))),
            keep_history=bool(data.get("keep_history", True)),
            git=bool(data.get("git", False)),
            exclude=[str(x) for x in data.get("exclude", [])],
            include_completed=bool(data.get("include_completed", False)),
            sources=_sources(data.get("sources", list(DEFAULT_SOURCES))),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid value in {config_file}: {exc}") from exc


def _sources(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"`sources` must be a list, e.g. {list(SOURCES)}")
    unknown = [s for s in value if s not in SOURCES]
    if unknown:
        raise ConfigError(f"Unknown source(s) {unknown}. Choose from {list(SOURCES)}.")
    return [str(s) for s in value]


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_config(cfg: Config, *, config_file: Path | None = None) -> str:
    """Write settings; store the token in the keychain when possible.

    Returns where the token ended up: "keychain" or the config file path.
    """
    config_file = config_file or default_config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)

    values: dict[str, object] = {
        "website": cfg.website,
        "save_dir": str(cfg.save_dir),
        "concurrency": cfg.concurrency,
        "scan_concurrency": cfg.scan_concurrency,
        "keep_history": cfg.keep_history,
        "git": cfg.git,
        "exclude": cfg.exclude,
        "include_completed": cfg.include_completed,
        "sources": cfg.sources,
    }
    stored_in = "keychain"
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, cfg.website, cfg.token)
    except Exception:
        values["token"] = cfg.token
        stored_in = str(config_file)

    lines = ["# canvas-pull settings. Edit freely; run `canvas-pull config` to check."]
    lines += [f"{k} = {_toml_value(v)}" for k, v in values.items()]
    config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if "token" in values:
        config_file.chmod(0o600)
    return stored_in
