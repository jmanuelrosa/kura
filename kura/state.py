"""The declarative project manifest and legacy migration state."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile

from . import catalog as cat
from . import harnesses

SCHEMA_VERSION = 1
V2_SCHEMA_VERSION = 2
FILENAME = "kura.json"
DIRECT = "direct"
DEP_PREFIX = "dep-of:"
COLLECTIONS = {cat.SKILL: "skills", cat.AGENT: "agents", cat.PLUGIN: "plugins"}
BY_COLLECTION = {value: key for key, value in COLLECTIONS.items()}


class Malformed(Exception):
    pass


@dataclass(frozen=True)
class Manifest:
    harnesses: tuple
    skills: tuple = ()
    legacy: dict = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    agents: tuple = ()
    bundles: tuple = ()

    def as_dict(self):
        version = self.schema_version
        if version == SCHEMA_VERSION and (self.agents or self.bundles):
            version = V2_SCHEMA_VERSION
        data = {
            "schemaVersion": version,
            "harnesses": list(self.harnesses),
            "skills": list(self.skills),
        }
        if version == V2_SCHEMA_VERSION:
            data["agents"] = sorted(set(self.agents))
            data["bundles"] = sorted(set(self.bundles))
        if self.legacy:
            data["legacy"] = {
                collection: dict(sorted(entries.items()))
                for collection, entries in sorted(self.legacy.items())
                if entries
            }
        return data


def path_for(project):
    return Path(project) / FILENAME


def legacy_path_for(project):
    return Path(project) / ".claude" / FILENAME


def dep_of(parent_name):
    return f"{DEP_PREFIX}{parent_name}"


def is_direct(reason):
    return not str(reason).startswith(DEP_PREFIX)


def parent_of(reason):
    text = str(reason)
    return text[len(DEP_PREFIX) :] if text.startswith(DEP_PREFIX) else None


def _sorted_unique_strings(value, field_name, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        suffix = "non-empty " if nonempty else ""
        raise Malformed(f"{field_name} must be a {suffix}list")
    if any(not isinstance(item, str) or not item for item in value):
        raise Malformed(f"{field_name} must contain non-empty strings")
    if value != sorted(value):
        raise Malformed(f"{field_name} must be sorted")
    if len(value) != len(set(value)):
        raise Malformed(f"{field_name} must not contain duplicates")
    return tuple(value)


def _artifact_names(value, field_name):
    names = _sorted_unique_strings(value, field_name)
    for name in names:
        try:
            cat._validate_registry_name(name)
        except ValueError as exc:
            raise Malformed(f"{field_name} contains an unsafe name {name!r}") from exc
    return names


def _parse_legacy(data):
    raw_legacy = data["legacy"] if "legacy" in data else {}
    if not isinstance(raw_legacy, dict):
        raise Malformed("legacy must be an object")
    legacy = {}
    unknown = sorted(set(raw_legacy) - {"agents", "plugins"})
    if unknown:
        raise Malformed(f"legacy contains unknown collections: {', '.join(unknown)}")
    for collection in ("agents", "plugins"):
        entries = raw_legacy.get(collection)
        if entries is None:
            continue
        if not isinstance(entries, dict) or any(
            not isinstance(name, str) or not name or not isinstance(reason, str)
            for name, reason in entries.items()
        ):
            raise Malformed(f"legacy.{collection} must map names to string reasons")
        if entries:
            legacy[collection] = dict(sorted(entries.items()))
    return legacy


def parse(data):
    if not isinstance(data, dict):
        raise Malformed("the top level is not an object")
    version = data.get("schemaVersion")
    if version not in (SCHEMA_VERSION, V2_SCHEMA_VERSION):
        raise Malformed(
            f"unsupported schemaVersion {version!r}; expected {SCHEMA_VERSION} or {V2_SCHEMA_VERSION}"
        )
    raw_harnesses = data.get("harnesses")
    try:
        selected = harnesses.validate_ids(raw_harnesses, "harnesses")
    except ValueError as exc:
        raise Malformed(str(exc)) from exc
    skills = _sorted_unique_strings(data.get("skills"), "skills")
    if version == SCHEMA_VERSION:
        unexpected = sorted(set(data) & {"agents", "bundles"})
        if unexpected:
            raise Malformed(
                f"schemaVersion 1 cannot contain active collections: {', '.join(unexpected)}"
            )
        return Manifest(selected, skills, _parse_legacy(data), SCHEMA_VERSION)
    agents = _artifact_names(data.get("agents"), "agents")
    bundles = _artifact_names(data.get("bundles"), "bundles")
    return Manifest(selected, skills, _parse_legacy(data), V2_SCHEMA_VERSION, agents, bundles)


def loads(text):
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise Malformed(str(exc)) from exc
    return parse(data)


def read_strict(project):
    path = path_for(project)
    if not path.is_file():
        return None
    return loads(path.read_text())


def read(project):
    try:
        return read_strict(project)
    except (Malformed, OSError):
        return None


def upgrade(manifest, agents=None, bundles=None):
    agent_names = manifest.agents if agents is None else agents
    bundle_names = manifest.bundles if bundles is None else bundles
    return Manifest(
        manifest.harnesses,
        manifest.skills,
        manifest.legacy,
        V2_SCHEMA_VERSION,
        _artifact_names(sorted(set(agent_names)), "agents"),
        _artifact_names(sorted(set(bundle_names)), "bundles"),
    )


def dump(manifest):
    return json.dumps(manifest.as_dict(), indent=2) + "\n"


def write(project, manifest):
    path = path_for(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".kura.json.")
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(dump(manifest))
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return path


def read_legacy(project):
    path = legacy_path_for(project)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise Malformed(f"legacy manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise Malformed("legacy manifest does not hold an object")
    installed = data.get("installed", {})
    if not isinstance(installed, dict):
        raise Malformed("legacy manifest does not hold an installed object")
    rows = {}
    for collection, entries in installed.items():
        kind = BY_COLLECTION.get(collection)
        if kind is None:
            continue
        if not isinstance(entries, dict):
            raise Malformed(f"legacy manifest collection {collection} is not an object")
        for name, reason in entries.items():
            if not isinstance(name, str) or not name or not isinstance(reason, str):
                raise Malformed(
                    f"legacy manifest {collection} must map non-empty names to string reasons"
                )
            rows[(kind, name)] = reason
    return rows


def migrated(rows, harness_ids):
    direct = sorted(
        name
        for (kind, name), reason in rows.items()
        if kind == cat.SKILL and is_direct(reason)
    )
    legacy = {}
    for kind, collection in ((cat.AGENT, "agents"), (cat.PLUGIN, "plugins")):
        entries = {
            name: reason
            for (row_kind, name), reason in rows.items()
            if row_kind == kind
        }
        if entries:
            legacy[collection] = dict(sorted(entries.items()))
    return Manifest(tuple(sorted(harness_ids)), tuple(direct), legacy)


def merge(root_manifest, migrated_manifest):
    if root_manifest is None:
        return migrated_manifest
    legacy_skills = set(migrated_manifest.skills)
    root_skills = set(root_manifest.skills)
    if legacy_skills != root_skills:
        only_root = ", ".join(sorted(root_skills - legacy_skills)) or "none"
        only_legacy = ", ".join(sorted(legacy_skills - root_skills)) or "none"
        raise Malformed(
            "root and legacy manifests disagree "
            f"(only in root: {only_root}; only in legacy: {only_legacy})"
        )
    legacy = {
        collection: dict(entries)
        for collection, entries in root_manifest.legacy.items()
    }
    for collection, entries in migrated_manifest.legacy.items():
        current = legacy.setdefault(collection, {})
        for name, reason in entries.items():
            if name in current and current[name] != reason:
                raise Malformed(
                    f"root and legacy manifests disagree on {collection}.{name}: "
                    f"{current[name]!r} != {reason!r}"
                )
            current[name] = reason
    version = root_manifest.schema_version
    if root_manifest.agents or root_manifest.bundles:
        version = V2_SCHEMA_VERSION
    return Manifest(
        root_manifest.harnesses,
        tuple(sorted(root_skills or legacy_skills)),
        legacy,
        version,
        root_manifest.agents,
        root_manifest.bundles,
    )
