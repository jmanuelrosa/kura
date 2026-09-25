"""Desired native views and deterministic reconciliation plans."""

from dataclasses import dataclass, field
import os
from pathlib import Path

from . import catalog as cat
from . import harnesses
from .transaction import Action, Snapshot, snapshot

MISSING = "missing"
CURRENT = "linked"
STALE = "stale"
FOREIGN = "foreign"
REAL = "real"


@dataclass(frozen=True)
class EntryState:
    state: str
    path: Path
    target: Path = None
    expected: Snapshot = None


@dataclass
class Plan:
    actions: list = field(default_factory=list)
    current: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    logical_skills: set = field(default_factory=set)
    logical_agents: set = field(default_factory=set)
    notes: list = field(default_factory=list)
    global_fallbacks: dict = field(default_factory=dict)

    @property
    def changes(self):
        return len(self.actions)

    @property
    def refused(self):
        return bool(self.blocked)

    def extend(self, other):
        self.actions.extend(other.actions)
        self.current.extend(other.current)
        self.blocked.extend(other.blocked)
        self.missing.extend(other.missing)
        self.logical_skills.update(other.logical_skills)
        self.logical_agents.update(other.logical_agents)
        self.notes.extend(other.notes)
        for harness_id, names in other.global_fallbacks.items():
            self.global_fallbacks.setdefault(harness_id, set()).update(names)
        return self

    def ordered_actions(self):
        order = {"mkdir": -1, "delete": 0, "relink": 1, "create": 2, "write": 3, "delete-file": 4}
        return sorted(
            self.actions,
            key=lambda action: (
                order.get(action.operation, 9),
                action.harness or "",
                action.skill or "",
                str(action.path),
            ),
        )


def accepted_artifact_roots(catalog_root, kind, old_catalog_roots=()):
    return tuple(
        Path(root) / cat.STORE[kind]
        for root in (catalog_root, *old_catalog_roots)
    )


def accepted_skill_roots(catalog_root, old_catalog_roots=()):
    return accepted_artifact_roots(catalog_root, cat.SKILL, old_catalog_roots)


def accepted_agent_roots(catalog_root, old_catalog_roots=()):
    return accepted_artifact_roots(catalog_root, cat.AGENT, old_catalog_roots)


def _link_target(path, raw):
    target = Path(raw)
    if not target.is_absolute():
        target = Path(path).parent / target
    return Path(os.path.normpath(target))


def _canonical(path):
    return Path(os.path.realpath(path))


def _points_into(target, root):
    try:
        _canonical(target).relative_to(_canonical(root))
    except ValueError:
        return False
    return True


def _points_at(target, source):
    return _canonical(target) == _canonical(source)


def _accepted_target(target, roots, exact_sources=()):
    return any(_points_into(target, root) for root in roots) or any(
        _points_at(target, source) for source in exact_sources
    )


def classify(path, expected, accepted_roots, exact_sources=()):
    path = Path(path)
    before = snapshot(path)
    if before.kind == "symlink":
        target = _link_target(path, before.target)
        if _canonical(target) == _canonical(expected):
            return EntryState(CURRENT, path, target, before)
        if _accepted_target(target, accepted_roots, exact_sources):
            return EntryState(STALE, path, target, before)
        return EntryState(FOREIGN, path, target, before)
    if before.kind != "absent":
        return EntryState(REAL, path, expected=before)
    return EntryState(MISSING, path, expected=before)


def _collision(harness_id, name, state):
    profile = harnesses.get(harness_id)
    if state.state == REAL:
        return f"{profile.display_name}: {state.path} is a real path"
    return f"{profile.display_name}: {state.path} is a foreign symlink -> {state.target}"


def _root_preflight(root, anchor, replace_root=False):
    root = Path(root)
    anchor = Path(anchor)
    try:
        components = root.relative_to(anchor).parts
    except ValueError:
        return f"{root} is outside its native anchor {anchor}", ()
    current = anchor
    expected_ancestors = []
    for component in components:
        current /= component
        before = snapshot(current)
        planned = Snapshot("absent") if current == root and replace_root else before
        expected_ancestors.append((current, planned))
        if before.kind == "symlink":
            if current == root and replace_root:
                continue
            return f"{current} is a directory symlink", tuple(expected_ancestors)
        if before.kind == "file" or (before.kind == "other" and not current.is_dir()):
            return f"{current} is not a directory", tuple(expected_ancestors)
    return None, tuple(expected_ancestors)


