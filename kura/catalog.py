"""Reading the three artifact sources into one uniform shape.

Skills and agents come from registries; plugins are discovered by scanning for a
manifest, because they carry no registry row. Everything below is pure apart from
build_catalog's reads, so the rules that matter are testable on literal data.
"""

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

SKILL = "skill"
AGENT = "agent"
PLUGIN = "plugin"
BUNDLE = "bundle"

# Where each type INSTALLS. Plugins load as <name>@skills-dir, so they share the
# skills leaf with skills.
LEAF = {SKILL: "skills", AGENT: "agents", PLUGIN: "skills"}
# Where each type is STORED in the repo. Distinct from LEAF, and that distinction is
# load-bearing: since skills and plugins install into the same leaf, the store a
# symlink points into is the only thing that says which type it is.
STORE = {SKILL: "skills", AGENT: "agents", PLUGIN: "plugins"}

REGISTRY_FILE = {SKILL: "skill-registry.json", AGENT: "agent-registry.json"}
COLLECTION = {SKILL: "skills", AGENT: "agents"}
# Agents are single files; skills and plugins are directories.
SUFFIX = {SKILL: "", AGENT: ".md", PLUGIN: ""}

PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
# Claude Code reserves `dependencies` in a plugin manifest. An array of skill
# names there makes the plugin ship nothing at all, silently, while
# `claude plugin details` still lists every artifact. Verified on 2.1.220.
PLUGIN_DEPS_KEY = "skillDependencies"
PLUGIN_RESERVED_KEY = "dependencies"
PLUGIN_REQUIRED_KEYS = ("name", "description", "version")
BUNDLE_MARKER = "bundle.json"


@dataclass(frozen=True)
class Artifact:
    name: str
    type: str
    groups: tuple = ()
    dependencies: tuple = ()
    dependency_only: bool = False
    source: Path = None
    origin: str = "local"
    # Only repo-tracked skills have an upstream to sync from.
    upstream_repo: str = None
    upstream_path: str = None
    upstream_branch: str = None
    updated_at: str = None
    # Plugins only: keys present in the manifest, so doctor can flag the traps.
    manifest_keys: tuple = field(default=())
    metadata: bool = False
    catalog_error: str = None

    @property
    def leaf(self):
        return LEAF[self.type]

    @property
    def basename(self):
        return f"{self.name}{SUFFIX[self.type]}"

    @property
    def tagged_global(self):
        return "global" in self.groups

    @property
    def has_upstream(self):
        return self.upstream_repo is not None


@dataclass(frozen=True)
class Bundle:
    name: str
    source: Path
    agents: tuple = ()
    skills: tuple = ()
    requires_skills: tuple = ()
    requires_agents: tuple = ()
    catalog_error: str = None


def _validate_registry_name(name):
    if (
        not isinstance(name, str)
        or not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\0" in name
    ):
        raise ValueError(f"registry artifact name {name!r} must be one safe path component")
    return name


def _containment_error(kind, source, root):
    try:
        source.resolve().relative_to(root.resolve())
    except ValueError:
        return f"{kind} source {source} resolves outside {root}"
    return None


def entry_name(entry, repo_key):
    """A repo entry's directory name: the basename of upstream_path.

    Falls back to the repo name when the path is empty, "." or "/", which is how
    a skill living at a repo root is named.

    This is now the only implementation, and the Television cables depend on it being
    the name they get from `kura list --json`: their preview and $EDITOR actions
    build a path under files/claude/skills/ from it.
    """
    if "name" in entry:
        return entry["name"]
    path = (entry.get("upstream_path") or "").rstrip("/")
    if path in ("", "."):
        return repo_key.split("/")[-1]
    return path.split("/")[-1]


