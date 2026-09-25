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

    @property
    def pi_agent_global(self):
        return _pi_agent_value(self.extra, "global")

    @property
    def pi_agent_project(self):
        return _pi_agent_value(self.extra, "project")

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


def _pi_agent_value(extra, scope):
    pi = extra.get("pi", {})
    if not isinstance(pi, dict):
        return None
    agents = pi.get("agents", {})
    if not isinstance(agents, dict):
        return None
    return agents.get(scope)


def _normal_absolute(path):
    return Path(os.path.normpath(os.path.abspath(path)))


def _validate_pi_global_path(value, home):
    if not isinstance(value, str) or not value:
        raise Malformed("pi.agents.global must be a non-empty path string")
    if value == "~" or value.startswith("~/"):
        expanded = Path(home, value[2:]) if value.startswith("~/") else Path(home)
    elif value.startswith("~"):
        raise Malformed("pi.agents.global must use ~ or an absolute path under HOME")
    else:
        raw = Path(value)
        if not raw.is_absolute():
            raise Malformed("pi.agents.global must use ~ or an absolute path under HOME")
        expanded = raw
    home = _normal_absolute(home)
    expanded = _normal_absolute(expanded)
    if expanded == home or home not in expanded.parents:
        raise Malformed("pi.agents.global must be a directory under HOME")
    return expanded


def _validate_pi_project_path(value):
    if not isinstance(value, str) or not value:
        raise Malformed("pi.agents.project must be a non-empty path string")
    path = Path(value)
    if path.is_absolute():
        raise Malformed("pi.agents.project must be relative to the project")
    if not path.parts or any(part in ("", "..") for part in path.parts):
        raise Malformed("pi.agents.project must not escape the project or name its root")
    return path


def _validate_pi(data, home):
    pi = data.get("pi")
    if pi is None:
        return
    if not isinstance(pi, dict):
        raise Malformed("pi must be an object")
    agents = pi.get("agents")
    if agents is None:
        return
    if not isinstance(agents, dict):
        raise Malformed("pi.agents must be an object")
    for scope in ("global", "project"):
        if scope not in agents:
            continue
        path = (
            _validate_pi_global_path(agents[scope], home)
            if scope == "global"
            else _validate_pi_project_path(agents[scope])
        )
        anchor = Path(home) if scope == "global" else Path(".")
        owned = [harnesses.skill_root(harness_id, anchor, None if scope == "global" else anchor)
                 for harness_id in harnesses.IDS]
        owned.append(harnesses.agent_root("claude", anchor, None if scope == "global" else anchor))
        for root in owned:
            if path == root or path in root.parents or root in path.parents:
                raise Malformed(f"pi.agents.{scope} overlaps the {root} native view")


def parse(data, home=None):
    if not isinstance(data, dict):
        raise Malformed("the top level is not an object")
    version = data.get("schemaVersion")
    if version != SCHEMA_VERSION:
        raise Malformed(f"unsupported schemaVersion {version!r}; expected {SCHEMA_VERSION}")
    try:
        selected = harnesses.validate_ids(data.get("globalHarnesses"), "globalHarnesses")
    except ValueError as exc:
        raise Malformed(str(exc)) from exc
    _validate_pi(data, Path(home) if home is not None else home_path())
    known = {"schemaVersion", "catalog", "globalHarnesses"}
    return Config(selected, {key: value for key, value in data.items() if key not in known})


def loads(text, home=None):
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise Malformed(str(exc)) from exc
    return parse(data, home)


def read(home=None):
    path = path_for(home)
    try:
        return loads(path.read_text(), home)
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
            f"create {root / 'skills'} or run `kura config` to create it"
        )
    return root
