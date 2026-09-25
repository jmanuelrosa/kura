# kura

Kura is a stdlib-only Python package that projects declared skills, standalone agents, and portable agent bundles into the native directories of selected coding-agent harnesses.
The first profiles are Claude Code and Pi.

The package lives under [kura/](kura), and [bin/kura](bin/kura) is only a development shim.
A release is a deterministic executable zipapp built by [build.py](build.py).

## Domain boundaries

A catalog owns skill content, standalone agent content, and portable bundle content.
Optional registry metadata adds groups, dependencies, global policy, and upstream source information.
A project manifest owns portable project intent.
Machine configuration owns globally enabled harnesses and Pi agent view paths.
The catalog has one fixed machine location.
Native views are derived filesystem state and never become another source of truth.

Kura manages skills, standalone Markdown agents, and portable bundles.
Legacy plugin intent may be preserved in a project manifest, but it is inert and plugin commands refuse with migration guidance.
Kura does not launch harnesses, install Pi extensions, translate non-Markdown agent formats, select a default harness, or maintain an index of initialized projects.

The domain vocabulary is in [CONTEXT.md](CONTEXT.md).
The original skill behavior is in [docs/specs/multi-harness-skills.md](docs/specs/multi-harness-skills.md) and [docs/specs/multi-harness-skills-ux.md](docs/specs/multi-harness-skills-ux.md); the agent and bundle extension is in [docs/design/portable-agent-bundles.md](docs/design/portable-agent-bundles.md).

## Roots and configuration

There are three explicit locations.

1. `~/.config/kura/catalog` contains source skills.
2. `${XDG_CONFIG_HOME:-~/.config}/kura/config.json` contains machine configuration.
3. Cwd is the exact project for every project command.

The catalog location cannot be configured or overridden, and none of these locations is discovered from another.
There is no Git project discovery and no ancestor search.
A subdirectory is its own project when it has its own root `kura.json`.
`$HOME` is excluded because its harness directories are global directories.

[kura/config.py](kura/config.py) reads `${XDG_CONFIG_HOME:-~/.config}/kura/config.json`.
The versioned schema stores a sorted non-empty set of globally enabled harness IDs.
Unknown fields survive a valid rewrite so a newer producer does not lose unrelated state, while the retired `catalog` field is discarded on rewrite.
Unknown versions and harness IDs refuse.

The same module derives the catalog only from `$HOME` as `~/.config/kura/catalog`.
It does not consult `XDG_CONFIG_HOME`, machine configuration, or environment overrides for that path.
Read-oriented commands may use the fixed catalog without machine configuration, while mutations that require machine policy still refuse when configuration is absent, except `config`, whose own mutation is what creates it.

[kura/paths.py](kura/paths.py) remains the `HOME` seam used by tests and delegates fixed catalog validation to the configuration module.

## Harness profiles

[kura/harnesses.py](kura/harnesses.py) is the built-in declarative profile registry.
Each profile supplies a stable ID, display name, executable, native project skill root, native global skill root, native agent roots, and project footprints used by initialization suggestions.

| Harness | Project skill root | Global skill root | Project agent root | Global agent root |
|---|---|---|---|---|
| Claude Code | `.claude/skills` | `~/.claude/skills` | `.claude/agents` | `~/.claude/agents` |
| Pi | `.agents/skills` | `~/.agents/skills` | `pi.agents.project` | `pi.agents.global` |

Executable detection does not decide whether skills are linked.
A project may prepare a native view before a harness is installed.
Executables matter only for initialization evidence and trust eligibility.

A selected skill or agent gets one direct catalog link in each selected harness.
No native view points through another harness.
This preserves the old skill contract while adding independent agent views.
It also preserves the historical replacement of the old `.agents/skills -> ../.claude/skills` topology, which made Claude Code's directory an accidental canonical store.
Pi agent views are only configured Markdown directories for a compatible subagent extension; Kura does not install or prove that extension.

Version-sensitive trust behavior does not belong in profiles.
Claude Code trust remains in [kura/workspace.py](kura/workspace.py), and Pi trust remains in [kura/pi_trust.py](kura/pi_trust.py).

## Filesystem-first catalog

[kura/catalog.py](kura/catalog.py) discovers every `skills/<name>/SKILL.md`, `agents/<name>.md`, and `bundles/<name>/bundle.json` before applying metadata.
This makes the minimum skill catalog useful without a registry, and makes a self-contained bundle such as `bundles/backend/bundle.json` with `{}` valid when it owns at least one agent and one skill.
A registry-free skill or agent is project-scoped, has no groups or dependencies, has no durable global policy, and has no upstream update source.

When `skill-registry.json` or `agent-registry.json` exists, its entries are merged onto discovered root artifacts.
Registered but absent sources remain representable so `list` and `doctor` can report missing content.
For metadata-backed skills and agents, the registry name, source name, and directory or file name must agree.
A mismatch is attached to the catalog artifact and blocks planning that artifact.
Root registry `local` groups are valid, including `global`.
Bundle-owned agents and skills are selected through their bundle, not by independent root selection.