def _native_root(kind, harness_id, home, project, machine_config=None):
    if kind == cat.AGENT:
        return harnesses.agent_root(harness_id, home, project, machine_config)
    return harnesses.skill_root(harness_id, home, project)


def _native_path(kind, harness_id, name, home, project, machine_config=None):
    if kind == cat.AGENT:
        return harnesses.agent_path(harness_id, name, home, project, machine_config)
    return harnesses.skill_path(harness_id, name, home, project)


def _root_anchor(project, home):
    return project if project is not None else home


def _source_exists(kind, source):
    if source is None:
        return False
    return source.is_file() if kind == cat.AGENT else source.is_dir()


def _missing_root_message(harness_id, project):
    profile = harnesses.get(harness_id)
    scope = "project" if project is not None else "global"
    return f"{profile.display_name}: {scope} agent path is not configured"


def _add_desired(
    plan,
    harness_id,
    names,
    home,
    project,
    artifact_map,
    roots,
    replace_root=False,
    relink=True,
    kind=cat.SKILL,
    exact_sources=(),
    machine_config=None,
):
    root = _native_root(kind, harness_id, home, project, machine_config)
    if root is None:
        if names:
            plan.blocked.append(_missing_root_message(harness_id, project))
        return None
    collision, expected_ancestors = _root_preflight(
        root,
        _root_anchor(project, home),
        replace_root,
    )
    if collision:
        plan.blocked.append(f"{harnesses.get(harness_id).display_name}: {collision}")
        return None
    for name in sorted(names):
        art = artifact_map.get(name)
        if art is None or not _source_exists(kind, art.source):
            plan.missing.append((name, harness_id, art.source if art else None))
            continue
        path = _native_path(kind, harness_id, name, home, project, machine_config)
        state = (
            EntryState(MISSING, path, expected=Snapshot("absent"))
            if replace_root
            else classify(path, art.source, roots, exact_sources)
        )
        if state.state == CURRENT:
            plan.current.append((harness_id, name, state.path))
        elif state.state == MISSING:
            plan.actions.append(
                Action(
                    "create",
                    state.path,
                    art.source,
                    harness=harness_id,
                    skill=name,
                    expected=state.expected,
                    expected_ancestors=expected_ancestors,
                )
            )
        elif state.state == STALE and relink:
            plan.actions.append(
                Action(
                    "relink",
                    state.path,
                    art.source,
                    harness=harness_id,
                    skill=name,
                    expected=state.expected,
                    expected_ancestors=expected_ancestors,
                )
            )
        elif state.state == STALE:
            plan.blocked.append(
                f"{harnesses.get(harness_id).display_name}: {state.path} points at {state.target}"
            )
        else:
            plan.blocked.append(_collision(harness_id, name, state))
    return expected_ancestors


def _managed_link(path, roots, exact_sources=()):
    before = snapshot(path)
    if before.kind != "symlink":
        return None
    target = _link_target(path, before.target)
    return before if _accepted_target(target, roots, exact_sources) else None


def _same_source(left, right):
    if left is None or right is None:
        return False
    return _canonical(left) == _canonical(right)


def _put_source(target, art):
    if art is not None and art.source is not None:
        target.setdefault(art.name, art)


def _project_resolution(catalog, manifest):
    manifest = manifest or type("EmptyManifest", (), {"skills": (), "agents": (), "bundles": ()})()
    resolution = cat.bundle_resolution(
        catalog,
        manifest.bundles,
        direct_skills=manifest.skills,
        direct_agents=manifest.agents,
    )
    skill_map = {}
    agent_map = {}
    root_skills = cat.skills(catalog)
    root_agents = cat.agents(catalog)
    for name in manifest.skills:
        _put_source(skill_map, root_skills.get(name))
    for name in manifest.agents:
        _put_source(agent_map, root_agents.get(name))
    for name in manifest.bundles:
        bundle = cat.bundles(catalog).get(name)
        if bundle is None:
            continue
        for art in bundle.skills:
            _put_source(skill_map, art)
        for art in bundle.agents:
            _put_source(agent_map, art)
        for required in bundle.requires_skills:
            _put_source(skill_map, root_skills.get(required))
        for required in bundle.requires_agents:
            _put_source(agent_map, root_agents.get(required))
    for name in resolution.skills:
        _put_source(skill_map, root_skills.get(name))
    for name in resolution.agents:
        _put_source(agent_map, root_agents.get(name))
    return resolution, skill_map, agent_map


