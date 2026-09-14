"""Initialize one exact directory for declarative multi-harness skills."""

from dataclasses import replace
import os
from pathlib import Path
import sys

from .. import catalog as cat
from .. import config, errors, harnesses, paths, scope, state, ui, views
from ..transaction import Action, snapshot
from . import common


def _input(prompt):
    try:
        return input(prompt)
    except EOFError as exc:
        raise common.Refusal(errors.USAGE, "Input ended before confirmation; nothing was changed.") from exc


def _parse_ids(values, field):
    if not values:
        raise common.Refusal(errors.USAGE, f"Select at least one {field}.")
    selected = sorted(set(values))
    unknown = sorted(set(selected) - set(harnesses.IDS))
    if unknown:
        raise common.Refusal(errors.USAGE, f"Unknown harnesses: {', '.join(unknown)}.")
    return tuple(selected)


def _prompt_harnesses(project):
    detected = harnesses.evidence(project)
    ui.title(f"🔎 Detected harnesses in {ui.path(project)}")
    suggested = []
    for harness_id, facts in detected.items():
        reasons = list(facts["footprints"])
        if reasons:
            suggested.append(harness_id)
        elif facts["executable"]:
            reasons.append(f"{harnesses.get(harness_id).executable} executable on PATH")
            suggested.append(harness_id)
        label = ", ".join(reasons) if reasons else "no project evidence"
        print(f"  {harnesses.get(harness_id).display_name}: {label}")
    default = ",".join(suggested)
    response = _input(f"Harnesses (claude,pi){f' [{default}]' if default else ''}: ").strip()
    values = response.split(",") if response else suggested
    return _parse_ids([value.strip() for value in values if value.strip()], "project harness")


