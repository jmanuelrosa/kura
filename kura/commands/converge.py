"""Reconcile every selected native skill view with project intent."""

import sys
from pathlib import Path

from .. import errors, harnesses, paths, projects, ui, views
from . import common


def _plan(machine, catalog_root, catalog, project):
    try:
        manifest = common.manifest(project)
    except common.Refusal as exc:
        return None, None, [exc.message]
    plan = views.project_plan(
        catalog,
        catalog_root,
        paths.home(),
        project,
        manifest,
        manifest,
        machine.global_harnesses,
    )
    problems = list(plan.blocked)
    problems.extend(f"'{name}' is declared but missing from the catalog" for name, _, _ in plan.missing)
    return manifest, plan, problems


def _report(project, manifest, plan, args, stream):
    if not args.quiet:
        common.report_actions(plan.ordered_actions(), dry_run=args.dry_run)
    if (not args.all or args.verbose) and not args.quiet:
        for harness_id in manifest.harnesses:
            physical = len(
                [row for row in plan.current if row[0] == harness_id]
                + [row for row in plan.actions if row.harness == harness_id and row.operation != "delete"]
            )
            ui.ok(f"{harnesses.get(harness_id).display_name} skill view is current ({physical} links).")


def run(args):
    def operation():
        machine, catalog_root = common.machine()
        catalog = common.loaded_catalog(catalog_root)
        if args.roots and not args.all:
            raise common.Refusal(errors.USAGE, "--root requires --all.")
        if args.all:
            roots = [Path(root) for root in args.roots] if args.roots else None
            try:
                found = projects.discover(paths.home(), roots)
            except projects.ScanIncomplete as exc:
                detail = "\n".join(
                    f"  {error.path}: {error.message}" for error in exc.errors
                )
                raise common.Refusal(
                    errors.DRIFT,
                    f"Project scan was incomplete.\n{detail}\n\nNo project was changed.",
                ) from exc
        else:
            found = [common.project_root()]
            common.manifest(found[0])
        if not found:
            if not args.quiet:
                roots = args.roots or [str(Path.cwd())]
                ui.done(f"No initialized projects found under {', '.join(roots)}")
            return errors.OK

        stream = sys.stderr
        planned = []
        drift = False
        for project in found:
            manifest, plan, problems = _plan(machine, catalog_root, catalog, project)
            if problems:
                drift = True
                for detail in problems:
                    ui.warn(f"{ui.path(project)}: {detail}", stream=stream)
            if plan is not None:
                planned.append((project, manifest, plan))

        if drift:
            if not args.quiet:
                ui.note("No project was changed because the complete sweep did not pass preflight.")
            return errors.DRIFT

        combined = views.Plan()
        for _, _, plan in planned:
            combined.extend(plan)
        if not args.dry_run:
            common.apply_plan(combined)
        for project, manifest, plan in planned:
            _report(project, manifest, plan, args, stream)
            if not args.dry_run:
                common.warn_global_fallbacks(
                    plan, project if args.all else None, stream if args.quiet else None
                )

        if not args.quiet:
            word = "project" if len(found) == 1 else "projects"
            ui.done(
                f"{len(found)} {word}, {combined.changes} changes"
                + (", dry run" if args.dry_run and combined.changes else "")
            )
        return errors.OK

    return common.run_guarded(operation)
