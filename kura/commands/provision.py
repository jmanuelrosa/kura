"""Converge registry-global skills across globally enabled harnesses."""

from .. import errors, paths, ui, views
from . import common


def run(args):
    def operation():
        machine, catalog_root = common.machine()
        catalog = common.loaded_catalog(catalog_root)
        plan = views.global_plan(
            catalog,
            catalog_root,
            paths.home(),
            machine.global_harnesses,
        )
        common.refuse_plan(plan, "sync global skill views")
        if plan.missing:
            rows = "\n".join(f"  '{name}' is missing from the catalog" for name, _, _ in plan.missing)
            raise common.Refusal(errors.DRIFT, f"Cannot sync global skill views.\n{rows}\nNothing was changed.")
        if not args.dry_run:
            common.apply_plan(plan)
        common.report_actions(plan.ordered_actions(), dry_run=args.dry_run)
        ui.done(
            f"{len(plan.logical_skills)} global skills across "
            f"{len(machine.global_harnesses)} harnesses, {plan.changes} changes"
            + (", dry run" if args.dry_run and plan.changes else "")
        )
        return errors.OK

    return common.run_guarded(operation)
