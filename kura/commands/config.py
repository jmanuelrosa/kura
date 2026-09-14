"""Inspect or mutate Kura machine configuration."""

from dataclasses import replace
import os
from pathlib import Path
import sys

from .. import catalog as cat
from .. import config, errors, harnesses, paths, projects, scope, state, ui, views
from . import common


def _input(prompt):
    try:
        return input(prompt)
    except EOFError as exc:
        raise common.Refusal(errors.USAGE, "Input ended before confirmation; nothing was changed.") from exc


def _show(machine, effective_root, home):
    ui.title("⚙ Kura machine configuration")
    print(f"  File: {ui.path(config.path_for(home))}")
    if machine is None:
        print("  Saved configuration: none")
        print(f"  Effective catalog: {ui.path(effective_root)}")
        if os.environ.get(config.ENV_CATALOG):
            print(f"  Source: {config.ENV_CATALOG}")
        print("  Global harnesses: none")
        return errors.OK
    print(f"  Catalog: {ui.path(machine.catalog)}")
    print(f"  Effective catalog: {ui.path(effective_root)}")
    if os.environ.get(config.ENV_CATALOG):
        print(f"  Source: {config.ENV_CATALOG}")
    print(f"  Global harnesses: {', '.join(machine.global_harnesses)}")
    return errors.OK


def _harnesses(values):
    selected = sorted(set(values))
    if not selected:
        raise common.Refusal(errors.USAGE, "At least one --harness is required.")
    unknown = sorted(set(selected) - set(harnesses.IDS))
    if unknown:
        raise common.Refusal(errors.USAGE, f"Unknown harnesses: {', '.join(unknown)}.")
    return tuple(selected)


def _catalog_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise common.Refusal(errors.USAGE, "--catalog must be an absolute path.")
    if not path.is_dir() or not (path / cat.STORE[cat.SKILL]).is_dir():
        raise common.Refusal(errors.DRIFT, f"{path} is not a catalog with a skills/ directory.")
    return path


def _report_saved_changes(current, updated):
    ui.title("⚙ Machine configuration plan")
    changed = False
    if current.catalog != updated.catalog:
        print(f"  Saved catalog: {ui.path(current.catalog)} -> {ui.path(updated.catalog)}")
        changed = True
    if current.global_harnesses != updated.global_harnesses:
        before = ", ".join(current.global_harnesses)
        after = ", ".join(updated.global_harnesses)
        print(f"  Global harnesses: {before} -> {after}")
        changed = True
    if not changed:
        print("  Saved configuration: unchanged")


def _scan_roots(home, roots):
    try:
        return projects.discover(home, [Path(root) for root in roots])
    except projects.ScanIncomplete as exc:
        detail = "\n".join(
            f"  {error.path}: {error.message}" for error in exc.errors
        )
        raise common.Refusal(
            errors.DRIFT,
            f"Project scan was incomplete.\n{detail}\n\nNo links or configuration were changed.",
        ) from exc


def _unresolved_old_links(project, manifest, catalog, old_root):
    closure = set(cat.resolve(catalog, manifest.skills).names)
    old_skill_root = Path(old_root) / cat.STORE[cat.SKILL]
    unresolved = []
    for harness_id in manifest.harnesses:
        root = harnesses.project_skill_root(project, harness_id)
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError as exc:
            detail = exc.strerror or str(exc)
            unresolved.append(f"{harnesses.get(harness_id).display_name}: cannot inspect {root}: {detail}")
            continue
        for entry in entries:
            if (
                entry.name not in closure
                and entry.is_symlink()
                and scope.points_into(entry, old_skill_root)
            ):
                unresolved.append(
                    f"{harnesses.get(harness_id).display_name}: {entry} -> {scope.link_target(entry)}"
                )
    return unresolved


