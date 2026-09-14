"""Adopt agreeing catalog-backed native links as direct project intent."""

from dataclasses import replace
import os

from .. import catalog as cat
from .. import errors, harnesses, paths, scope, state, ui, views
from . import common


def _installed(catalog, catalog_root, home, project, manifest):
    skill_map = cat.skills(catalog)
    by_name = {}
    for harness_id in manifest.harnesses:
        root = harnesses.project_skill_root(project, harness_id)
        if root.is_symlink():
            raise common.Refusal(
                errors.DRIFT,
                f"{root} is a directory symlink. Run `kura init` to migrate the old topology.",
            )
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_symlink() or not scope.points_into(path, catalog_root / cat.STORE[cat.SKILL]):
                continue
            target = scope.link_target(path)
            by_name.setdefault(path.name, []).append((harness_id, path, target))
    for name, entries in by_name.items():
        targets = {os.path.realpath(target) for _, _, target in entries}
        if len(targets) > 1:
            detail = "\n".join(
                f"  {harnesses.get(harness_id).display_name}: {path} -> {target}"
                for harness_id, path, target in entries
            )
            raise common.Refusal(errors.DRIFT, f"Cannot adopt '{name}'; selected views disagree.\n{detail}")
        art = skill_map.get(name)
        if art is None or not all(scope.links_to(path, art.source) for _, path, _ in entries):
            detail = "\n".join(f"  {path} -> {target}" for _, path, target in entries)
            raise common.Refusal(
                errors.DRIFT,
                f"Cannot adopt '{name}'; its catalog target does not match its name.\n{detail}",
            )
    return set(by_name)


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


def run(args):
    def operation():
        machine, catalog_root = common.machine()
        catalog = common.loaded_catalog(catalog_root)
        project = common.project_root()
        manifest = common.manifest(project)
        installed = _installed(catalog, catalog_root, paths.home(), project, manifest)
        resolution = cat.resolve(catalog, manifest.skills)
        covered = set(manifest.skills) | set(resolution.names)
        roots, derived = infer_direct(catalog, installed, covered)
        added = set(roots) - set(manifest.skills)
        declaration = replace(manifest, skills=tuple(sorted(set(manifest.skills) | set(roots))))
        plan = views.project_plan(
            catalog,
            catalog_root,
            paths.home(),
            project,
            manifest,
            declaration,
            machine.global_harnesses,
            delete=False,
        )
        common.refuse_plan(plan, "adopt project skills")
        ui.title("📋 Adopt as direct")
        for name in sorted(added):
            print(f"  + {name}")
        if derived:
            print("\nDerived, not persisted")
            for name in derived:
                print(f"  · {name}")
        common.report_actions(plan.ordered_actions(), dry_run=True)
        if args.dry_run:
            ui.note("Nothing written (--dry-run).")
            return errors.OK
        if added or plan.actions:
            common.apply_plan(plan, [common.manifest_action(project, declaration, manifest)])
        common.report_actions(plan.ordered_actions())
        if not added and not plan.actions:
            ui.ok("Nothing to adopt: every eligible skill is already declared and current.")
        ui.done(f"{len(added)} direct skills adopted, {plan.changes} link changes")
        return errors.DRIFT if plan.missing else errors.OK

    return common.run_guarded(operation)
