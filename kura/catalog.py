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
    if "repos" in registry or "local_skills" in registry:
        raise ValueError("registry uses retired repos or local_skills keys; use upstream and local")
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


def _frontmatter_name(path):
    if not path.is_file():
        return None
    text = path.read_text()
    if not text.startswith("---\n"):
        return None
    body, marker, _ = text[4:].partition("\n---")
    if not marker:
        return None
    for line in body.splitlines():
        if line.startswith((" ", "\t")):
            continue
        key, separator, value = line.partition(":")
        if not separator or key.strip() not in ("name", '"name"', "'name'"):
            continue
        return value.strip().strip("\"'") or None
    return None


def _metadata_strings(entry, key, artifact_name):
    value = entry.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"registry skill {artifact_name!r} has malformed {key}")
    if key == "dependencies":
        for item in value:
            _validate_registry_name(item)
    return tuple(value)


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
            catalog_error=containment or mismatch,
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

    return catalog


def get(catalog, kind, name):
    return catalog.get((kind, name))


def of_type(catalog, kind):
    """Every artifact of one type, name-ordered."""
    return [art for (t, _), art in sorted(catalog.items()) if t == kind]


def skills(catalog):
    """Name-keyed skills. Every dependency edge names a skill, so resolvers want
    this view rather than the pair-keyed catalog."""
    return {art.name: art for (t, _), art in catalog.items() if t == SKILL}


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
