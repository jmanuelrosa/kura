"""Add direct project intent or temporary global skill links."""

from dataclasses import replace

from .. import catalog as cat
from .. import errors, harnesses, paths, scope, state, ui, views
from . import common


PROJECT_ONLY = {cat.AGENT: "Standalone agents", cat.BUNDLE: "Bundles"}


def _selected_names(catalog, args, effective):
    names = list(args.names)
    if args.group is None:
        if not names:
            raise common.Refusal(errors.USAGE, "Name at least one skill, or pass --group TAG.")
        return names, []
    if names:
        raise common.Refusal(errors.USAGE, "--group takes no skill names in the same call.")
    members = cat.in_group(catalog, cat.SKILL, args.group)
    if not members:
        raise common.Refusal(errors.NOT_FOUND, f"No skill carries the tag '{args.group}'.")
    selected = [art.name for art in members if scope.belongs_global(art, effective) == args.want_global]
    elsewhere = [art.name for art in members if art.name not in selected]
    return selected, elsewhere


def _validate_direct(catalog, names, want_global, effective, declared=()):
    declared = set(declared)
    for name in names:
        art = cat.get(catalog, cat.SKILL, name)
        if art is None:
            if not want_global and name in declared:
                continue
            raise common.Refusal(errors.NOT_FOUND, f"'{name}' is not a known skill.")
        if not art.source.is_dir():
            if not want_global and name in declared:
                continue
            raise common.Refusal(errors.NOT_FOUND, f"'{name}' is missing from the catalog at {art.source}.")
        if art.dependency_only:
            raise common.Refusal(
                errors.DEPENDENCY_ONLY,
                f"'{name}' exists only to satisfy another skill. Add that skill instead.",
            )
        if art.catalog_error:
            raise common.Refusal(errors.DRIFT, f"Cannot install '{name}': {art.catalog_error}.")
        if not want_global and scope.belongs_global(art, effective):
            raise common.Refusal(
                errors.WRONG_SCOPE,
                f"'{name}' is global policy.\n  Run: kura add {name} --type skill --global",
            )


def _project(args, machine, catalog_root, catalog, names, elsewhere, project, manifest):
    if not names:
        ui.note(f"No project skills are in the '{args.group}' group.")
        ui.done("0 skill changes, 0 link changes")
        return errors.OK
    home = paths.home()
    additions = set(names) - set(manifest.skills)
    declaration = replace(manifest, skills=tuple(sorted(set(manifest.skills) | set(names))))
    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        manifest,
        declaration,
        machine.global_harnesses,
        machine_config=machine,
    )
    common.refuse_plan(plan, "add the selected skills")
    healthy = not additions and not plan.actions and not plan.missing
    if healthy:
        raise common.Refusal(errors.ALREADY, f"'{names[0]}' is already configured and current.")
    common.apply_plan(plan, [common.manifest_action(project, declaration, manifest)])
    common.report_actions(plan.ordered_actions())
    common.warn_global_fallbacks(plan)
    for name in sorted(additions):
        ui.ok(f"Added '{name}' to {ui.path(state.path_for(project))}")
    if elsewhere:
        ui.note(f"The global half of '{args.group}' is untouched: {', '.join(elsewhere)}")
    ui.done(f"{len(additions)} skill changes, {plan.changes} link changes")
    if plan.missing:
        for name, harness_id, source in plan.missing:
            ui.warn(f"'{name}' remains declared but is missing from the catalog.")
        return errors.DRIFT
    return errors.OK


def _global(args, machine, catalog_root, catalog, names, elsewhere):
    if not names:
        ui.note(f"No global skills are in the '{args.group}' group.")
        ui.done("0 skill changes, 0 link changes")
        return errors.OK
    home = paths.home()
    plan = views.global_plan(
        catalog,
        catalog_root,
        home,
        machine.global_harnesses,
        desired_names=names,
        prune=False,
        empty_guard=False,
    )
    common.refuse_plan(plan, "add the selected global skills")
    if plan.missing:
        name = plan.missing[0][0]
        raise common.Refusal(errors.NOT_FOUND, f"'{name}' is missing from the catalog.")
    if not plan.actions:
        raise common.Refusal(errors.ALREADY, "Every selected skill is already linked globally.")
    common.apply_plan(plan)
    common.report_actions(plan.ordered_actions())
    if elsewhere:
        ui.note(f"The project half of '{args.group}' is untouched: {', '.join(elsewhere)}")
    ui.note("This is a temporary global addition. The next `kura sync` restores registry policy.")
    ui.done(f"{len(names)} skills selected, {plan.changes} links created")
    return errors.OK


def _plugin_refusal():
    raise common.Refusal(
        errors.USAGE,
        "Claude Code plugins are legacy Kura state. Migrate the content to a portable bundle, then run `kura add NAME --type bundle`.",
    )


