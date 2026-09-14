"""Shared command loading, refusal, and transaction helpers."""

from pathlib import Path

from .. import catalog as cat
from .. import config, errors, harnesses, paths, scope, state, ui
from ..cli import fail
from ..transaction import Action, Conflict, Failed, apply, snapshot


class Refusal(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def project_root():
    project = scope.project_root(Path.cwd(), paths.home())
    if project is None:
        raise Refusal(
            errors.NO_PROJECT,
            "This directory is not initialized for project operations.\n"
            "  Run `kura init` in this exact directory outside $HOME.",
        )
    return project


def machine(required=True, require_catalog=True):
    home = paths.home()
    try:
        saved = config.read(home)
    except config.Malformed as exc:
        raise Refusal(errors.DRIFT, f"Invalid machine configuration: {exc}") from exc
    if required and saved is None:
        raise Refusal(
            errors.NO_PROJECT,
            "Kura machine configuration does not exist.\n  Run `kura init` in a project.",
        )
    try:
        catalog_root = config.effective_catalog(saved, home, require=require_catalog)
    except config.Malformed as exc:
        raise Refusal(errors.DRIFT, str(exc)) from exc
    return saved, catalog_root


def manifest(project, required=True):
    path = state.path_for(project)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise Refusal(errors.DRIFT, f"{path} is not a regular project manifest.")
    if not path.is_file():
        if required:
            raise Refusal(
                errors.NO_PROJECT,
                "This directory is not initialized for project operations.\n"
                "  Run `kura init` in this exact directory.",
            )
        return None
    try:
        return state.read_strict(project)
    except state.Malformed as exc:
        raise Refusal(errors.DRIFT, f"{path} is invalid: {exc}") from exc


def loaded_catalog(root):
    skill_root = Path(root) / cat.STORE[cat.SKILL]
    if not skill_root.is_dir():
        raise Refusal(errors.DRIFT, f"Catalog {root} has no skills/ directory.")
    try:
        return cat.build_catalog(Path(root))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise Refusal(errors.DRIFT, f"Catalog {root} is invalid: {exc}") from exc


_UNSET = object()


def manifest_action(project, declaration, expected_manifest=_UNSET):
    path = state.path_for(project)
    before = snapshot(path)
    if expected_manifest is None and before.kind != "absent":
        raise Refusal(errors.DRIFT, f"{path} changed after project state was read.")
    if expected_manifest is not _UNSET and expected_manifest is not None:
        try:
            live = state.loads(before.data.decode()) if before.kind == "file" else None
        except (UnicodeError, state.Malformed):
            live = None
        if live != expected_manifest:
            raise Refusal(errors.DRIFT, f"{path} changed after project state was read.")
    mode = before.mode if before.kind == "file" else 0o644
    return Action(
        "write",
        path,
        data=state.dump(declaration).encode(),
        mode=mode,
        expected=before,
    )


def config_action(home, machine_config, expected_config=_UNSET):
    path = config.path_for(home)
    before = snapshot(path)
    if expected_config is None and before.kind != "absent":
        raise Refusal(errors.DRIFT, f"{path} changed after machine configuration was read.")
    if expected_config is not _UNSET and expected_config is not None:
        try:
            live = config.loads(before.data.decode()) if before.kind == "file" else None
        except (UnicodeError, config.Malformed):
            live = None
        if live is None or live.as_dict() != expected_config.as_dict():
            raise Refusal(errors.DRIFT, f"{path} changed after machine configuration was read.")
    mode = before.mode if before.kind == "file" else 0o600
    return Action(
        "write",
        path,
        data=config.dump(machine_config).encode(),
        mode=mode,
        expected=before,
    )


def apply_plan(plan, final_actions=()):
    try:
        apply([*plan.ordered_actions(), *final_actions])
    except Failed as exc:
        message = f"Filesystem transaction failed: {exc.cause}."
        if exc.rollback_errors:
            details = "; ".join(f"{path}: {error}" for path, error in exc.rollback_errors)
            raise Refusal(errors.DRIFT, f"{message}\n  Rollback also failed: {details}") from exc
        code = errors.DRIFT if isinstance(exc.cause, Conflict) else errors.USAGE
        raise Refusal(code, f"{message}\n  All earlier changes were rolled back.") from exc


def refuse_plan(plan, operation):
    if not plan.refused:
        return
    detail = "\n".join(f"  {item}" for item in plan.blocked)
    raise Refusal(
        errors.DRIFT,
        f"Cannot {operation}.\n{detail}\n\nNo manifest or links were changed.",
    )


def report_actions(actions, dry_run=False):
    verbs = {
        "create": "Would link" if dry_run else "Linked",
        "relink": "Would relink" if dry_run else "Relinked",
        "delete": "Would unlink" if dry_run else "Unlinked",
    }
    for action in actions:
        if action.operation not in verbs:
            continue
        profile = harnesses.get(action.harness)
        subject = f"'{action.skill}'" if action.skill else "native skill root"
        ui.ok(f"{profile.display_name}: {verbs[action.operation]} {subject} at {ui.path(action.path)}")


def run_guarded(callback):
    try:
        return callback()
    except Refusal as exc:
        return fail(exc.code, exc.message)
