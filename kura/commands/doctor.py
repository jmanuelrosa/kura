"""Diagnose configuration, catalog, manifests, native views, and trust."""

from pathlib import Path

from .. import catalog as cat
from .. import checks, config, errors, harnesses, paths, pi_trust, scope, state, ui, views
from .. import workspace as ws

PROBLEM = checks.PROBLEM
NOTE = checks.NOTE
Finding = checks.Finding

ORDER = {
    "machine-config": 0,
    "project": 1,
    "project-manifest": 1,
    "catalog": 2,
    "catalog-name": 2,
    "missing-source": 2,
    "missing-dependency": 2,
    "missing-intent": 2,
    "unsafe-view": 3,
    "native-view-drift": 4,
    "trust": 5,
    "executable-absent": 6,
    "split-instructions": 7,
    "legacy-state": 8,
}


def _machine(home, findings):
    try:
        machine = config.read(home)
    except config.Malformed as exc:
        findings.append(Finding("machine-config", PROBLEM, "Machine configuration", str(exc), None))
        machine = None
    if machine is None:
        findings.append(Finding("machine-config", NOTE, "Machine configuration", "not created; run `kura init` in a project", None))
    try:
        root = config.effective_catalog(machine, home)
    except config.Malformed as exc:
        findings.append(Finding("catalog", PROBLEM, "Catalog", str(exc)))
        return machine, None
    return machine, root


def _catalog(root, findings):
    if root is None or not (root / cat.STORE[cat.SKILL]).is_dir():
        if root is not None:
            findings.append(Finding("catalog", PROBLEM, "Catalog", f"{root} has no skills/ directory"))
        return {}
    try:
        catalog = cat.build_catalog(root)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        findings.append(Finding("catalog", PROBLEM, "Catalog", f"cannot be read: {exc}"))
        return {}
    findings.extend(checks.catalog_health(catalog))
    return catalog


def _manifest(project, findings):
    path = state.path_for(project)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        findings.append(
            Finding(
                "project-manifest",
                PROBLEM,
                "Project manifest",
                f"{path} is not a regular project manifest",
                None,
            )
        )
        return None
    if not path.is_file():
        findings.append(
            Finding("project", NOTE, "Project", "not initialized; run `kura init` in this exact directory", None)
        )
        return None
    try:
        return state.read_strict(project)
    except (state.Malformed, OSError) as exc:
        findings.append(Finding("project-manifest", PROBLEM, "Project manifest", f"{path}: {exc}", None))
        return None


def _views(machine, root, catalog, home, project, manifest, findings):
    if root is None:
        return
    if machine is not None:
        global_plan = views.global_plan(catalog, root, home, machine.global_harnesses)
        findings.extend(checks.view_plan(global_plan, "global views"))
    if manifest is None:
        return
    plan = views.project_plan(
        catalog,
        root,
        home,
        project,
        manifest,
        manifest,
        machine.global_harnesses if machine is not None else (),
    )
    findings.extend(checks.view_plan(plan, "project views"))


def _notes(home, project, manifest, findings):
    if manifest is None:
        return
    if "claude" in manifest.harnesses and harnesses.executable_available("claude"):
        try:
            data = ws.read(ws.config_path(home))
            key = ws.key_for(project)
            if ws.granted_by(data, key, project) is None:
                findings.append(Finding("trust", NOTE, "Claude Code trust", "not granted for this project", None))
        except ws.Unreadable:
            findings.append(Finding("trust", NOTE, "Claude Code trust", "cannot be inspected", None))
    if "pi" in manifest.harnesses and harnesses.executable_available("pi"):
        store = pi_trust.read(pi_trust.store_path(home))
        _, decision = pi_trust.decided_by(store, project)
        if decision is not True:
            findings.append(Finding("trust", NOTE, "Pi trust", "not granted for this project", None))
    findings.extend(checks.executable_notes(manifest))
    findings.extend(checks.instruction_notes(project))
    findings.extend(checks.legacy_notes(manifest))


def report(findings):
    ordered = [
        finding
        for _, finding in sorted(
            enumerate(findings),
            key=lambda item: (ORDER.get(item[1].check, len(ORDER)), item[0]),
        )
    ]
    problems = [finding for finding in ordered if finding.is_problem]
    notes = [finding for finding in ordered if not finding.is_problem]
    for rows, title in ((problems, "🚨 Problems:"), (notes, "📝 Notes:")):
        if not rows:
            continue
        ui.title(title)
        for finding in rows:
            print(f"  {finding.subject}: {finding.detail}")
        print()
    if not findings:
        ui.ok("No drift found across configured skill views.")
    else:
        ui.done(f"{len(problems)} problem(s), {len(notes)} note(s) across skills.")
    return errors.DRIFT if problems else errors.OK


def run(args):
    home = paths.home()
    project = scope.project_root(Path.cwd(), home)
    findings = []
    machine, root = _machine(home, findings)
    manifest = _manifest(project, findings) if project is not None else None
    catalog = _catalog(root, findings)
    _views(machine, root, catalog, home, project, manifest, findings)
    if project is not None:
        _notes(home, project, manifest, findings)
    return report(findings)