def _machine(args, home, interactive):
    try:
        saved = config.read(home)
    except config.Malformed as exc:
        raise common.Refusal(
            errors.DRIFT,
            f"Invalid machine configuration: {exc}. Fix or remove {config.path_for(home)}, then rerun `kura init`.",
        ) from exc
    machine_flags = args.catalog is not None or bool(args.global_harnesses)
    if saved is not None:
        if machine_flags:
            raise common.Refusal(
                errors.USAGE,
                "Machine configuration already exists. Use `kura config` for machine changes.",
            )
        return saved, False, False
    if config.ENV_CATALOG in os.environ:
        raise common.Refusal(
            errors.USAGE,
            f"Cannot create machine configuration while {config.ENV_CATALOG} is set. "
            f"Unset {config.ENV_CATALOG} and rerun `kura init` so the saved and effective catalogs agree.",
        )

    catalog_explicit = args.catalog is not None
    if args.catalog is None:
        if not interactive:
            raise common.Refusal(errors.USAGE, "First initialization requires --catalog PATH.")
        default = home / config.DEFAULT_CATALOG
        value = _input(f"Catalog [{default}]: ").strip()
        catalog_explicit = bool(value)
        catalog_path = Path(value).expanduser() if value else default
    else:
        catalog_path = Path(args.catalog).expanduser()
    if not catalog_path.is_absolute():
        raise common.Refusal(errors.USAGE, "--catalog must be an absolute path.")

    if args.global_harnesses:
        global_harnesses = _parse_ids(args.global_harnesses, "global harness")
    elif interactive:
        response = _input("Globally enabled harnesses [claude,pi]: ").strip()
        values = response.split(",") if response else list(harnesses.IDS)
        global_harnesses = _parse_ids([value.strip() for value in values], "global harness")
    else:
        raise common.Refusal(errors.USAGE, "First initialization requires --global-harness.")

    if catalog_path.is_symlink() or catalog_path.exists():
        if not catalog_path.is_dir():
            raise common.Refusal(
                errors.DRIFT,
                f"Selected catalog {catalog_path} is not a directory. Choose a catalog directory and rerun `kura init`.",
            )
        if not (catalog_path / cat.STORE[cat.SKILL]).is_dir():
            raise common.Refusal(
                errors.DRIFT,
                f"Catalog {catalog_path} has no skills/ directory. Add skills/ or select a valid catalog.",
            )
        missing = False
    else:
        missing = True
    if missing and not catalog_explicit:
        raise common.Refusal(
            errors.DRIFT,
            f"Inferred catalog {catalog_path} does not exist. Select an existing catalog explicitly with --catalog.",
        )
    if missing and not args.yes and not args.dry_run:
        if not interactive:
            raise common.Refusal(
                errors.USAGE,
                f"Catalog {catalog_path} does not exist. Re-run with --yes to create it.",
            )
        answer = _input(f"Create {catalog_path} with an empty skills/ directory? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            raise common.Refusal(errors.USAGE, "Initialization cancelled; nothing was changed.")
    return config.Config(catalog_path, global_harnesses), True, missing


def _legacy_topology(project, catalog_root, catalog, manifest):
    pi_root = harnesses.project_skill_root(project, "pi")
    claude_root = harnesses.project_skill_root(project, "claude")
    before = snapshot(pi_root)
    if before.kind != "symlink":
        return False, [], None
    target = Path(before.target)
    if not target.is_absolute():
        target = pi_root.parent / target
    if os.path.realpath(target) != os.path.realpath(claude_root):
        return False, [], None
    expected = set(cat.resolve(catalog, manifest.skills).names)
    differences = []
    if claude_root.is_dir():
        for entry in sorted(claude_root.iterdir()):
            if not entry.is_symlink():
                differences.append(f"{entry.name}: real path")
            elif not scope.points_into(entry, Path(catalog_root) / cat.STORE[cat.SKILL]):
                differences.append(f"{entry.name}: foreign symlink -> {scope.link_target(entry)}")
            elif entry.name not in expected:
                differences.append(f"{entry.name}: catalog-backed but not declared or derived")
    return True, differences, before


def _instruction_write(path, data):
    if path.is_symlink():
        raise common.Refusal(
            errors.DRIFT,
            f"Cannot write instruction file {path}: it is a symlink -> {scope.link_target(path)}. "
            "Replace it with a regular file or remove it, then rerun `kura init`.",
        )
    if path.exists() and not path.is_file():
        raise common.Refusal(
            errors.DRIFT,
            f"Cannot write instruction file {path}: it is not a regular file. "
            "Move it aside or replace it with a regular file, then rerun `kura init`.",
        )
    return Action("write", path, data=data, mode=0o644)


def _instruction_actions(project):
    agents = project / "AGENTS.md"
    claude = project / "CLAUDE.md"
    agents_regular = agents.is_file() and not agents.is_symlink()
    claude_regular = claude.is_file() and not claude.is_symlink()
    actions = []
    notes = []
    if not agents_regular and not claude_regular:
        actions.append(_instruction_write(agents, b"# Project instructions\n"))
        actions.append(_instruction_write(claude, b"@AGENTS.md\n"))
        topology = (("+", "AGENTS.md"), ("+", "CLAUDE.md importing AGENTS.md"))
    elif agents_regular and not claude_regular:
        actions.append(_instruction_write(claude, b"@AGENTS.md\n"))
        topology = (("=", "AGENTS.md preserved"), ("+", "CLAUDE.md importing AGENTS.md"))
    elif claude_regular and not agents_regular:
        topology = (("=", "CLAUDE.md preserved"), ("=", "AGENTS.md not created"))
        notes.append("Preserving CLAUDE.md. Pi reads it natively; no instruction migration is required.")
    else:
        topology = (("=", "AGENTS.md preserved"), ("=", "CLAUDE.md preserved"))
        if b"@AGENTS.md" not in claude.read_bytes().splitlines():
            notes.append("Instructions are split. Kura will preserve both files; run `kura doctor`.")
    return actions, notes, topology


def _counted(count, noun, plural=None):
    return f"{count} {noun if count == 1 else plural or noun + 's'}"


def _migration_rows(legacy_rows):
    direct = sorted(
        (name, reason)
        for (kind, name), reason in legacy_rows.items()
        if kind == cat.SKILL and state.is_direct(reason)
    )
    dependencies = sorted(
        (name, reason)
        for (kind, name), reason in legacy_rows.items()
        if kind == cat.SKILL and not state.is_direct(reason)
    )
    return direct, dependencies


def _render_plan(
    args,
    project,
    home,
    machine,
    effective_root,
    declaration,
    catalog,
    combined,
    final,
    instruction_topology,
    notes,
    legacy_rows,
    create_machine,
    create_catalog,
    root_manifest,
):
    verbose = bool(args.verbose)
    resolution = cat.resolve(catalog, declaration.skills)
    derived = sorted(set(resolution.names) - set(declaration.skills))
    legacy_direct, legacy_dependencies = _migration_rows(legacy_rows)
    preserved = [
        (collection, name, reason)
        for collection, entries in sorted(declaration.legacy.items())
        for name, reason in sorted(entries.items())
    ]
    legacy_path = state.legacy_path_for(project)
    delete_legacy = any(
        action.operation == "delete-file" and action.path == legacy_path
        for action in final
    )

    ui.title(f"🧭 Initialize {ui.path(project)}")
    print("\nProject")
    print(f"  Harnesses: {', '.join(declaration.harnesses)}")
    print(f"  Direct skills: {len(declaration.skills) if declaration.skills else 'none'}")

    print("\nInstructions")
    for marker, detail in instruction_topology:
        print(f"  {marker} {detail}")
    for note in notes:
        ui.warn(note)

    if legacy_rows or preserved or delete_legacy:
        print("\nMigration")
        print(f"  {_counted(len(legacy_direct), 'direct skill')} retained")
        print(f"  {_counted(len(legacy_dependencies), 'dependency row')} will be re-derived")
        print(f"  {_counted(len(preserved), 'legacy agent/plugin record')} preserved")
        if delete_legacy:
            print("  .claude/kura.json will be removed after successful convergence")

    native_actions = [
        action
        for action in combined.ordered_actions()
        if action.harness is not None and action.operation in ("create", "relink", "delete")
    ]
    create_count = sum(action.operation == "create" for action in native_actions)
    relink_count = sum(action.operation == "relink" for action in native_actions)
    delete_count = sum(action.operation == "delete" for action in native_actions)
    print("\nNative views")
    if not native_actions and not combined.current and not combined.missing:
        print("  No skill directories are needed yet.")
    else:
        print(
            f"  {_counted(create_count, 'link')} to create, "
            f"{_counted(relink_count, 'link')} to relink, "
            f"{_counted(delete_count, 'link')} to remove, "
            f"{_counted(len(combined.current), 'link')} already current"
        )
        if combined.missing:
            missing_names = sorted({name for name, _, _ in combined.missing})
            verb = "is" if len(missing_names) == 1 else "are"
            print(f"  {_counted(len(missing_names), 'catalog skill')} {verb} missing: {', '.join(missing_names)}")

    state_writes = sum(action.operation == "write" for action in final)
    print("\nState")
    if create_catalog:
        print(f"  Catalog: create {ui.path(machine.catalog / 'skills')}")
    else:
        print(f"  Catalog: use {ui.path(effective_root)}")
    print(f"  Machine configuration: {'create' if create_machine else 'preserve'}")
    print(f"  Project manifest: {'create' if root_manifest is None else 'update'}")
    print(f"  {_counted(state_writes, 'state file')} will be written")

    if not verbose:
        return

    print("\nSkill details")
    if not declaration.skills and not derived:
        print("  No direct or derived project skills")
    for name in declaration.skills:
        print(f"  + {name}: direct, persisted in kura.json")
    for name in derived:
        print(f"  · {name}: derived, not persisted")
    for name in resolution.missing_direct:
        print(f"  ! {name}: direct intent, missing from the catalog")

    if legacy_rows or preserved:
        print("\nMigration details")
        for name, reason in legacy_direct:
            print(f"  + skill {name}: {reason}, retained as direct intent")
        for name, reason in legacy_dependencies:
            print(f"  · skill {name}: {reason}, dropped and re-derived from catalog metadata")
        for collection, name, reason in preserved:
            singular = collection[:-1]
            print(f"  · {singular} {name}: {reason}, preserved as legacy state")

    print("\nNative view details")
    if not native_actions and not combined.current and not combined.missing:
        print("  No native link decisions")
    for action in native_actions:
        profile = harnesses.get(action.harness)
        if action.skill:
            target = f" -> {ui.path(action.target)}" if action.target is not None else ""
            print(
                f"  {action.operation}: {profile.display_name} '{action.skill}' "
                f"at {ui.path(action.path)}{target}"
            )
        else:
            print(f"  {action.operation}: {profile.display_name} native skill root at {ui.path(action.path)}")
    for harness_id, name, path in sorted(combined.current):
        print(f"  current: {harnesses.get(harness_id).display_name} '{name}' at {ui.path(path)}")
    for name, harness_id, source in sorted(
        combined.missing,
        key=lambda row: (row[1] or "", row[0], str(row[2] or "")),
    ):
        owner = f"{harnesses.get(harness_id).display_name}: " if harness_id else ""
        location = f" at {ui.path(source)}" if source else ""
        print(f"  missing: {owner}'{name}'{location}")

    print("\nState details")
    if create_catalog:
        print(f"  mkdir: {ui.path(machine.catalog / 'skills')}")
    for action in final:
        if action.operation == "write":
            label = "machine configuration" if action.path == config.path_for(home) else "project manifest"
            print(f"  write: {label} at {ui.path(action.path)}")
        elif action.operation == "delete-file":
            print(f"  delete after success: legacy manifest at {ui.path(action.path)}")


def run(args):
    def operation():
        home = paths.home()
        project = scope.project_root(Path.cwd(), home)
        if project is None:
            raise common.Refusal(
                errors.NO_PROJECT,
                "$HOME is not a project. Run `kura init` in the exact project directory.",
            )
        interactive = sys.stdin.isatty()
        if args.harnesses:
            selected = _parse_ids(args.harnesses, "project harness")
        elif interactive:
            selected = _prompt_harnesses(project)
        else:
            raise common.Refusal(errors.USAGE, "Noninteractive initialization requires --harness.")

        machine, create_machine, create_catalog = _machine(args, home, interactive)
        try:
            effective_root = config.effective_catalog(
                machine,
                home,
                require=not create_catalog,
            )
        except config.Malformed as exc:
            remedy = (
                f"Unset {config.ENV_CATALOG} and rerun `kura init`."
                if config.ENV_CATALOG in os.environ
                else "Restore it or run `kura config --catalog /absolute/path` with a valid catalog."
            )
            raise common.Refusal(
                errors.DRIFT,
                f"Cannot resolve the effective catalog: {exc}. {remedy}",
            ) from exc
        catalog = {} if create_catalog else common.loaded_catalog(effective_root)

        root_path = state.path_for(project)
        root_before = snapshot(root_path)
        if root_before.kind not in ("absent", "file"):
            raise common.Refusal(errors.DRIFT, f"{root_path} is not a regular project manifest.")
        legacy_path = state.legacy_path_for(project)
        legacy_before = snapshot(legacy_path)
        if legacy_before.kind not in ("absent", "file"):
            raise common.Refusal(errors.DRIFT, f"{legacy_path} is not a regular legacy manifest.")
        try:
            root_manifest = state.read_strict(project)
            legacy_exists = legacy_before.kind == "file"
            legacy_rows = state.read_legacy(project)
            migrated = state.migrated(legacy_rows, selected)
            if root_manifest is None:
                declaration = migrated
            elif legacy_exists:
                declaration = state.merge(root_manifest, migrated)
                declaration = replace(declaration, harnesses=selected)
            else:
                declaration = replace(root_manifest, harnesses=selected)
            if root_manifest is None and not legacy_rows:
                declaration = state.Manifest(selected, ())
            elif declaration.harnesses != selected:
                declaration = replace(declaration, harnesses=selected)
        except state.Malformed as exc:
            raise common.Refusal(
                errors.DRIFT,
                f"Cannot migrate project state: {exc}. Fix {root_path} and "
                f"{state.legacy_path_for(project)} so their declarations agree, then rerun `kura init`.",
            ) from exc

        if not catalog and declaration.skills:
            raise common.Refusal(
                errors.DRIFT,
                "A missing catalog cannot migrate existing skill intent. Create or select the catalog first.",
            )

        old_topology, differences, old_topology_before = _legacy_topology(
            project,
            effective_root,
            catalog,
            declaration,
        )
        if differences:
            rows = "\n".join(f"  {row}" for row in differences)
            raise common.Refusal(
                errors.DRIFT,
                f"Cannot replace .agents/skills.\n{rows}\nNo files were changed.",
            )

        combined = views.Plan()
        if create_catalog:
            combined.actions.append(Action("mkdir", machine.catalog / "skills"))
        pending_global = set()
        if create_machine and not create_catalog:
            global_projection = views.global_plan(
                catalog,
                effective_root,
                home,
                machine.global_harnesses,
            )
            common.refuse_plan(global_projection, "initialize global skill views")
            if global_projection.missing:
                raise common.Refusal(errors.DRIFT, "Global skills are missing from the selected catalog.")
            combined.extend(global_projection)
            pending_global = {
                (harness_id, name)
                for harness_id in machine.global_harnesses
                for name in global_projection.logical_skills
            }

        old_manifest = root_manifest or state.Manifest(selected, ())
        projection = views.project_plan(
            catalog,
            effective_root,
            home,
            project,
            old_manifest,
            declaration,
            machine.global_harnesses,
            pending_global=pending_global,
            replace_roots=("pi",) if old_topology else (),
        )
        common.refuse_plan(projection, "initialize this project")
        combined.extend(projection)
        if old_topology:
            pi_root = harnesses.project_skill_root(project, "pi")
            if not any(
                action.operation == "delete" and action.path == pi_root
                for action in combined.actions
            ):
                combined.actions.append(
                    Action(
                        "delete",
                        pi_root,
                        harness="pi",
                        expected=old_topology_before,
                    )
                )

        instruction_actions, notes, instruction_topology = _instruction_actions(project)
        combined.actions.extend(instruction_actions)
        final = []
        if create_machine:
            final.append(common.config_action(home, machine, None))
        final.append(common.manifest_action(project, declaration, root_manifest))
        if legacy_before.kind == "file":
            final.append(Action("delete-file", legacy_path, expected=legacy_before))

        _render_plan(
            args,
            project,
            home,
            machine,
            effective_root,
            declaration,
            catalog,
            combined,
            final,
            instruction_topology,
            notes,
            legacy_rows,
            create_machine,
            create_catalog,
            root_manifest,
        )
        if args.dry_run:
            ui.note("Nothing written (--dry-run).")
            return errors.DRIFT if combined.missing else errors.OK
        if not args.yes:
            if not interactive:
                raise common.Refusal(errors.USAGE, "Apply requires --yes in a noninteractive session.")
            answer = _input("Apply this plan? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                ui.note("Initialization cancelled; nothing was changed.")
                return errors.OK
        common.apply_plan(combined, final)
        ui.done(
            f"Initialized {len(declaration.skills)} direct skills across "
            f"{len(declaration.harnesses)} harnesses, {combined.changes} filesystem changes"
        )
        return errors.DRIFT if combined.missing else errors.OK

    return common.run_guarded(operation)