Dependency closure is recursive, deterministic, and cycle-safe.
The project closure begins with the direct skill, agent, and bundle names in `kura.json`.
The global closure begins with metadata-backed skills and standalone agents carrying the `global` group, and includes skill dependencies.
Dependencies are never written to the project manifest.

`update` and `outdated` act only on skills whose metadata supplies an upstream source.
A filesystem-only skill is therefore visible to `list` and installable without becoming an update target.

## Declarative project state

[kura/state.py](kura/state.py) owns `<project>/kura.json`.
The versioned schema stores sorted selected harness IDs, sorted direct skill names, sorted standalone agent names, sorted bundle names, and optional inert legacy rows.
Version 1 manifests remain skill-only and are upgraded to version 2 when new agent or bundle intent is written in a successful project mutation.
Strict parsing rejects duplicates, malformed values, unknown harnesses, and unknown schema versions.

The manifest is intended for version control and may be edited by a user.
A malformed manifest never reads as an empty declaration.
Mutations refuse, while `doctor` reports the invalid bytes as drift.

The old `.claude/kura.json` recorded historical `direct` and `dep-of:` provenance for skills, agents, and plugins.
`init` migrates direct skill rows, drops dependency rows for re-derivation, preserves agent and plugin rows under `legacy`, writes the root manifest, and deletes the old file only after the complete transaction succeeds.
If both manifests exist, incompatible direct or legacy meaning refuses with a semantic difference.

A direct skill that becomes registry-global remains direct intent in the manifest.
A current global link makes its project link redundant for that harness, while a missing global link leaves the project link in place.
If global policy disappears later, project convergence recreates its project links from unchanged intent.

## Desired state

Let `direct` be the project manifest's skill names, agent names, and bundle names.
Let `closure(direct)` be the recursive dependency and bundle closure from current metadata and bundle requirements.
Let `global` be the recursive closure of metadata-backed global skill roots and standalone global agent roots.

The project intent is `closure(direct)`.
For each selected harness, the project native skill view is the skill closure minus skills with a current global link in that enabled harness, and the project native agent view is the selected standalone and bundle-owned agent closure.
The durable global native view is `global` in every globally enabled harness.
Temporary global additions may exist until the next `sync`.

A missing global link does not force a machine-wide sync: the project gets its own link for the skill until that harness has a current global link.
A harness excluded from global policy also uses its project view.
A stale, foreign, or real path at an enabled harness's expected global destination still blocks the project transaction rather than hiding a conflict behind a local link.

A missing direct skill already present in a manifest preserves intent and yields `DRIFT`.
A missing dependency blocks the transaction because there is no complete desired state to apply.

## Link ownership and native views

[kura/views.py](kura/views.py) classifies and plans native links.
A desired destination can be missing, current, stale but catalog-managed, foreign, or a real path.

A skill link is manageable only when it occupies the expected harness path and resolves under the fixed catalog's `skills` directory or an exact declared bundle-owned skill source.
An agent link is manageable only when it occupies the expected harness path and resolves under the fixed catalog's root `agents` directory or an exact declared bundle-owned agent path.
A project deletion is further limited to names derived from the old or current project declaration for that transaction.
A real path is always user-owned.
A symlink outside the recognized catalog is always foreign.
Neither is replaced or deleted.

Links to any other catalog path are foreign.
Their repair requires manual removal before `restore` can create links to the fixed catalog.

`restore` is additive.
It creates missing links, refuses stale or conflicting destinations, and deletes nothing.
`converge` and direct-intent mutations may relink stale managed destinations and remove managed links that the transaction's prior declaration no longer needs.

## Transactions

[kura/transaction.py](kura/transaction.py) applies filesystem plans.
Every mutating command first validates configuration, manifests, catalog content, global requirements, source paths, native roots, and every destination across every harness.
`--yes` never bypasses those checks.

A transaction snapshots each changed regular file's exact bytes and mode and each changed symlink's original target.
Actions are deterministic: directory creation, managed deletion, relinking, creation, then state writes.
Configuration and project manifest writes occur last.
If a later action fails, snapshots are restored in reverse order and newly created empty directories are pruned.
A rollback failure is reported separately and returns `DRIFT`.

This model is why a conflict in Pi prevents an otherwise valid Claude Code link from being created.
A multi-harness mutation is one logical operation rather than a loop of independent installations.
Reports count selected skills separately from physical links.

## Project lifecycle

`init` is the first project mutation and requires machine configuration to already exist.
It gathers harness selection, prepares instruction files, migrates legacy state, preflights native views, and asks once before applying an interactive plan.
A noninteractive run supplies repeated `--harness` flags plus `--yes`.
`init` no longer bootstraps machine configuration itself; a missing one refuses and names `kura config`.

The shared instruction surface is `AGENTS.md`.
Claude Code receives a minimal `CLAUDE.md` containing `@AGENTS.md` when a bridge is needed.
A lone existing `CLAUDE.md` is preserved because Pi reads it natively.
Existing split files are never rewritten automatically.

