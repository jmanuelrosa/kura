"""Versioned machine configuration."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile

from . import harnesses

SCHEMA_VERSION = 1
FILENAME = "config.json"
CATALOG_PATH = Path(".config/kura/catalog")


class Malformed(Exception):
    pass


@dataclass(frozen=True)
class Config:
    global_harnesses: tuple
    extra: dict = field(default_factory=dict, compare=False)
    schema_version: int = SCHEMA_VERSION

    def as_dict(self):
        data = dict(self.extra)
        data.update(
            {
                "schemaVersion": self.schema_version,
                "globalHarnesses": list(self.global_harnesses),
            }
        )
        return data


def home_path():
    return Path(os.environ.get("HOME") or Path.home())


def path_for(home=None):
    home = Path(home) if home is not None else home_path()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else home / ".config"
    return root / "kura" / FILENAME


def catalog_path(home=None):
    home = Path(home) if home is not None else home_path()
    return home / CATALOG_PATH


def parse(data):
    if not isinstance(data, dict):
        raise Malformed("the top level is not an object")
    version = data.get("schemaVersion")
    if version != SCHEMA_VERSION:
        raise Malformed(f"unsupported schemaVersion {version!r}; expected {SCHEMA_VERSION}")
    try:
        selected = harnesses.validate_ids(data.get("globalHarnesses"), "globalHarnesses")
    except ValueError as exc:
        raise Malformed(str(exc)) from exc
    known = {"schemaVersion", "catalog", "globalHarnesses"}
    return Config(selected, {key: value for key, value in data.items() if key not in known})


def loads(text):
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise Malformed(str(exc)) from exc
    return parse(data)


def read(home=None):
    path = path_for(home)
    try:
        return loads(path.read_text())
    except FileNotFoundError:
        return None
    except OSError:
        raise


def dump(config):
    return json.dumps(config.as_dict(), indent=2) + "\n"


def write(config, home=None):
    path = path_for(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".config.json.")
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(dump(config))
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return path


def effective_catalog(home=None, require=True):
    root = catalog_path(home)
    if require and not root.is_dir():
        raise Malformed(
            f"the fixed catalog {root} is not a directory; "
            f"create {root / 'skills'} or run `kura init` in a project"
        )
    return root
