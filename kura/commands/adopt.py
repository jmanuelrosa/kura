"""Adopt agreeing catalog-backed native links as direct project intent."""

from dataclasses import replace
import os

from .. import catalog as cat
from .. import errors, harnesses, paths, scope, state, ui, views
from . import common

# Bundles first, then agents, so a skill or agent an adopted bundle or agent already
# brings in is covered rather than recorded again as direct intent.
KINDS = (cat.BUNDLE, cat.AGENT, cat.SKILL)
ADOPTED = {cat.BUNDLE: "bundles", cat.AGENT: "direct agents", cat.SKILL: "direct skills"}


def _project_root(kind, harness_id, project, machine):
    if kind == cat.AGENT:
        return harnesses.project_agent_root(project, harness_id, machine)
    return harnesses.project_skill_root(project, harness_id)


def _native_name(kind, path):
    if kind == cat.AGENT:
        return path.stem if path.suffix == cat.SUFFIX[cat.AGENT] else None
    return path.name


def _installed(kind, catalog, catalog_root, project, manifest, machine):
    sources = cat.skills(catalog) if kind == cat.SKILL else cat.agents(catalog)
    store = catalog_root / cat.STORE[kind]
    by_name = {}
    for harness_id in manifest.harnesses:
        root = _project_root(kind, harness_id, project, machine)
        if root is None:
            continue
        if root.is_symlink():
            hint = " Run `kura init` to migrate the old topology." if kind == cat.SKILL else ""
            raise common.Refusal(errors.DRIFT, f"{root} is a directory symlink.{hint}")
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            name = _native_name(kind, path)
            if name is None or not path.is_symlink() or not scope.points_into(path, store):
                continue
            target = scope.link_target(path)
            by_name.setdefault(name, []).append((harness_id, path, target))
    for name, entries in by_name.items():
        targets = {os.path.realpath(target) for _, _, target in entries}
        if len(targets) > 1:
            detail = "\n".join(
                f"  {harnesses.get(harness_id).display_name}: {path} -> {target}"
                for harness_id, path, target in entries
            )
            raise common.Refusal(errors.DRIFT, f"Cannot adopt '{name}'; selected views disagree.\n{detail}")
        art = sources.get(name)
        if art is None or not all(scope.links_to(path, art.source) for _, path, _ in entries):
            detail = "\n".join(f"  {path} -> {target}" for _, path, target in entries)
            raise common.Refusal(
                errors.DRIFT,
                f"Cannot adopt '{name}'; its catalog target does not match its name.\n{detail}",
            )
    return set(by_name)


def _member_path(art, harness_id, home, project, machine):
    if art.type == cat.AGENT:
        return harnesses.agent_path(harness_id, art.name, home, project, machine)
    return harnesses.skill_path(harness_id, art.name, home, project)


def bundle_views(catalog, home, project, manifest, machine):
    """Undeclared bundles with a linked member, as (complete names, {partial name: unlinked}).

    Only a link pointing at a member's exact source counts as evidence, because
    nothing but that bundle can produce one; a same-name skill or agent elsewhere in
    the catalog says nothing about whether the bundle was installed.
    """
    complete, partial = [], {}
    for bundle in cat.of_type(catalog, cat.BUNDLE):
        if bundle.name in manifest.bundles or bundle.catalog_error or not bundle.source.is_dir():
            continue
        linked, unlinked = False, []
        for harness_id in manifest.harnesses:
            for art in (*bundle.skills, *bundle.agents):
                path = _member_path(art, harness_id, home, project, machine)
                if path is not None and scope.links_to(path, art.source):
                    linked = True
                else:
                    unlinked.append((harness_id, art))
        if not linked:
            continue
        if unlinked:
            partial[bundle.name] = unlinked
        else:
            complete.append(bundle.name)
    return complete, partial


def _closure(catalog, declaration):
    return cat.bundle_resolution(catalog, declaration.bundles, declaration.skills, declaration.agents)


