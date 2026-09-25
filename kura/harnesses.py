"""Built-in harness profiles and native artifact paths."""

from dataclasses import dataclass
from pathlib import Path
import shutil


@dataclass(frozen=True)
class Profile:
    id: str
    display_name: str
    executable: str
    project_root: tuple
    global_root: tuple
    footprints: tuple
    project_agents: tuple = None
    global_agents: tuple = None


PROFILES = {
    "claude": Profile(
        "claude",
        "Claude Code",
        "claude",
        (".claude", "skills"),
        (".claude", "skills"),
        (".claude", "CLAUDE.md"),
        (".claude", "agents"),
        (".claude", "agents"),
    ),
    "pi": Profile(
        "pi",
        "Pi",
        "pi",
        (".agents", "skills"),
        (".agents", "skills"),
        (".pi", "AGENTS.md", "AGENTS.override.md"),
    ),
}
IDS = tuple(PROFILES)


def get(harness_id):
    return PROFILES[harness_id]


def validate_ids(values, field="harnesses"):
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a non-empty list")
    if any(not isinstance(value, str) for value in values):
        raise ValueError(f"{field} must contain harness IDs")
    unknown = sorted(set(values) - set(IDS))
    if unknown:
        raise ValueError(f"{field} contains unknown harnesses: {', '.join(unknown)}")
    if values != sorted(values):
        raise ValueError(f"{field} must be sorted")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must not contain duplicates")
    return tuple(values)


def project_skill_root(project, harness_id):
    return Path(project).joinpath(*get(harness_id).project_root)


def global_skill_root(home, harness_id):
    return Path(home).joinpath(*get(harness_id).global_root)


def skill_root(harness_id, home, project=None):
    if project is None:
        return global_skill_root(home, harness_id)
    return project_skill_root(project, harness_id)


def skill_path(harness_id, name, home, project=None):
    return skill_root(harness_id, home, project) / name


def _configured_pi_agent_root(home, project, value):
    if value is None:
        return None
    path = Path(value)
    if project is not None:
        if path.is_absolute() or any(part in ("", "..") for part in path.parts):
            return None
        return Path(project) / path
    if value == "~" or value.startswith("~/"):
        return Path(home, value[2:]) if value.startswith("~/") else Path(home)
    if value.startswith("~"):
        return None
    if not path.is_absolute():
        return None
    return path


def project_agent_root(project, harness_id, machine_config=None):
    profile = get(harness_id)
    if profile.project_agents is not None:
        return Path(project).joinpath(*profile.project_agents)
    if harness_id == "pi" and machine_config is not None:
        return _configured_pi_agent_root(None, project, machine_config.pi_agent_project)
    return None


def global_agent_root(home, harness_id, machine_config=None):
    profile = get(harness_id)
    if profile.global_agents is not None:
        return Path(home).joinpath(*profile.global_agents)
    if harness_id == "pi" and machine_config is not None:
        return _configured_pi_agent_root(home, None, machine_config.pi_agent_global)
    return None


def agent_root(harness_id, home, project=None, machine_config=None):
    if project is None:
        return global_agent_root(home, harness_id, machine_config)
    return project_agent_root(project, harness_id, machine_config)


def agent_path(harness_id, name, home, project=None, machine_config=None):
    root = agent_root(harness_id, home, project, machine_config)
    return None if root is None else root / f"{name}.md"


def executable_available(harness_id):
    return shutil.which(get(harness_id).executable) is not None


def evidence(project):
    project = Path(project)
    found = {}
    for harness_id, profile in PROFILES.items():
        footprints = [name for name in profile.footprints if (project / name).exists()]
        executable = executable_available(harness_id)
        found[harness_id] = {"footprints": tuple(footprints), "executable": executable}
    return found