Re-running `init` replaces selected harnesses and retains direct skills.
Removing a harness deletes only links proven managed from the previous project declaration.

`add` and `remove` modify direct skill, standalone agent, or bundle names, derive the new closure, reconcile all selected views, and write the manifest last.
They require explicit `--type skill`, `--type agent`, or `--type bundle` for project artifacts.
`--type plugin` parses but refuses because Claude Code plugins are legacy state, not portable bundles.
Typed non-skill global operations and typed non-skill unsupported commands refuse rather than pretending to manage partial state.
`remove --no-cascade` remains parseable as a deprecated compatibility option, but dependency presence is defined by declarative closure rather than historical provenance.

`adopt` considers catalog-backed links in selected harnesses.
Same-name links with different targets refuse.
It chooses roots not reached by another installed skill, breaks uncovered cycles deterministically, augments existing direct intent, omits derived names, and fills missing selected views.

`converge --all` uses [kura/projects.py](kura/projects.py).
It recursively scans cwd unless explicit roots replace it, includes each root, has no depth cap, does not follow directory symlinks, and prunes hidden, VCS, dependency, cache, build, and vendor trees.
Only root `kura.json` marks a project.
Claude Code's private project registry and a Kura project index are not consulted.

## Global lifecycle

Bare `sync` derives global skill policy and standalone global agent policy once and reconciles every globally enabled harness.
It includes skill dependencies required by global agents.
`sync --type skill` narrows to skills.
It prunes only symlinks resolving under the current catalog and exact managed agent sources.
Real paths, foreign links, and legacy plugin state are outside its ownership.

The empty-desired guard remains load-bearing.
When desired global state is empty and managed state is also empty, sync succeeds.
When desired global state is empty but managed links remain, sync returns `DRIFT` and deletes nothing.
This prevents a malformed or accidentally emptied registry from silently clearing every global skill.
The closing summary retains `, 0 changes` because machine provisioning uses that exact marker.

Global `add` and `remove` are explicit scratch operations across every globally enabled harness.
They create no global manifest.
The next `sync` restores registry policy.

`config` mutations reconcile global views in the same transaction as the configuration write.
A removed global harness is an explicit request, so its managed links can be removed without applying the ambiguous empty-policy pruning rule to harnesses that remain selected.

`config` is also the one command that bootstraps machine configuration: a mutation against an absent configuration creates it instead of refusing, and may create the fixed catalog's empty `skills/` directory after confirmation the same way `init` used to on a first run.

## Listing and diagnostics

[kura/commands/listing.py](kura/commands/listing.py) keeps the established JSON fields and adds `views`, keyed by harness ID.
A configured skill is `linked` only when every required native view is current.
Any missing, stale, foreign, or real-path view makes the row `drift` and names each harness.
JSON stdout contains only JSON, while initialization notices remain on stderr.

Bare `doctor` orders actionable drift before informational notes across implemented skill and agent behavior.
Invalid machine configuration, invalid manifests, missing content, dependency failures, missing required Pi agent paths, collisions, and incorrect native views return `DRIFT`.
Missing executables, trust not granted, split instructions, the need to enable a Markdown-compatible Pi subagent extension, and preserved legacy state are notes.
It can run without a project manifest and still checks the fixed catalog and global state.

## Trust adapters

`trust` is cwd-only and requires the root project manifest.
Selected harnesses without an executable on `PATH` are reported and skipped.
A mutation refuses if none of the selected harness executables is available.

Claude Code's key is its Git root, or the main checkout for a linked worktree, and trust may be inherited from a cwd ancestor.
Those private semantics remain isolated in [kura/workspace.py](kura/workspace.py).
Kura never creates a missing `~/.claude.json`.

Pi's key is canonical cwd, and the nearest parent boolean wins.
Its trust file is `~/.pi/agent/trust.json` with sorted JSON and a trailing newline.
Writes acquire the same `<trust.json>.lock` directory shape used by Pi's proper-lockfile integration.
The lock is acquired before either harness store is changed, so a lock conflict cannot leave Claude Code changed alone.

Cross-harness trust writes use the same byte-and-mode rollback guarantees as artifact transactions.
Pi's trust format and lock are private, version-sensitive adapter contracts rather than public Pi APIs.

## Tests and release

Tests live in [tests/](tests) outside the package and use three altitudes: pure value tests, `tmp_path` filesystem tests, and limited subprocess coverage through the shim.
`HOME` is the catalog and native-view seam used by tests.
No test imports `conftest`, and `tests/` has no `__init__.py`, avoiding Python module-name collisions across suites.

Runtime imports remain standard-library only.
PyYAML is a test oracle for the intentionally narrow frontmatter scanner and never a runtime dependency.

[build.py](build.py) writes sorted package members with fixed zip timestamps.
Two builds of one source tree therefore produce the same checksum.
The shim and package stay separate because the package must remain directly importable for fast tests.
