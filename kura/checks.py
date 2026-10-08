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
    for art in cat.of_type(catalog, cat.AGENT):
        if art.catalog_error:
            findings.append(
                Finding("catalog-name", PROBLEM, f"agent '{art.name}'", art.catalog_error, cat.AGENT)
            )
        if art.source is not None and not art.source.is_file():
            findings.append(
                Finding("missing-source", PROBLEM, f"agent '{art.name}'", f"missing from {art.source}", cat.AGENT)
            )
        for dependency in art.dependencies:
            if dependency not in skill_map:
                findings.append(
                    Finding(
                        "missing-dependency",
                        PROBLEM,
                        f"agent '{art.name}'",
                        f"dependency '{dependency}' is not in the catalog",
                        cat.AGENT,
                    )
                )
    for bundle in cat.of_type(catalog, cat.BUNDLE):
        if bundle.catalog_error:
            findings.append(
                Finding("catalog-name", PROBLEM, f"bundle '{bundle.name}'", bundle.catalog_error, cat.BUNDLE)
            )
        if not bundle.source.is_dir():
            findings.append(
                Finding("missing-source", PROBLEM, f"bundle '{bundle.name}'", f"missing from {bundle.source}", cat.BUNDLE)
            )
    return findings


def unregistered_notes(sources):
    return [
        Finding(
            "unregistered",
            NOTE,
            f"{kind} '{path.stem if kind == cat.AGENT else path.name}'",
            f"{path} has no entry in {cat.REGISTRY_FILE[kind]}, so kura ignores it",
            kind,
        )
        for kind, path in sources
    ]


def _missing_finding(row):
    return Finding(
        "missing-intent",
        PROBLEM,
        f"{row.kind} '{row.name}'",
        "declared but missing from the catalog",
        row.kind,
    )


def view_plan(plan, subject):
    findings = [_missing_finding(row) for row in plan.missing]
    for row in plan.blocked:
        detail = row.detail
        if "requires missing dependency" in detail:
            check = "missing-dependency"
        elif "invalid catalog metadata" in detail:
            check = "catalog-name"
        elif "global link" in detail and "not current" in detail:
            check = "native-view-drift"
        else:
            check = "unsafe-view"
        findings.append(Finding(check, PROBLEM, subject, detail, row.kind))
    findings.extend(
        Finding(
            "native-view-drift",
            PROBLEM,
            f"{harnesses.get(action.harness).display_name} view",
            f"{action.skill}: {action.operation} required at {action.path}",
            action.kind,
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


def pi_agent_notes(manifest):
    if "pi" not in manifest.harnesses or not (manifest.agents or manifest.bundles):
        return []
    return [
        Finding(
            "pi-agent-discovery",
            NOTE,
            "Pi agent discovery",
            "project agents are selected; verify a Markdown-compatible Pi extension reads the configured project path",
            cat.AGENT,
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