def project_sources(catalog, manifest):
    _, skill_map, agent_map = _project_resolution(catalog, manifest)
    return skill_map, agent_map


def _exact_sources(artifacts, roots):
    sources = []
    for art in artifacts.values():
        if art.source is None or any(_points_into(art.source, root) for root in roots):
            continue
        sources.append(art.source)
    return tuple(sources)


def _direct_missing_skills(resolution, manifest):
    declared = set(manifest.skills if manifest else ())
    return tuple(name for name in resolution.missing_skills if name in declared)


def _required_missing_skills(resolution, manifest):
    direct = set(_direct_missing_skills(resolution, manifest))
    return tuple(name for name in resolution.missing_skills if name not in direct)


def _add_resolution_findings(plan, resolution, manifest):
    for parent, name in resolution.missing_dependencies:
        plan.blocked.append(f"skill '{parent}' requires missing dependency '{name}'")
    for name, detail in resolution.invalid:
        plan.blocked.append(f"skill '{name}' has invalid catalog metadata: {detail}")
    for name in resolution.missing_bundles:
        plan.blocked.append(f"bundle '{name}' is missing from the catalog")
    for name in _required_missing_skills(resolution, manifest):
        plan.blocked.append(f"skill '{name}' is missing from the catalog")
    for name in resolution.missing_agents:
        plan.blocked.append(f"agent '{name}' is missing from the catalog")
    for name in _direct_missing_skills(resolution, manifest):
        plan.missing.append((name, None, None))


def _global_agent_names(catalog):
    return tuple(
        sorted(
            art.name
            for art in cat.of_type(catalog, cat.AGENT)
            if art.metadata and art.tagged_global
        )
    )


def _global_skill_roots(catalog):
    roots = {
        art.name
        for art in cat.of_type(catalog, cat.SKILL)
        if art.metadata and art.tagged_global
    }
    for name in _global_agent_names(catalog):
        art = cat.get(catalog, cat.AGENT, name)
        if art is not None:
            roots.update(art.dependencies)
    return tuple(sorted(roots))


def _global_skill_resolution(catalog):
    return cat.resolve(catalog, _global_skill_roots(catalog))


def _global_skill_names(catalog):
    return set(_global_skill_resolution(catalog).names)


def _root_skill_selected(catalog, name, art):
    root_art = cat.skills(catalog).get(name)
    return root_art is not None and _same_source(root_art.source, art.source)


def _preflight_for_delete(plan, kind, harness_id, home, project, machine_config, replace_root=False):
    root = _native_root(kind, harness_id, home, project, machine_config)
    if root is None:
        plan.blocked.append(_missing_root_message(harness_id, project))
        return None
    collision, expected_ancestors = _root_preflight(root, _root_anchor(project, home), replace_root)
    if collision:
        plan.blocked.append(f"{harnesses.get(harness_id).display_name}: {collision}")
        return None
    return expected_ancestors


def _delete_unwanted(
    plan,
    kind,
    harness_id,
    names,
    keep,
    home,
    project,
    roots,
    exact_sources,
    expected_ancestors,
    machine_config=None,
):
    if expected_ancestors is None:
        return
    for name in sorted(set(names) - set(keep)):
        path = _native_path(kind, harness_id, name, home, project, machine_config)
        if path is None:
            continue
        before = _managed_link(path, roots, exact_sources)
        if before is not None:
            plan.actions.append(
                Action(
                    "delete",
                    path,
                    harness=harness_id,
                    skill=name,
                    expected=before,
                    expected_ancestors=expected_ancestors,
                )
            )