def _refuse_project_only(kind):
    label = PROJECT_ONLY[kind]
    raise common.Refusal(errors.WRONG_SCOPE, f"{label} are project-only in this phase; remove `--global`.")


def _explicit_names(args, label):
    if args.group is not None:
        raise common.Refusal(errors.USAGE, f"--group is not supported for {label}; name each {label[:-1]} explicitly.")
    if not args.names:
        raise common.Refusal(errors.USAGE, f"Name at least one {label[:-1]}.")
    return list(args.names)


def _without_legacy_plugins(manifest, names):
    legacy = {collection: dict(entries) for collection, entries in manifest.legacy.items()}
    plugins = legacy.get("plugins")
    if not plugins:
        return manifest.legacy
    for name in names:
        plugins.pop(name, None)
    if not plugins:
        legacy.pop("plugins", None)
    return {collection: entries for collection, entries in legacy.items() if entries}


def _validate_agent(catalog, names, declared=()):
    declared = set(declared)
    for name in names:
        art = cat.get(catalog, cat.AGENT, name)
        if art is None:
            if name in declared:
                continue
            raise common.Refusal(errors.NOT_FOUND, f"'{name}' is not a known standalone agent.")
        if not art.source.is_file():
            if name in declared:
                continue
            raise common.Refusal(errors.NOT_FOUND, f"'{name}' is missing from the catalog at {art.source}.")
        if art.catalog_error:
            raise common.Refusal(errors.DRIFT, f"Cannot install '{name}': {art.catalog_error}.")


def _validate_bundle(catalog, names):
    bundles = cat.bundles(catalog)
    for name in names:
        bundle = bundles.get(name)
        if bundle is None:
            raise common.Refusal(errors.NOT_FOUND, f"'{name}' is not a known bundle.")
        if not bundle.source.is_dir():
            raise common.Refusal(errors.NOT_FOUND, f"'{name}' is missing from the catalog at {bundle.source}.")
        if bundle.catalog_error:
            raise common.Refusal(errors.DRIFT, f"Cannot install '{name}': {bundle.catalog_error}.")


def _project_active(args, machine, catalog_root, catalog, project, manifest):
    kind = args.type
    names = _explicit_names(args, f"{kind}s")
    if kind == cat.AGENT:
        _validate_agent(catalog, names, manifest.agents)
        additions = set(names) - set(manifest.agents)
        declaration = state.upgrade(manifest, agents=tuple(sorted(set(manifest.agents) | set(names))))
        noun = "agent"
    else:
        _validate_bundle(catalog, names)
        additions = set(names) - set(manifest.bundles)
        legacy = _without_legacy_plugins(manifest, additions)
        declaration = state.upgrade(manifest, bundles=tuple(sorted(set(manifest.bundles) | set(names))))
        if legacy != manifest.legacy:
            declaration = replace(declaration, legacy=legacy)
        noun = "bundle"
    plan = views.project_plan(
        catalog,
        catalog_root,
        paths.home(),
        project,
        manifest,
        declaration,
        machine.global_harnesses,
        machine_config=machine,
    )
    common.refuse_plan(plan, f"add the selected {noun}s")
    healthy = not additions and not plan.actions and not plan.missing
    if healthy:
        raise common.Refusal(errors.ALREADY, f"'{names[0]}' is already configured and current.")
    common.apply_plan(plan, [common.manifest_action(project, declaration, manifest)])
    common.report_actions(plan.ordered_actions())
    common.warn_global_fallbacks(plan)
    for name in sorted(additions):
        ui.ok(f"Added '{name}' to {ui.path(state.path_for(project))}")
    ui.done(f"{len(additions)} {noun} changes, {plan.changes} link changes")
    return errors.DRIFT if plan.missing else errors.OK


def run(args):
    def operation():
        if args.type == cat.PLUGIN:
            _plugin_refusal()
        if args.type in PROJECT_ONLY and args.want_global:
            _refuse_project_only(args.type)
        if args.type != cat.SKILL and args.group is not None:
            _explicit_names(args, f"{args.type}s")
        project = manifest = None
        if not args.want_global:
            project = common.project_root()
            manifest = common.manifest(project)
        machine, catalog_root = common.machine()
        catalog = common.loaded_catalog(catalog_root)
        if args.type != cat.SKILL:
            return _project_active(args, machine, catalog_root, catalog, project, manifest)
        effective = scope.global_set(catalog)
        names, elsewhere = _selected_names(catalog, args, effective)
        _validate_direct(
            catalog,
            names,
            args.want_global,
            effective,
            manifest.skills if manifest is not None else (),
        )
        if args.want_global:
            return _global(args, machine, catalog_root, catalog, names, elsewhere)
        return _project(args, machine, catalog_root, catalog, names, elsewhere, project, manifest)

    return common.run_guarded(operation)
