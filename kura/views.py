"""Desired native skill views and deterministic reconciliation plans."""

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
    notes: list = field(default_factory=list)

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
        self.notes.extend(other.notes)
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


def accepted_skill_roots(catalog_root, old_catalog_roots=()):
    return tuple(
        Path(root) / cat.STORE[cat.SKILL]
        for root in (catalog_root, *old_catalog_roots)
    )


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


def classify(path, expected, accepted_roots):
    path = Path(path)
    before = snapshot(path)
    if before.kind == "symlink":
        target = _link_target(path, before.target)
        if _canonical(target) == _canonical(expected):
            return EntryState(CURRENT, path, target, before)
        if any(_points_into(target, root) for root in accepted_roots):
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


def _add_desired(
    plan,
    harness_id,
    names,
    home,
    project,
    skill_map,
    roots,
    replace_root=False,
    relink=True,
):
    root = harnesses.skill_root(harness_id, home, project)
    collision, expected_ancestors = _root_preflight(
        root,
        project if project is not None else home,
        replace_root,
    )
    if collision:
        plan.blocked.append(f"{harnesses.get(harness_id).display_name}: {collision}")
        return None
    for name in sorted(names):
        art = skill_map.get(name)
        if art is None or not art.source.is_dir():
            plan.missing.append((name, harness_id, art.source if art else None))
            continue
        state = (
            EntryState(MISSING, root / name, expected=Snapshot("absent"))
            if replace_root
            else classify(root / name, art.source, roots)
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


def _managed_link(path, roots):
    before = snapshot(path)
    if before.kind != "symlink":
        return None
    target = _link_target(path, before.target)
    return before if any(_points_into(target, root) for root in roots) else None


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
):
    plan = Plan()
    roots = accepted_skill_roots(catalog_root, old_catalog_roots)
    skill_map = cat.skills(catalog)
    new_resolution = cat.resolve(catalog, new_manifest.skills)
    old_resolution = cat.resolve(old_catalog or catalog, old_manifest.skills if old_manifest else ())
    global_resolution = cat.global_resolution(catalog)
    global_names = set(global_resolution.names)
    plan.logical_skills.update(new_resolution.names)

    for parent, name in new_resolution.missing_dependencies:
        plan.blocked.append(f"skill '{parent}' requires missing dependency '{name}'")
    for name, detail in new_resolution.invalid:
        plan.blocked.append(f"skill '{name}' has invalid catalog metadata: {detail}")
    for name in new_resolution.missing_direct:
        plan.missing.append((name, None, None))

    pending = set(pending_global)
    required_global = set(new_resolution.names) & global_names
    enabled = set(global_harnesses)
    for harness_id in new_manifest.harnesses:
        for name in sorted(required_global):
            if harness_id not in enabled:
                plan.blocked.append(
                    f"{harnesses.get(harness_id).display_name}: global harness is not enabled for '{name}'"
                )
                continue
            art = skill_map[name]
            destination = harnesses.skill_path(harness_id, name, home)
            if (harness_id, name) in pending:
                continue
            if classify(destination, art.source, roots).state != CURRENT:
                plan.blocked.append(
                    f"{harnesses.get(harness_id).display_name}: global link for '{name}' is not current at {destination}"
                )

    desired = set(new_resolution.names) - global_names
    root_expectations = {}
    for harness_id in new_manifest.harnesses:
        root_expectations[harness_id] = _add_desired(
            plan,
            harness_id,
            desired,
            home,
            project,
            skill_map,
            roots,
            replace_root=harness_id in set(replace_roots),
            relink=relink,
        )

    if delete:
        old_names = (
            set(old_resolution.names)
            | set(old_manifest.skills if old_manifest else ())
            | {name for _, name in old_resolution.missing_dependencies}
        )
        managed_candidates = old_names | set(new_resolution.names)
        old_harnesses = set(old_manifest.harnesses if old_manifest else ())
        selected = set(new_manifest.harnesses)
        replaced = set(replace_roots)
        for harness_id in sorted(old_harnesses | selected):
            if harness_id in replaced:
                continue
            if harness_id in selected:
                expected_ancestors = root_expectations[harness_id]
                if expected_ancestors is None:
                    continue
            else:
                root = harnesses.skill_root(harness_id, home, project)
                collision, expected_ancestors = _root_preflight(root, project)
                if collision:
                    plan.blocked.append(
                        f"{harnesses.get(harness_id).display_name}: {collision}"
                    )
                    continue
            keep = (desired | set(new_resolution.missing_direct)) if harness_id in selected else set()
            for name in sorted(managed_candidates - keep):
                path = harnesses.skill_path(harness_id, name, home, project)
                before = _managed_link(path, roots)
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

    unique = {}
    for action in plan.actions:
        unique[action.path] = action
    plan.actions = list(unique.values())
    return plan


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
):
    plan = Plan()
    roots = accepted_skill_roots(catalog_root, old_catalog_roots)
    skill_map = cat.skills(catalog)
    resolution = cat.global_resolution(catalog) if desired_names is None else cat.resolve(catalog, desired_names)
    desired = set(resolution.names)
    plan.logical_skills.update(desired)

    for parent, name in resolution.missing_dependencies:
        plan.blocked.append(f"skill '{parent}' requires missing dependency '{name}'")
    for name, detail in resolution.invalid:
        plan.blocked.append(f"skill '{name}' has invalid catalog metadata: {detail}")
    for name in resolution.missing_direct:
        plan.missing.append((name, None, None))

    selected = set(harness_ids)
    previous = set(previous_harness_ids)
    root_expectations = {}
    for harness_id in sorted(selected):
        root_expectations[harness_id] = _add_desired(
            plan,
            harness_id,
            desired,
            home,
            None,
            skill_map,
            roots,
        )

    managed_existing = []
    for harness_id in sorted(selected | previous):
        root = harnesses.global_skill_root(home, harness_id)
        if harness_id in selected:
            expected_ancestors = root_expectations[harness_id]
            if expected_ancestors is None:
                continue
        else:
            collision, expected_ancestors = _root_preflight(root, home)
            if collision:
                plan.blocked.append(f"{harnesses.get(harness_id).display_name}: {collision}")
                continue
        if not root.is_dir():
            continue
        keep = desired if harness_id in selected else set()
        if prune or harness_id not in selected:
            for path in sorted(root.iterdir()):
                before = _managed_link(path, roots)
                if path.name in keep or before is None:
                    continue
                managed_existing.append(
                    (harness_id, path, before, expected_ancestors)
                )

    selected_existing = [row for row in managed_existing if row[0] in selected]
    if empty_guard and not desired and selected_existing and not protect_empty_selected:
        plan.blocked.append(
            "global metadata resolved to an empty set while managed global links still exist"
        )
    else:
        if empty_guard and not desired and protect_empty_selected:
            managed_existing = [row for row in managed_existing if row[0] not in selected]
        for harness_id, path, before, expected_ancestors in managed_existing:
            plan.actions.append(
                Action(
                    "delete",
                    path,
                    harness=harness_id,
                    skill=path.name,
                    expected=before,
                    expected_ancestors=expected_ancestors,
                )
            )

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