def project_plan(
    catalog,
    catalog_root,
    home,
    project,
    old_manifest,
    new_manifest,
    global_harnesses=(),
    delete=True,
    old_catalog_roots=(),
    pending_global=(),
    replace_roots=(),
    old_catalog=None,
    relink=True,
    machine_config=None,
):
    plan = Plan()
    skill_roots = accepted_skill_roots(catalog_root, old_catalog_roots)
    agent_roots = accepted_agent_roots(catalog_root, old_catalog_roots)
    new_resolution, new_skill_map, new_agent_map = _project_resolution(catalog, new_manifest)
    old_resolution, old_skill_map, old_agent_map = _project_resolution(old_catalog or catalog, old_manifest)
    skill_exact = _exact_sources(
        {("old", name): art for name, art in old_skill_map.items()}
        | {("new", name): art for name, art in new_skill_map.items()},
        skill_roots,
    )
    agent_exact = _exact_sources(
        {("old", name): art for name, art in old_agent_map.items()}
        | {("new", name): art for name, art in new_agent_map.items()},
        agent_roots,
    )
    plan.logical_skills.update(new_resolution.skills)
    plan.logical_agents.update(new_resolution.agents)
    agent_candidates = (
        set(old_resolution.agents)
        | set(old_manifest.agents if old_manifest else ())
        | set(old_resolution.missing_agents)
        | set(new_resolution.agents)
    )
    _add_resolution_findings(plan, new_resolution, new_manifest)
    removed_bundles = set(old_manifest.bundles if old_manifest else ()) - set(new_manifest.bundles)
    for name in sorted(removed_bundles & set(old_resolution.missing_bundles)):
        plan.blocked.append(f"bundle '{name}' is missing; cannot prove which old links to remove")
    for name, detail in old_resolution.invalid:
        if name in removed_bundles:
            plan.blocked.append(f"bundle '{name}' is invalid; cannot prove old links to remove: {detail}")

    global_names = _global_skill_names(catalog)
    pending = set(pending_global)
    enabled = set(global_harnesses)
    project_names = {}
    for harness_id in new_manifest.harnesses:
        local = set(new_resolution.skills)
        for name in sorted(set(new_resolution.skills) & global_names):
            art = new_skill_map.get(name)
            if art is None or not _root_skill_selected(catalog, name, art):
                continue
            local.discard(name)
            if harness_id not in enabled:
                local.add(name)
                continue
            if (harness_id, name) in pending:
                continue
            destination = harnesses.skill_path(harness_id, name, home)
            status = classify(destination, art.source, skill_roots).state
            if status == MISSING:
                local.add(name)
                plan.global_fallbacks.setdefault(harness_id, set()).add(name)
            elif status != CURRENT:
                plan.blocked.append(
                    f"{harnesses.get(harness_id).display_name}: global link for '{name}' is not current at {destination}"
                )
        project_names[harness_id] = local

    project_agent_names = {}
    global_agents = set(_global_agent_names(catalog))
    for harness_id in new_manifest.harnesses:
        local_agents = set(new_resolution.agents)
        for name in sorted(local_agents & global_agents):
            art = new_agent_map.get(name)
            root_art = cat.agents(catalog).get(name)
            if art is None or root_art is None or not _same_source(art.source, root_art.source):
                continue
            if harness_id not in enabled:
                continue
            destination = harnesses.agent_path(harness_id, name, home, machine_config=machine_config)
            if destination is None:
                continue
            status = classify(destination, art.source, agent_roots).state
            if status == CURRENT:
                local_agents.discard(name)
            elif status != MISSING:
                plan.blocked.append(
                    f"{harnesses.get(harness_id).display_name}: global link for agent '{name}' is not current at {destination}"
                )
        project_agent_names[harness_id] = local_agents

    root_expectations = {}
    selected = set(new_manifest.harnesses)
    replaced = set(replace_roots)
    for harness_id in new_manifest.harnesses:
        root_expectations[(cat.SKILL, harness_id)] = _add_desired(
            plan,
            harness_id,
            project_names[harness_id],
            home,
            project,
            new_skill_map,
            skill_roots,
            replace_root=harness_id in replaced,
            relink=relink,
            kind=cat.SKILL,
            exact_sources=skill_exact,
            machine_config=machine_config,
        )
        root_expectations[(cat.AGENT, harness_id)] = _add_desired(
            plan,
            harness_id,
            project_agent_names[harness_id],
            home,
            project,
            new_agent_map,
            agent_roots,
            replace_root=False,
            relink=relink,
            kind=cat.AGENT,
            exact_sources=agent_exact,
            machine_config=machine_config,
        )

    if delete:
        old_skill_names = (
            set(old_resolution.skills)
            | set(old_manifest.skills if old_manifest else ())
            | {name for _, name in old_resolution.missing_dependencies}
            | set(old_resolution.missing_skills)
        )
        skill_candidates = old_skill_names | set(new_resolution.skills)
        old_harnesses = set(old_manifest.harnesses if old_manifest else ())
        for harness_id in sorted(old_harnesses | selected):
            if harness_id in replaced:
                continue
            if harness_id in selected:
                skill_expectations = root_expectations.get((cat.SKILL, harness_id))
            else:
                skill_expectations = _preflight_for_delete(
                    plan,
                    cat.SKILL,
                    harness_id,
                    home,
                    project,
                    machine_config,
                )
            skill_keep = (
                project_names[harness_id] | set(_direct_missing_skills(new_resolution, new_manifest))
                if harness_id in selected
                else set()
            )
            _delete_unwanted(
                plan,
                cat.SKILL,
                harness_id,
                skill_candidates,
                skill_keep,
                home,
                project,
                skill_roots,
                skill_exact,
                skill_expectations,
                machine_config,
            )

            if agent_candidates:
                if harness_id in selected:
                    agent_expectations = root_expectations.get((cat.AGENT, harness_id))
                else:
                    agent_expectations = _preflight_for_delete(
                        plan,
                        cat.AGENT,
                        harness_id,
                        home,
                        project,
                        machine_config,
                    )
                agent_keep = (
                    project_agent_names[harness_id] | set(new_manifest.agents)
                    if harness_id in selected else set()
                )
                _delete_unwanted(
                    plan,
                    cat.AGENT,
                    harness_id,
                    agent_candidates,
                    agent_keep,
                    home,
                    project,
                    agent_roots,
                    agent_exact,
                    agent_expectations,
                    machine_config,
                )

    unique = {}
    for action in plan.actions:
        unique[action.path] = action
    plan.actions = list(unique.values())
    return plan


