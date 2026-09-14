"""Built-in harness profiles and native skill paths."""

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


PROFILES = {
    "claude": Profile(
        "claude",
        "Claude Code",
        "claude",
        (".claude", "skills"),
        (".claude", "skills"),
        (".claude", "CLAUDE.md"),
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
