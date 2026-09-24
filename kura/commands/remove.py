"""Remove direct project intent or temporary global skill links."""

from dataclasses import replace

from .. import catalog as cat
from .. import errors, harnesses, paths, scope, state, ui, views
from ..transaction import Action
from . import common


def _project_names(catalog, manifest, args, effective):
    if args.group is None:
        if not args.names:
            raise common.Refusal(errors.USAGE, "Name at least one skill, or pass --group TAG.")
        return list(args.names), []
    if args.names:
        raise common.Refusal(errors.USAGE, "--group takes no skill names in the same call.")
    members = cat.in_group(catalog, cat.SKILL, args.group)
    if not members:
        raise common.Refusal(errors.NOT_FOUND, f"No skill carries the tag '{args.group}'.")
    selected = [
        art.name
        for art in members
        if art.name in manifest.skills and not scope.belongs_global(art, effective)
    ]
    absent = [art.name for art in members if art.name not in selected]
    return selected, absent


def _project(args, machine, catalog_root, catalog):
    home = paths.home()
    project = common.project_root()
    manifest = common.manifest(project)
    effective = scope.global_set(catalog)
    names, absent = _project_names(catalog, manifest, args, effective)
    if not names:
        if args.group is not None:
            ui.note(f"No direct project skills tagged '{args.group}' are configured.")
            return errors.OK
        raise common.Refusal(errors.NOT_INSTALLED, f"'{args.names[0]}' is not directly configured.")
    missing = [name for name in names if name not in manifest.skills]
    if missing:
        raise common.Refusal(
            errors.NOT_INSTALLED,
            f"Not directly configured: {', '.join(sorted(missing))}.",
        )
    declaration = replace(manifest, skills=tuple(sorted(set(manifest.skills) - set(names))))
    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        manifest,
        declaration,
        machine.global_harnesses,
    )
    common.refuse_plan(plan, "remove the selected skills")
    common.apply_plan(plan, [common.manifest_action(project, declaration, manifest)])
    common.report_actions(plan.ordered_actions())
    common.warn_global_fallbacks(plan)
    for name in sorted(names):
        ui.ok(f"Removed '{name}' from {ui.path(state.path_for(project))}")
    if args.no_cascade:
        ui.note("--no-cascade is retained for compatibility; dependencies are derived from kura.json.")
    if absent:
        ui.note(f"Not direct project intent: {', '.join(absent)}")
    ui.done(f"{len(names)} skill changes, {plan.changes} link changes")
    return errors.DRIFT if plan.missing else errors.OK


def _global_names(catalog, args, effective):
    if args.group is None:
        if not args.names:
            raise common.Refusal(errors.USAGE, "Name at least one skill, or pass --group TAG.")
        return list(args.names), []
    if args.names:
        raise common.Refusal(errors.USAGE, "--group takes no skill names in the same call.")
    members = cat.in_group(catalog, cat.SKILL, args.group)
    if not members:
        raise common.Refusal(errors.NOT_FOUND, f"No skill carries the tag '{args.group}'.")
    selected = [art.name for art in members if scope.belongs_global(art, effective)]
    elsewhere = [art.name for art in members if art.name not in selected]
    return selected, elsewhere


def _global(args, machine, catalog_root, catalog):
    names, elsewhere = _global_names(catalog, args, scope.global_set(catalog))
    if not names:
        ui.note(f"No global skills are in the '{args.group}' group.")
        ui.done("0 skill changes, 0 link changes")
        return errors.OK
    roots = views.accepted_skill_roots(catalog_root)
    plan = views.Plan()
    found = set()
    for harness_id in machine.global_harnesses:
        for name in sorted(names):
            art = cat.get(catalog, cat.SKILL, name)
            if art is None:
                raise common.Refusal(errors.NOT_FOUND, f"'{name}' is not a known skill.")
            home = paths.home()
            root = harnesses.global_skill_root(home, harness_id)
            collision, expected_ancestors = views._root_preflight(root, home)
            if collision:
                plan.blocked.append(
                    f"{harnesses.get(harness_id).display_name}: {collision}"
                )
                continue
            path = harnesses.skill_path(harness_id, name, home)
            current = views.classify(path, art.source, roots)
            if current.state in (views.CURRENT, views.STALE):
                plan.actions.append(
                    Action(
                        "delete",
                        path,
                        harness=harness_id,
                        skill=name,
                        expected=current.expected,
                        expected_ancestors=expected_ancestors,
                    )
                )
                found.add(name)
            elif current.state in (views.REAL, views.FOREIGN):
                plan.blocked.append(
                    f"{harnesses.get(harness_id).display_name}: {path} is not a managed link"
                )
    common.refuse_plan(plan, "remove the selected global skills")
    if not found:
        raise common.Refusal(errors.NOT_INSTALLED, "None of the selected skills is linked globally.")
    common.apply_plan(plan)
    common.report_actions(plan.ordered_actions())
    if elsewhere:
        ui.note(f"The project half of '{args.group}' is untouched: {', '.join(elsewhere)}")
    ui.note("The next `kura sync` restores registry-global policy.")
    ui.done(f"{len(found)} skills selected, {plan.changes} links removed")
    return errors.OK


def run(args):
    def operation():
        machine, catalog_root = common.machine()
        catalog = common.loaded_catalog(catalog_root)
        if args.want_global:
            return _global(args, machine, catalog_root, catalog)
        return _project(args, machine, catalog_root, catalog)

    return common.run_guarded(operation)
