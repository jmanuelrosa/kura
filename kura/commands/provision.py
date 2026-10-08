"""Converge registry-global skills and agents across globally enabled harnesses."""

from .. import catalog as cat
from .. import errors, paths, ui, views
from . import common


def run(args):
    def operation():
        if args.type == cat.BUNDLE:
            raise common.Refusal(
                errors.USAGE,
                "Bundles are project-only; `sync` has no global bundle policy to converge. "
                "Use `kura converge --type bundle` in a project.",
            )
        machine, catalog_root = common.machine()
        catalog = common.loaded_catalog(catalog_root)
        plan = views.global_plan(
            catalog,
            catalog_root,
            paths.home(),
            machine.global_harnesses,
            machine_config=machine,
            kinds=(args.type,) if args.type else (cat.SKILL, cat.AGENT),
        )
        common.refuse_plan(plan, "sync global views")
        if plan.missing:
            rows = "\n".join(f"  '{row.name}' is missing from the catalog" for row in plan.missing)
            raise common.Refusal(errors.DRIFT, f"Cannot sync global views.\n{rows}\nNothing was changed.")
        if not args.dry_run:
            common.apply_plan(plan)
        common.report_actions(plan.ordered_actions(), dry_run=args.dry_run)
        counted = (
            f"{len(plan.logical_agents)} global agents"
            if args.type == cat.AGENT
            else f"{len(plan.logical_skills)} global skills"
        )
        ui.done(
            f"{counted} across "
            f"{len(machine.global_harnesses)} harnesses, {plan.changes} changes"
            + (", dry run" if args.dry_run and plan.changes else "")
        )
        return errors.OK

    return common.run_guarded(operation)