def _native_artifact_name(kind, path):
    if kind == cat.AGENT:
        return path.stem if path.suffix == cat.SUFFIX[cat.AGENT] else None
    return path.name


def _append_global_prunes(
    plan,
    managed_existing,
    kind,
    selected,
    previous,
    root_expectations,
    desired,
    home,
    roots,
    prune,
    machine_config=None,
):
    for harness_id in sorted(selected | previous):
        root = _native_root(kind, harness_id, home, None, machine_config)
        if root is None:
            if kind == cat.AGENT and harness_id in previous and harness_id not in selected and desired:
                plan.blocked.append(_missing_root_message(harness_id, None))
            continue
        if harness_id in selected:
            expected_ancestors = root_expectations.get((kind, harness_id))
            if expected_ancestors is None:
                if not root.is_dir():
                    continue
                collision, expected_ancestors = _root_preflight(root, home)
                if collision:
                    plan.blocked.append(f"{harnesses.get(harness_id).display_name}: {collision}")
                    continue
        else:
            if not root.is_dir():
                continue
            collision, expected_ancestors = _root_preflight(root, home)
            if collision:
                plan.blocked.append(f"{harnesses.get(harness_id).display_name}: {collision}")
                continue
        if not root.is_dir():
            continue
        keep = desired if harness_id in selected else set()
        if not (prune or harness_id not in selected):
            continue
        for path in sorted(root.iterdir()):
            name = _native_artifact_name(kind, path)
            if name is None:
                continue
            before = _managed_link(path, roots)
            if name in keep or before is None:
                continue
            managed_existing.append((harness_id, path, name, before, expected_ancestors))


def _add_global_deletes(plan, managed_existing):
    for harness_id, path, name, before, expected_ancestors in managed_existing:
        plan.actions.append(
            Action(
                "delete",
                path,
                harness=harness_id,
                skill=name,
                expected=before,
                expected_ancestors=expected_ancestors,
            )
        )