def run(args):
    def operation():
        if args.roots and args.catalog is None:
            raise common.Refusal(errors.USAGE, "--root applies only with --catalog.")
        home = paths.home()
        mutating = args.catalog is not None or args.harnesses is not None
        try:
            machine = config.read(home)
            effective_root = config.effective_catalog(
                machine,
                home,
                require=mutating and args.catalog is None,
            )
        except config.Malformed as exc:
            raise common.Refusal(errors.DRIFT, f"Invalid machine configuration: {exc}") from exc
        if not mutating:
            return _show(machine, effective_root, home)
        if machine is None:
            raise common.Refusal(errors.NO_PROJECT, "No machine configuration exists. Run `kura init` first.")
        config_path = config.path_for(home)
        if config_path.is_symlink() or not config_path.is_file():
            raise common.Refusal(errors.DRIFT, f"{config_path} is not a regular machine configuration file.")
        if args.catalog is not None and os.environ.get(config.ENV_CATALOG):
            raise common.Refusal(
                errors.USAGE,
                f"Cannot change the saved catalog while {config.ENV_CATALOG} is set.",
            )

        saved_catalog = _catalog_path(args.catalog) if args.catalog is not None else machine.catalog
        operation_catalog_root = saved_catalog if args.catalog is not None else effective_root
        selected = _harnesses(args.harnesses) if args.harnesses is not None else machine.global_harnesses
        updated = replace(machine, catalog=saved_catalog, global_harnesses=selected)
        new_catalog = common.loaded_catalog(operation_catalog_root)
        old_root = machine.catalog if args.catalog is not None else effective_root
        old_catalog_available = (old_root / cat.STORE[cat.SKILL]).is_dir()
        if old_catalog_available:
            try:
                old_catalog = common.loaded_catalog(old_root)
            except common.Refusal:
                old_catalog_available = False
                old_catalog = new_catalog
        else:
            old_catalog = new_catalog
        moved = saved_catalog != machine.catalog
        old_roots = (machine.catalog,) if moved else ()
        removed_harnesses = set(machine.global_harnesses) - set(selected)
        protect_empty_selected = (
            args.catalog is None
            and args.harnesses is not None
            and bool(removed_harnesses)
        )
        global_projection = views.global_plan(
            new_catalog,
            operation_catalog_root,
            home,
            selected,
            previous_harness_ids=machine.global_harnesses,
            old_catalog_roots=old_roots,
            protect_empty_selected=protect_empty_selected,
        )
        common.refuse_plan(global_projection, "change machine configuration")
        if global_projection.missing:
            raise common.Refusal(errors.DRIFT, "The new catalog is missing a registry-global skill.")

        combined = views.Plan().extend(global_projection)
        targets = []
        cwd = Path.cwd()
        if moved:
            cwd_project = scope.project_root(cwd, home)
            if cwd_project is not None and state.path_for(cwd_project).is_file():
                targets.append(cwd_project)
            if args.roots:
                targets.extend(_scan_roots(home, args.roots))
        unique = {str(path.resolve()): path for path in targets}
        pending_global = {
            (harness_id, name)
            for harness_id in selected
            for name in global_projection.logical_skills
        }
        for project in [unique[key] for key in sorted(unique)]:
            manifest = common.manifest(project)
            projection = views.project_plan(
                new_catalog,
                operation_catalog_root,
                home,
                project,
                manifest,
                manifest,
                selected,
                old_catalog_roots=old_roots,
                pending_global=pending_global,
                old_catalog=old_catalog,
            )
            common.refuse_plan(projection, f"move catalog links for {project}")
            if projection.missing:
                names = ", ".join(sorted({name for name, _, _ in projection.missing}))
                raise common.Refusal(
                    errors.DRIFT,
                    f"Cannot move catalog links for {project}: direct skill content is missing: {names}.\n"
                    "No links or configuration were changed.",
                )
            if moved and not old_catalog_available:
                unresolved = _unresolved_old_links(project, manifest, new_catalog, old_root)
                if unresolved:
                    detail = "\n".join(f"  {row}" for row in unresolved)
                    raise common.Refusal(
                        errors.DRIFT,
                        "Unresolved catalog-move drift: the old catalog is unavailable, so these "
                        f"links cannot be proven safely obsolete.\n{detail}\n\n"
                        "No links or configuration were changed.",
                    )
            combined.extend(projection)

        if moved:
            ui.warn("Kura remembers the old catalog only for this command.", stream=sys.stderr)
            ui.warn(
                "Projects outside the scanned roots will keep old links. Those links will become foreign "
                "and must be removed manually before `kura restore` can recreate them.",
                stream=sys.stderr,
            )
        _report_saved_changes(machine, updated)
        common.report_actions(combined.ordered_actions(), dry_run=True)
        if args.dry_run:
            ui.note("Nothing written (--dry-run).")
            return errors.OK
        if not args.yes:
            if not sys.stdin.isatty():
                raise common.Refusal(errors.USAGE, "A noninteractive configuration change requires --yes.")
            answer = _input("Apply this plan? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                ui.note("Configuration change cancelled; nothing was changed.")
                return errors.OK
        common.apply_plan(combined, [common.config_action(home, updated, machine)])
        common.report_actions(combined.ordered_actions())
        ui.done(f"Configuration updated, {combined.changes} link changes")
        return errors.OK

    return common.run_guarded(operation)
