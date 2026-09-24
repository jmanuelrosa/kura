"""Restore missing selected harness views without deleting anything."""

from .. import errors, paths, ui, views
from . import common


def run(args):
    def operation():
        machine, catalog_root = common.machine()
        catalog = common.loaded_catalog(catalog_root)
        project = common.project_root()
        manifest = common.manifest(project)
        plan = views.project_plan(
            catalog,
            catalog_root,
            paths.home(),
            project,
            manifest,
            manifest,
            machine.global_harnesses,
            delete=False,
            relink=False,
        )
        common.refuse_plan(plan, "restore this project")
        if not args.dry_run:
            common.apply_plan(plan)
        common.report_actions(plan.ordered_actions(), dry_run=args.dry_run)
        if not args.dry_run:
            common.warn_global_fallbacks(plan)
        if plan.missing:
            for name, harness_id, source in plan.missing:
                ui.warn(f"'{name}' remains declared but is missing from the catalog.")
        if not plan.actions and not plan.missing:
            ui.ok(
                "Project declaration is already restored across "
                + " and ".join(manifest.harnesses)
                + "."
            )
        ui.note("Restore deletes nothing.")
        ui.done(f"0 skill changes, {plan.changes} link changes")
        return errors.DRIFT if plan.missing else errors.OK

    return common.run_guarded(operation)