def _read_json(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def registry_entries(registry, collection):
    """Yield (name, entry, repo_key) across upstream and local entries.

    repo_key is None for local entries, which is what distinguishes a skill with
    an upstream from one authored here.
    """
    retired = sorted(set(registry) & {"repos", "local_skills", "local_agents"})
    if retired:
        raise ValueError(f"registry uses retired {', '.join(retired)} keys; use upstream and local")
    upstream = registry.get("upstream") or {}
    local = registry.get("local") or []
    if not isinstance(upstream, dict) or not isinstance(local, list):
        raise ValueError("registry upstream must be an object and local must be a list")
    for repo_key, repo in upstream.items():
        if not isinstance(repo, dict) or not isinstance(repo.get(collection) or [], list):
            raise ValueError(f"registry upstream {repo_key!r} has malformed {collection}")
        for entry in repo.get(collection) or []:
            if not isinstance(entry, dict):
                raise ValueError(f"registry upstream {repo_key!r} has a non-object entry")
            name = _validate_registry_name(entry_name(entry, repo_key))
            yield name, entry, repo_key
    for entry in local:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError("registry local has a malformed entry")
        name = _validate_registry_name(entry["name"])
        yield name, entry, None


def _frontmatter_value(path, wanted):
    if not path.is_file():
        return None
    text = path.read_text()
    if not text.startswith("---\n"):
        return None
    body, marker, _ = text[4:].partition("\n---")
    if not marker:
        return None
    quoted = {wanted, f'"{wanted}"', f"'{wanted}'"}
    for line in body.splitlines():
        if line.startswith((" ", "\t")):
            continue
        key, separator, value = line.partition(":")
        if not separator or key.strip() not in quoted:
            continue
        return value.strip().strip("\"'") or None
    return None


def _frontmatter_name(path):
    return _frontmatter_value(path, "name")


def _agent_description_error(path, label):
    if _frontmatter_name(path) is None:
        return f"{label} is missing frontmatter name"
    if _frontmatter_value(path, "description") is None:
        return f"{label} is missing frontmatter description"
    return None


def _strings(value, label, validate=False):
    if value is None:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{label} must be a list of non-empty strings")
    if validate:
        for item in value:
            _validate_registry_name(item)
    return tuple(value)


def _required_names(value, label):
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{label} must be a list of non-empty strings")
    seen = set()
    duplicates = []
    for item in value:
        _validate_registry_name(item)
        if item in seen:
            duplicates.append(item)
        seen.add(item)
    if duplicates:
        raise ValueError(f"{label} has duplicate names: {', '.join(sorted(set(duplicates)))}")
    return tuple(value)


def _metadata_strings(entry, key, artifact_name):
    value = entry.get(key, [])
    try:
        return _strings(value, f"registry skill {artifact_name!r} {key}", key == "dependencies")
    except ValueError as exc:
        raise ValueError(f"registry skill {artifact_name!r} has malformed {key}") from exc


def _from_registry(claude, kind):
    path = claude / REGISTRY_FILE[kind]
    if not path.is_file():
        return {}
    registry = _read_json(path)
    collection = COLLECTION[kind]
    upstream = registry.get("upstream") or {}
    out = {}
    for name, entry, repo_key in registry_entries(registry, collection):
        source_root = claude / collection
        source = source_root / f"{name}{SUFFIX[kind]}"
        containment = _containment_error(kind, source, source_root)
        declared = None
        if containment is None:
            declared = _frontmatter_name(source / "SKILL.md") if kind == SKILL else _frontmatter_name(source)
        metadata_error = None
        if kind == AGENT and containment is None and source.exists():
            metadata_error = _agent_description_error(source, f"agent '{name}'")
        mismatch = None
        if containment is None and source.exists() and declared != name:
            shown = declared if declared is not None else "missing"
            mismatch = f"registry name '{name}', source name '{shown}', and directory name '{source.stem}' must agree"
        dependency_only = entry.get("dependency_only", False)
        if not isinstance(dependency_only, bool):
            raise ValueError(f"registry skill {name!r} has malformed dependency_only")
        out[name] = Artifact(
            name=name,
            type=kind,
            groups=_metadata_strings(entry, "groups", name),
            dependencies=_metadata_strings(entry, "dependencies", name),
            dependency_only=dependency_only,
            source=source,
            origin=repo_key or "local",
            upstream_repo=repo_key,
            upstream_path=entry.get("upstream_path") if repo_key else None,
            upstream_branch=upstream[repo_key].get("branch") if repo_key else None,
            updated_at=entry.get("updated_at"),
            metadata=True,
            catalog_error=containment or metadata_error or mismatch,
        )
    return out


def _root_agents(claude):
    catalog = {}
    registered_agents = _from_registry(claude, AGENT)
    agent_root = claude / STORE[AGENT]
    if agent_root.is_dir():
        for path in sorted(agent_root.glob("*.md")):
            name = _validate_registry_name(path.stem)
            declared = _frontmatter_name(path)
            art = registered_agents.pop(name, None)
            if art is None and declared in registered_agents:
                metadata_art = registered_agents.pop(declared)
                art = replace(
                    metadata_art,
                    source=path,
                    catalog_error=(
                        f"registry name '{declared}', source name '{declared}', and "
                        f"file name '{path.stem}' must agree"
                    ),
                )
            if art is None:
                art = Artifact(name=name, type=AGENT, source=path, metadata=False)
            containment = _containment_error(AGENT, path, agent_root)
            metadata_error = None
            if containment is None:
                metadata_error = _agent_description_error(path, f"agent '{art.name}'")
            mismatch = None
            if containment is None and declared != art.name:
                shown = declared if declared is not None else "missing"
                mismatch = f"agent name '{art.name}', source name '{shown}', and file name '{path.stem}' must agree"
            if containment or metadata_error or mismatch:
                art = replace(art, catalog_error=containment or metadata_error or mismatch)
            catalog[(AGENT, art.name)] = art
    for name, art in registered_agents.items():
        catalog[(AGENT, name)] = art
    return catalog


def _owned_skill(path, root, bundle_name):
    name = _validate_registry_name(path.parent.name)
    declared = _frontmatter_name(path)
    containment = _containment_error(SKILL, path.parent, root)
    mismatch = None
    if containment is None and declared != name:
        shown = declared if declared is not None else "missing"
        mismatch = f"bundle '{bundle_name}' skill name '{name}', source name '{shown}', and directory name '{path.parent.name}' must agree"
    return Artifact(name=name, type=SKILL, source=path.parent, catalog_error=containment or mismatch)


def _owned_agent(path, root, bundle_name):
    name = _validate_registry_name(path.stem)
    declared = _frontmatter_name(path)
    containment = _containment_error(AGENT, path, root)
    mismatch = None
    if containment is None and declared != name:
        shown = declared if declared is not None else "missing"
        mismatch = f"bundle '{bundle_name}' agent name '{name}', source name '{shown}', and file name '{path.stem}' must agree"
    description_error = None if containment else _agent_description_error(path, f"bundle '{bundle_name}' agent '{name}'")
    return Artifact(name=name, type=AGENT, source=path, catalog_error=containment or mismatch or description_error)


def _bundle_requires(path):
    data = _read_json(path)
    requires = data.get("requires", {})
    if not isinstance(requires, dict):
        raise ValueError(f"{path} requires must be an object")
    unknown = sorted(set(requires) - {"skills", "agents"})
    if unknown:
        raise ValueError(f"{path} requires has unknown keys: {', '.join(unknown)}")
    return (
        _required_names(requires.get("skills", []), f"{path} requires.skills"),
        _required_names(requires.get("agents", []), f"{path} requires.agents"),
    )


def _from_bundles(claude):
    out = {}
    root = claude / "bundles"
    if not root.is_dir():
        return out
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        name = _validate_registry_name(directory.name)
        marker = directory / BUNDLE_MARKER
        if not marker.is_file():
            continue
        containment = _containment_error(BUNDLE, directory, root)
        marker_containment = _containment_error(BUNDLE, marker, directory)
        if marker_containment:
            requires_skills, requires_agents = (), ()
        else:
            requires_skills, requires_agents = _bundle_requires(marker)
        skills_root = directory / STORE[SKILL]
        agents_root = directory / STORE[AGENT]
        owned_skills = tuple(
            _owned_skill(path, skills_root, name)
            for path in sorted(skills_root.glob("*/SKILL.md"))
        )
        owned_agents = tuple(
            _owned_agent(path, agents_root, name)
            for path in sorted(agents_root.glob("*.md"))
        )
        missing = None
        if not owned_skills or not owned_agents:
            missing = f"bundle '{name}' must contain at least one agent and one skill"
        errors = tuple(
            art.catalog_error for art in (*owned_skills, *owned_agents) if art.catalog_error
        )
        out[(BUNDLE, name)] = Bundle(
            name=name,
            source=directory,
            agents=owned_agents,
            skills=owned_skills,
            requires_skills=requires_skills,
            requires_agents=requires_agents,
            catalog_error=containment or marker_containment or missing or (errors[0] if errors else None),
        )
    return out


def _from_plugins(claude):
    out = {}
    root = claude / "plugins"
    if not root.is_dir():
        return out
    for directory in sorted(root.iterdir()):
        manifest = directory / PLUGIN_MANIFEST
        if not manifest.is_file():
            continue
        data = _read_json(manifest)
        # A plugin bundles only what it owns. A skill vendored from upstream stays
        # under skills/ and is named here, so `update` keeps syncing it by
        # upstream_path instead of it forking inside a plugin.
        out[directory.name] = Artifact(
            name=directory.name,
            type=PLUGIN,
            groups=tuple(data.get("groups") or ()),
            dependencies=tuple(data.get(PLUGIN_DEPS_KEY) or ()),
            source=directory,
            origin="local",
            manifest_keys=tuple(data.keys()),
            metadata=True,
        )
    return out


def build_catalog(claude):
    """Filesystem skills plus optional metadata, keyed by (type, name)."""
    claude = Path(claude)
    catalog = {}
    registered_skills = _from_registry(claude, SKILL)
    skill_root = claude / STORE[SKILL]
    if skill_root.is_dir():
        for directory in sorted(skill_root.iterdir()):
            skill_file = directory / "SKILL.md"
            if not directory.is_dir() or not skill_file.is_file():
                continue
            declared = _frontmatter_name(skill_file)
            art = registered_skills.pop(directory.name, None)
            if art is None and declared in registered_skills:
                metadata_art = registered_skills.pop(declared)
                art = replace(
                    metadata_art,
                    source=directory,
                    catalog_error=(
                        f"registry name '{declared}', source name '{declared}', and "
                        f"directory name '{directory.name}' must agree"
                    ),
                )
            if art is None:
                art = Artifact(
                    name=directory.name,
                    type=SKILL,
                    source=directory,
                    metadata=False,
                )
            containment = _containment_error(SKILL, directory, skill_root)
            if containment:
                art = replace(art, catalog_error=containment)
            catalog[(SKILL, art.name)] = art
    for name, art in registered_skills.items():
        catalog[(SKILL, name)] = art

    catalog.update(_root_agents(claude))
    catalog.update(_from_bundles(claude))

    return catalog


def get(catalog, kind, name):
    return catalog.get((kind, name))


def of_type(catalog, kind):
    """Every artifact of one type, name-ordered."""
    return [art for (t, _), art in sorted(catalog.items()) if t == kind]


def skills(catalog):
    """Name-keyed standalone skills. Every dependency edge names a skill, so legacy resolvers want
    this view rather than the pair-keyed catalog."""
    return {art.name: art for (t, _), art in catalog.items() if t == SKILL}


def agents(catalog):
    return {art.name: art for (t, _), art in catalog.items() if t == AGENT}


def bundles(catalog):
    return {bundle.name: bundle for (t, _), bundle in catalog.items() if t == BUNDLE}


def visible(catalog, kind):
    """Artifacts of `kind` that can be named directly, name-ordered.

    A dependency-only skill installs with whichever skill needs it and refuses to be
    added by name, so every surface that offers a choice starts here.
    """
    return [art for art in of_type(catalog, kind) if not art.dependency_only]


def in_group(catalog, kind, tag):
    """Visible artifacts of `kind` carrying `tag`, name-ordered.

    The tag is opaque: exact membership, no case folding and no normalisation, since
    the vocabulary is whatever the registries say and one tag has a space in it. A
    tag nothing carries is an empty list, which each caller reads its own way.
    """
    return [art for art in visible(catalog, kind) if tag in art.groups]


def duplicate_names(catalog):
    """Names used by more than one type, as {name: [types]}."""
    seen = {}
    for (kind, name) in catalog:
        seen.setdefault(name, []).append(kind)
    return {n: sorted(k) for n, k in seen.items() if len(k) > 1}


def _source_key(path):
    return path.resolve() if path is not None else None


@dataclass(frozen=True)
class Resolution:
    names: tuple
    missing_direct: tuple = ()
    missing_dependencies: tuple = ()
    invalid: tuple = ()

    @property
    def complete(self):
        return not self.missing_direct and not self.missing_dependencies and not self.invalid


def resolve(catalog, direct):
    """Deterministic recursive skill closure for directly requested names."""
    skill_map = skills(catalog)
    visited = set()
    reached = set()
    missing_direct = []
    missing_dependencies = []
    invalid = []

    def visit(name, parent=None):
        art = skill_map.get(name)
        if art is None:
            if parent is None:
                missing_direct.append(name)
            else:
                missing_dependencies.append((parent, name))
            return
        if art.catalog_error:
            invalid.append((name, art.catalog_error))
            return
        if not art.source.is_dir():
            if parent is None:
                missing_direct.append(name)
            else:
                missing_dependencies.append((parent, name))
        else:
            reached.add(name)
        if name in visited:
            return
        visited.add(name)
        for dependency in sorted(set(art.dependencies)):
            visit(dependency, name)

    for name in sorted(set(direct)):
        visit(name)
    return Resolution(
        tuple(sorted(reached)),
        tuple(sorted(set(missing_direct))),
        tuple(sorted(set(missing_dependencies))),
        tuple(sorted(set(invalid))),
    )


def global_resolution(catalog):
    roots = [
        art.name
        for art in of_type(catalog, SKILL)
        if art.metadata and art.tagged_global
    ]
    return resolve(catalog, roots)


@dataclass(frozen=True)
class BundleResolution:
    bundles: tuple
    skills: tuple
    agents: tuple
    missing_bundles: tuple = ()
    missing_skills: tuple = ()
    missing_agents: tuple = ()
    missing_dependencies: tuple = ()
    invalid: tuple = ()

    @property
    def names(self):
        return self.skills

    @property
    def complete(self):
        return not (
            self.missing_bundles
            or self.missing_skills
            or self.missing_agents
            or self.missing_dependencies
            or self.invalid
        )


def bundle_resolution(catalog, names, direct_skills=(), direct_agents=()):
    bundle_map = bundles(catalog)
    skill_map = skills(catalog)
    agent_map = agents(catalog)
    requested_bundles = sorted(set(names))
    requested_skills = sorted(set(direct_skills))
    requested_agents = sorted(set(direct_agents))
    reached_bundles = []
    reached_skills = {}
    reached_agents = {}
    missing_bundles = []
    missing_skills = []
    missing_agents = []
    invalid = []

    def add_artifact(target, art, owner):
        previous = target.get(art.name)
        if previous is not None and _source_key(previous.source) != _source_key(art.source):
            invalid.append((art.name, f"{owner} conflicts with {previous.source}"))
            return
        if art.catalog_error:
            invalid.append((art.name, art.catalog_error))
            return
        target[art.name] = art

    for name in requested_skills:
        art = skill_map.get(name)
        if art is None or art.catalog_error or not art.source.is_dir():
            missing_skills.append(name)
            if art is not None and art.catalog_error:
                invalid.append((name, art.catalog_error))
            continue
        add_artifact(reached_skills, art, "direct skill")
    for name in requested_agents:
        art = agent_map.get(name)
        if art is None or art.catalog_error or not art.source.is_file():
            missing_agents.append(name)
            if art is not None and art.catalog_error:
                invalid.append((name, art.catalog_error))
            continue
        add_artifact(reached_agents, art, "direct agent")

    for name in requested_bundles:
        bundle = bundle_map.get(name)
        if bundle is None:
            missing_bundles.append(name)
            continue
        if bundle.catalog_error:
            invalid.append((name, bundle.catalog_error))
            continue
        reached_bundles.append(name)
        for art in bundle.skills:
            add_artifact(reached_skills, art, f"bundle '{name}'")
        for art in bundle.agents:
            add_artifact(reached_agents, art, f"bundle '{name}'")
        for skill_name in bundle.requires_skills:
            art = skill_map.get(skill_name)
            if art is None or art.catalog_error or not art.source.is_dir():
                missing_skills.append(skill_name)
                if art is not None and art.catalog_error:
                    invalid.append((skill_name, art.catalog_error))
            else:
                add_artifact(reached_skills, art, f"bundle '{name}' requirement")
        for agent_name in bundle.requires_agents:
            art = agent_map.get(agent_name)
            if art is None or art.catalog_error or not art.source.is_file():
                missing_agents.append(agent_name)
                if art is not None and art.catalog_error:
                    invalid.append((agent_name, art.catalog_error))
            else:
                add_artifact(reached_agents, art, f"bundle '{name}' requirement")

    for agent in tuple(reached_agents.values()):
        for skill_name in sorted(set(agent.dependencies)):
            art = skill_map.get(skill_name)
            if art is None or art.catalog_error or not art.source.is_dir():
                missing_skills.append(skill_name)
                if art is not None and art.catalog_error:
                    invalid.append((skill_name, art.catalog_error))
            else:
                add_artifact(reached_skills, art, f"agent '{agent.name}' dependency")

    root_skill_names = [
        name
        for name, art in reached_skills.items()
        if (SKILL, name) in catalog and _source_key(catalog[(SKILL, name)].source) == _source_key(art.source)
    ]
    skill_resolution = resolve(catalog, root_skill_names)
    for name in skill_resolution.names:
        art = skill_map.get(name)
        if art is not None:
            add_artifact(reached_skills, art, "skill dependency")
    invalid.extend(skill_resolution.invalid)

    return BundleResolution(
        tuple(sorted(set(reached_bundles))),
        tuple(sorted(reached_skills)),
        tuple(sorted(reached_agents)),
        tuple(sorted(set(missing_bundles))),
        tuple(sorted(set(missing_skills) | set(skill_resolution.missing_direct))),
        tuple(sorted(set(missing_agents))),
        tuple(sorted(set(skill_resolution.missing_dependencies))),
        tuple(sorted(set(invalid))),
    )
