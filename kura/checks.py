"""Pure finding builders for multi-harness skill diagnostics."""

from dataclasses import dataclass

from . import catalog as cat
from . import harnesses

PROBLEM = "problem"
NOTE = "note"


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    subject: str
    detail: str
    kind: str = cat.SKILL

    @property
    def is_problem(self):
        return self.severity == PROBLEM


def catalog_health(catalog):
    findings = []
    skill_map = cat.skills(catalog)
    for art in cat.of_type(catalog, cat.SKILL):
        if art.catalog_error:
            findings.append(
                Finding("catalog-name", PROBLEM, f"skill '{art.name}'", art.catalog_error)
            )
        if not art.source.is_dir():
            findings.append(
                Finding("missing-source", PROBLEM, f"skill '{art.name}'", f"missing from {art.source}")
            )
        for dependency in art.dependencies:
            if dependency not in skill_map:
                findings.append(
                    Finding(
                        "missing-dependency",
                        PROBLEM,
                        f"skill '{art.name}'",
                        f"dependency '{dependency}' is not in the catalog",
                    )
                )
    return findings


def view_plan(plan, subject):
    findings = [
        Finding(
            "missing-intent",
            PROBLEM,
            f"skill '{name}'",
            "declared but missing from the catalog",
        )
        for name, _, _ in plan.missing
    ]
    for detail in plan.blocked:
        if "requires missing dependency" in detail:
            check = "missing-dependency"
        elif "invalid catalog metadata" in detail:
            check = "catalog-name"
        elif "global link" in detail and "not current" in detail:
            check = "native-view-drift"
        else:
            check = "unsafe-view"
        findings.append(Finding(check, PROBLEM, subject, detail))
    findings.extend(
        Finding(
            "native-view-drift",
            PROBLEM,
            f"{harnesses.get(action.harness).display_name} view",
            f"{action.skill}: {action.operation} required at {action.path}",
        )
        for action in plan.actions
        if action.operation in ("create", "relink", "delete")
    )
    return findings


def executable_notes(manifest):
    return [
        Finding(
            "executable-absent",
            NOTE,
            harnesses.get(harness_id).display_name,
            "selected but executable not found on PATH",
            None,
        )
        for harness_id in manifest.harnesses
        if not harnesses.executable_available(harness_id)
    ]


def instruction_notes(project):
    agents = project / "AGENTS.md"
    claude = project / "CLAUDE.md"
    if not agents.is_file() or not claude.is_file():
        return []
    if any(line.strip() == "@AGENTS.md" for line in claude.read_text().splitlines()):
        return []
    return [
        Finding(
            "split-instructions",
            NOTE,
            "instructions",
            "AGENTS.md and CLAUDE.md are split; Claude does not import AGENTS.md",
            None,
        )
    ]


def legacy_notes(manifest):
    return [
        Finding(
            "legacy-state",
            NOTE,
            "legacy state",
            f"{len(entries)} preserved {collection} entries are intentionally unmanaged",
            None,
        )
        for collection, entries in manifest.legacy.items()
        if entries
    ]