def infer_direct(catalog, installed, covered=()):
    skill_map = cat.skills(catalog)
    candidates = set(installed) - set(covered)
    derived = {
        dependency
        for name in candidates
        for dependency in (skill_map.get(name).dependencies if skill_map.get(name) else ())
        if dependency in candidates
    }
    roots = candidates - derived
    reached = set(covered) | set(cat.resolve(catalog, roots).names)
    for name in sorted(candidates - reached):
        if name in reached:
            continue
        roots.add(name)
        reached.update(cat.resolve(catalog, [name]).names)
    return tuple(sorted(roots)), tuple(sorted(candidates - roots))


def _warn_partial(partial):
    for name, unlinked in sorted(partial.items()):
        detail = ", ".join(
            f"{harnesses.get(harness_id).display_name} {art.type} '{art.name}'"
            for harness_id, art in unlinked
        )
        ui.warn(f"Bundle '{name}' is only partly linked, so it was not adopted. Unlinked: {detail}.")


def run(args):
    def operation():
        selection = getattr(args, "type", None)
        kinds = KINDS if selection is None else (selection,)
        machine, catalog_root = common.machine()
        catalog = common.loaded_catalog(catalog_root)
        project = common.project_root()
        manifest = common.manifest(project)
        home = paths.home()
        installed = {
            kind: _installed(kind, catalog, catalog_root, project, manifest, machine)
            for kind in (cat.AGENT, cat.SKILL)
            if kind in kinds
        }
        added = {kind: () for kind in kinds}
        partial = {}
        derived = ()
        declaration = manifest
        if cat.BUNDLE in kinds:
            complete, partial = bundle_views(catalog, home, project, manifest, machine)
            added[cat.BUNDLE] = tuple(complete)
            if complete:
                declaration = state.upgrade(declaration, bundles=(*declaration.bundles, *complete))
        if cat.AGENT in kinds:
            covered = set(declaration.agents) | set(_closure(catalog, declaration).agents)
            added[cat.AGENT] = tuple(sorted(installed[cat.AGENT] - covered))
            if added[cat.AGENT]:
                declaration = state.upgrade(declaration, agents=(*declaration.agents, *added[cat.AGENT]))
        if cat.SKILL in kinds:
            covered = set(declaration.skills) | set(_closure(catalog, declaration).skills)
            roots, derived = infer_direct(catalog, installed[cat.SKILL], covered)
            added[cat.SKILL] = tuple(sorted(set(roots) - set(declaration.skills)))
            declaration = replace(declaration, skills=tuple(sorted(set(declaration.skills) | set(roots))))
        plan = views.project_plan(
            catalog,
            catalog_root,
            home,
            project,
            manifest,
            declaration,
            machine.global_harnesses,
            delete=False,
            machine_config=machine,
        )
        plan = views.narrow(plan, selection)
        common.refuse_plan(plan, "adopt project intent")
        ui.title("📋 Adopt as direct")
        for kind in kinds:
            suffix = "" if kind == cat.SKILL else f" ({kind})"
            for name in added[kind]:
                print(f"  + {name}{suffix}")
        if derived:
            print("\nDerived, not persisted")
            for name in derived:
                print(f"  · {name}")
        _warn_partial(partial)
        common.report_actions(plan.ordered_actions(), dry_run=True)
        outcome = errors.DRIFT if plan.missing or partial else errors.OK
        if args.dry_run:
            ui.note("Nothing written (--dry-run).")
            return outcome
        adopted = any(added.values())
        if adopted or plan.actions:
            common.apply_plan(plan, [common.manifest_action(project, declaration, manifest)])
        common.report_actions(plan.ordered_actions())
        common.warn_global_fallbacks(plan)
        if not adopted and not plan.actions:
            ui.ok(f"Nothing to adopt: every eligible {selection or 'artifact'} is already declared and current.")
        counts = ", ".join(f"{len(added[kind])} {ADOPTED[kind]}" for kind in kinds)
        ui.done(f"{counts} adopted, {plan.changes} link changes")
        return outcome

    return common.run_guarded(operation)
