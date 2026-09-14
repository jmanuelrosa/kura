"""Report or mutate trust for every selected, locally available harness."""

import copy
from contextlib import nullcontext
import json

from .. import errors, harnesses, paths, pi_trust, ui, views
from .. import workspace as ws
from ..transaction import Action, snapshot
from . import common


def _available(manifest):
    available = []
    for harness_id in manifest.harnesses:
        if harnesses.executable_available(harness_id):
            available.append(harness_id)
        else:
            print(f"  {harnesses.get(harness_id).display_name}: skipped, executable not found on PATH")
    if not available:
        raise common.Refusal(errors.USAGE, "No selected harness executable is available on PATH.")
    return available


def _claude_state(home, project, mutation=False):
    path = ws.config_path(home)
    before = snapshot(path)
    if before.kind == "symlink":
        raise common.Refusal(errors.DRIFT, f"{path} is a symlink, not a regular Claude Code trust store.")
    if before.kind == "other":
        raise common.Refusal(errors.DRIFT, f"{path} is not a regular Claude Code trust store.")
    if before.kind == "absent":
        if mutation:
            raise common.Refusal(
                errors.USAGE,
                f"{path} does not exist yet. Run Claude Code once before changing trust.",
            )
        return path, {}, None, None, before
    try:
        data = json.loads(before.data.decode())
    except (UnicodeError, ValueError) as exc:
        raise common.Refusal(errors.DRIFT, f"{path} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise common.Refusal(errors.DRIFT, f"{path} does not hold a JSON object")
    key = ws.key_for(project)
    return path, data, key, ws.granted_by(data, key, project), before


def _pi_state(home, project):
    path = pi_trust.store_path(home)
    before = snapshot(path)
    if before.kind != "file" and before.kind != "absent":
        raise common.Refusal(errors.DRIFT, f"{path} is not a regular Pi trust store.")
    try:
        data = {} if before.kind == "absent" else pi_trust.loads_strict(before.data, path)
    except ValueError as exc:
        raise common.Refusal(errors.DRIFT, str(exc)) from exc
    key = pi_trust.key_for(project)
    granted, decision = pi_trust.decided_by(data, project)
    return path, data, key, granted, decision, before


def _targets(home, project, available):
    targets = []
    for harness_id in available:
        if harness_id == "claude":
            targets.append((harness_id, ws.config_path(home), ws.key_for(project)))
        else:
            targets.append((harness_id, pi_trust.store_path(home), pi_trust.key_for(project)))
    return targets


def _report(home, project, available):
    lock = pi_trust.lock(pi_trust.store_path(home)) if "pi" in available else nullcontext()
    rows = []
    drift = False
    try:
        with lock:
            for harness_id in available:
                if harness_id == "claude":
                    _, _, key, granted, _ = _claude_state(home, project)
                    if key is None:
                        text = "not configured; ~/.claude.json is absent"
                        drift = True
                    elif granted == key:
                        text = f"trusted as {ui.path(key)}"
                    elif granted:
                        text = f"inherited trust from {ui.path(granted)}"
                    else:
                        text = "not trusted"
                        drift = True
                else:
                    _, _, key, granted, decision, _ = _pi_state(home, project)
                    if decision is True:
                        text = (
                            f"trusted as {ui.path(key)}"
                            if granted == key
                            else f"inherited trust from {ui.path(granted)}"
                        )
                    elif decision is False:
                        text = f"refused by {ui.path(granted)}"
                        drift = True
                    else:
                        text = "not decided"
                        drift = True
                rows.append((harness_id, text))
    except OSError as exc:
        raise common.Refusal(errors.DRIFT, str(exc)) from exc
    ui.title(f"🔐 Trust for {ui.path(project)}")
    for harness_id, text in rows:
        print(f"  {harnesses.get(harness_id).display_name}: {text}")
    return errors.DRIFT if drift else errors.OK


def _trust_actions(home, project, available, value):
    actions = []
    results = {}
    for harness_id in available:
        if harness_id == "claude":
            path, data, key, _, before = _claude_state(home, project, mutation=True)
            changed = copy.deepcopy(data)
            ws.apply(changed, key, value)
            written = ws.dump(changed).encode()
            mode = before.mode
            results[harness_id] = ws.granted_by(changed, key, project)
        else:
            path, data, key, _, _, before = _pi_state(home, project)
            changed = dict(data)
            changed[key] = value
            written = pi_trust.dump(changed).encode()
            mode = before.mode if before.kind == "file" else 0o600
            results[harness_id] = pi_trust.decided_by(changed, project)
        if before.kind == "absent" or before.data != written:
            actions.append(
                Action(
                    "write",
                    path,
                    data=written,
                    mode=mode,
                    harness=harness_id,
                    expected=before,
                )
            )
    return actions, results


def _mutate(home, project, available, value, dry_run):
    targets = _targets(home, project, available)
    ui.title("Trust targets")
    for harness_id, path, key in targets:
        print(f"  {harnesses.get(harness_id).display_name}: {ui.path(path)} key {ui.path(key)}")
    if dry_run:
        actions, _ = _trust_actions(home, project, available, value)
        if not actions:
            raise common.Refusal(
                errors.ALREADY,
                "Every available selected harness already has that trust decision.",
            )
        ui.note("Nothing written (--dry-run).")
        return errors.OK

    lock = pi_trust.lock(pi_trust.store_path(home)) if "pi" in available else nullcontext()
    try:
        with lock:
            actions, results = _trust_actions(home, project, available, value)
            if not actions:
                raise common.Refusal(
                    errors.ALREADY,
                    "Every available selected harness already has that trust decision.",
                )
            common.apply_plan(views.Plan(actions=actions))
    except OSError as exc:
        raise common.Refusal(errors.DRIFT, str(exc)) from exc

    for harness_id in available:
        profile = harnesses.get(harness_id)
        if value:
            ui.ok(f"{profile.display_name} trusts {ui.path(project)}")
        elif harness_id == "claude":
            granted = results[harness_id]
            if granted is not None:
                ui.warn(
                    f"Updated Claude Code's own key, but trust remains inherited from {ui.path(granted)}."
                )
            else:
                ui.ok(f"Claude Code no longer trusts {ui.path(project)}")
        else:
            ui.ok(f"Pi now refuses {ui.path(project)}")
    ui.done("Trust decisions were saved.")
    ui.note("Restart live harness sessions for the decisions to take effect.")
    return errors.OK


def run(args):
    def operation():
        project = common.project_root()
        manifest = common.manifest(project)
        home = paths.home()
        available = _available(manifest)
        value = True if args.turn_on else False if args.turn_off else None
        if value is None:
            return _report(home, project, available)
        return _mutate(home, project, available, value, args.dry_run)

    return common.run_guarded(operation)
