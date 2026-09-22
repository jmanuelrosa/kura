"""Inspect or mutate Kura machine configuration.

A mutation on an unconfigured machine bootstraps it: this is the only command that
creates machine configuration, so `init` no longer has to and refuses without one.
"""

from dataclasses import replace
import sys

from .. import config, errors, harnesses, paths, ui, views
from ..transaction import Action
from . import common


def _input(prompt):
    try:
        return input(prompt)
    except EOFError as exc:
        raise common.Refusal(errors.USAGE, "Input ended before confirmation; nothing was changed.") from exc


def _show(machine, catalog_root, home):
    ui.title("⚙ Kura machine configuration")
    print(f"  File: {ui.path(config.path_for(home))}")
    print(f"  Catalog: {ui.path(catalog_root)}")
    if machine is None:
        print("  Saved configuration: none")
        print("  Global harnesses: none")
        return errors.OK
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


def _report_saved_changes(current, updated):
    ui.title("⚙ Machine configuration plan")
    if current is None:
        print(f"  Saved configuration: create with {', '.join(updated.global_harnesses)}")
        return
    if current.global_harnesses == updated.global_harnesses:
        print("  Saved configuration: unchanged")
        return
    before = ", ".join(current.global_harnesses)
    after = ", ".join(updated.global_harnesses)
    print(f"  Global harnesses: {before} -> {after}")


def run(args):
    def operation():
        home = paths.home()
        mutating = args.harnesses is not None
        try:
            machine = config.read(home)
        except config.Malformed as exc:
            raise common.Refusal(errors.DRIFT, f"Invalid machine configuration: {exc}") from exc
        bootstrapping = mutating and machine is None
        try:
            catalog_root = config.effective_catalog(home, require=mutating and not bootstrapping)
        except config.Malformed as exc:
            raise common.Refusal(errors.DRIFT, str(exc)) from exc
        if not mutating:
            return _show(machine, catalog_root, home)
        interactive = sys.stdin.isatty()

        if not bootstrapping:
            config_path = config.path_for(home)
            if config_path.is_symlink() or not config_path.is_file():
                raise common.Refusal(errors.DRIFT, f"{config_path} is not a regular machine configuration file.")

        selected = _harnesses(args.harnesses)
        create_catalog = (
            common.bootstrap_catalog(home, interactive, args.yes, args.dry_run, _input)
            if bootstrapping
            else False
        )
        updated = config.Config(selected) if machine is None else replace(machine, global_harnesses=selected)
        catalog = {} if create_catalog else common.loaded_catalog(catalog_root)
        previous = machine.global_harnesses if machine is not None else ()
        removed_harnesses = set(previous) - set(selected)
        projection = views.global_plan(
            catalog,
            catalog_root,
            home,
            selected,
            previous_harness_ids=previous,
            protect_empty_selected=bool(removed_harnesses),
        )
        common.refuse_plan(projection, "change machine configuration")
        if projection.missing:
            raise common.Refusal(errors.DRIFT, "The catalog is missing a registry-global skill.")
        if create_catalog:
            projection.actions.append(Action("mkdir", catalog_root / "skills"))

        _report_saved_changes(machine, updated)
        if create_catalog:
            print(f"  Catalog: create {ui.path(catalog_root / 'skills')}")
        common.report_actions(projection.ordered_actions(), dry_run=True)
        if args.dry_run:
            ui.note("Nothing written (--dry-run).")
            return errors.OK
        if not args.yes:
            if not interactive:
                raise common.Refusal(errors.USAGE, "A noninteractive configuration change requires --yes.")
            answer = _input("Apply this plan? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                ui.note("Configuration change cancelled; nothing was changed.")
                return errors.OK
        common.apply_plan(projection, [common.config_action(home, updated, machine)])
        common.report_actions(projection.ordered_actions())
        ui.done(
            f"Configuration {'created' if bootstrapping else 'updated'}, {projection.changes} link changes"
        )
        return errors.OK

    return common.run_guarded(operation)
