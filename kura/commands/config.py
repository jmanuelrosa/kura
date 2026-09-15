"""Inspect or mutate Kura machine configuration."""

from dataclasses import replace
import sys

from .. import config, errors, harnesses, paths, ui, views
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
        try:
            catalog_root = config.effective_catalog(home, require=mutating)
        except config.Malformed as exc:
            raise common.Refusal(errors.DRIFT, str(exc)) from exc
        if not mutating:
            return _show(machine, catalog_root, home)
        if machine is None:
            raise common.Refusal(errors.NO_PROJECT, "No machine configuration exists. Run `kura init` first.")
        config_path = config.path_for(home)
        if config_path.is_symlink() or not config_path.is_file():
            raise common.Refusal(errors.DRIFT, f"{config_path} is not a regular machine configuration file.")

        selected = _harnesses(args.harnesses)
        updated = replace(machine, global_harnesses=selected)
        catalog = common.loaded_catalog(catalog_root)
        removed_harnesses = set(machine.global_harnesses) - set(selected)
        projection = views.global_plan(
            catalog,
            catalog_root,
            home,
            selected,
            previous_harness_ids=machine.global_harnesses,
            protect_empty_selected=bool(removed_harnesses),
        )
        common.refuse_plan(projection, "change machine configuration")
        if projection.missing:
            raise common.Refusal(errors.DRIFT, "The catalog is missing a registry-global skill.")

        _report_saved_changes(machine, updated)
        common.report_actions(projection.ordered_actions(), dry_run=True)
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
        common.apply_plan(projection, [common.config_action(home, updated, machine)])
        common.report_actions(projection.ordered_actions())
        ui.done(f"Configuration updated, {projection.changes} link changes")
        return errors.OK

    return common.run_guarded(operation)