def global_plan(
    catalog,
    catalog_root,
    home,
    harness_ids,
    desired_names=None,
    previous_harness_ids=(),
    old_catalog_roots=(),
    prune=True,
    empty_guard=True,
    protect_empty_selected=False,
    machine_config=None,
):
    plan = Plan()
    skill_roots = accepted_skill_roots(catalog_root, old_catalog_roots)
    agent_roots = accepted_agent_roots(catalog_root, old_catalog_roots)
    skill_map = cat.skills(catalog)
    agent_map = cat.agents(catalog)
    sync_agents = desired_names is None
    resolution = _global_skill_resolution(catalog) if sync_agents else cat.resolve(catalog, desired_names)
    desired_skills = set(resolution.names)
    desired_agents = set(_global_agent_names(catalog)) if sync_agents else set()
    plan.logical_skills.update(desired_skills)
    plan.logical_agents.update(desired_agents)

    for parent, name in resolution.missing_dependencies:
        plan.blocked.append(f"skill '{parent}' requires missing dependency '{name}'")
    for name, detail in resolution.invalid:
        plan.blocked.append(f"skill '{name}' has invalid catalog metadata: {detail}")
    for name in resolution.missing_direct:
        plan.missing.append((name, None, None))
    if sync_agents:
        for name in sorted(desired_agents):
            art = agent_map.get(name)
            if art is not None and art.catalog_error:
                plan.blocked.append(f"agent '{name}' has invalid catalog metadata: {art.catalog_error}")

    selected = set(harness_ids)
    previous = set(previous_harness_ids)
    root_expectations = {}
    for harness_id in sorted(selected):
        root_expectations[(cat.SKILL, harness_id)] = _add_desired(
            plan,
            harness_id,
            desired_skills,
            home,
            None,
            skill_map,
            skill_roots,
            kind=cat.SKILL,
        )
        if sync_agents and desired_agents:
            root_expectations[(cat.AGENT, harness_id)] = _add_desired(
                plan,
                harness_id,
                desired_agents,
                home,
                None,
                agent_map,
                agent_roots,
                kind=cat.AGENT,
                machine_config=machine_config,
            )

    managed_skills = []
    _append_global_prunes(
        plan,
        managed_skills,
        cat.SKILL,
        selected,
        previous,
        root_expectations,
        desired_skills,
        home,
        skill_roots,
        prune,
    )
    managed_agents = []
    if sync_agents:
        _append_global_prunes(
            plan,
            managed_agents,
            cat.AGENT,
            selected,
            previous,
            root_expectations,
            desired_agents,
            home,
            agent_roots,
            prune,
            machine_config=machine_config,
        )

    selected_skills = [row for row in managed_skills if row[0] in selected]
    selected_agents = [row for row in managed_agents if row[0] in selected]
    blocked_empty_skills = empty_guard and not desired_skills and selected_skills and not protect_empty_selected
    blocked_empty_agents = empty_guard and sync_agents and not desired_agents and selected_agents and not protect_empty_selected
    if blocked_empty_skills:
        plan.blocked.append(
            "global skill metadata resolved to an empty set while managed global skill links still exist"
        )
    if blocked_empty_agents:
        plan.blocked.append(
            "global agent metadata resolved to an empty set while managed global agent links still exist"
        )
    if not blocked_empty_skills:
        if empty_guard and not desired_skills and protect_empty_selected:
            managed_skills = [row for row in managed_skills if row[0] not in selected]
        _add_global_deletes(plan, managed_skills)
    if not blocked_empty_agents:
        if empty_guard and sync_agents and not desired_agents and protect_empty_selected:
            managed_agents = [row for row in managed_agents if row[0] not in selected]
        _add_global_deletes(plan, managed_agents)

    unique = {}
    for action in plan.actions:
        unique[action.path] = action
    plan.actions = list(unique.values())
    return plan


def view_states(catalog_root, catalog, home, project, harness_ids, names):
    roots = accepted_skill_roots(catalog_root)
    skill_map = cat.skills(catalog)
    result = {}
    for name in names:
        art = skill_map.get(name)
        per_harness = {}
        for harness_id in harness_ids:
            path = harnesses.skill_path(harness_id, name, home, project)
            if art is None:
                entry = EntryState(MISSING, path)
            else:
                entry = classify(path, art.source, roots)
            detail = {"state": entry.state}
            if entry.target is not None and entry.state in (FOREIGN, STALE):
                detail["target"] = str(entry.target)
            per_harness[harness_id] = detail
        result[name] = per_harness
    return result
