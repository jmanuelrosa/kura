# Portable Agent Bundles - Design Doc

**Status:** Locked for implementation
**Author:** Jose Manuel Rosa
**Date:** 2026-09-24
**Scope:** `kura/`, `tests/`, `README.md`, `ARCHITECTURE.md`, `CONTEXT.md`, `docs/specs/`, and the dotfiles AI role and catalog

## Summary

Let a user select a named bundle such as `backend` and make its agent and required skills available through each selected harness without making Claude Code's plugin format the source of truth.
Each bundle keeps its own agent and skill sources together in the fixed Kura catalog; standalone or shared artifacts remain at the catalog root.
Claude Code and Pi receive independent views of the same content; Pi agent views require configured paths read by a Markdown-compatible subagent extension.

## Motivation

The dotfiles currently link standalone agent files into `~/.claude/agents` with Ansible, while Kura manages global skills in both Claude Code and Pi ([dotfiles role](https://github.com/jmanuelrosa/dotfiles/blob/main/roles/ai/tasks/main.yml#L266-L278); `ARCHITECTURE.md`, Harness profiles).
The existing `backend` Claude plugin contains an agent and a skill, but its Claude plugin identity is not a requirement: users want to select `backend` and preferably invoke its agent as `backend` ([backend plugin](https://github.com/jmanuelrosa/dotfiles/tree/main/roles/ai/files/claude/plugins/backend)).

The two harnesses do not have equivalent packaging systems.
Claude Code discovers `.claude/agents/*.md` and `~/.claude/agents/*.md` ([subagent documentation](https://code.claude.com/docs/en/sub-agents)).
Pi discovers skills in `.agents/skills` and `~/.agents/skills` ([Pi skills documentation](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md)), but agent definitions are supplied by an extension, not discovered by core Pi ([Pi subagent example](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/examples/extensions/subagent)).
A Pi extension is the runtime for potentially many agents, not an equivalent package to one Claude plugin.

This is not just a filesystem move.
The `product-team` plugin contains several agents and skills, references namespaced `/product-team:...` commands and agents, and uses `.claude/skills/product-team/skills/...` paths in its instructions ([example skill](https://github.com/jmanuelrosa/dotfiles/blob/main/roles/ai/files/claude/plugins/product-team/skills/product-lead/SKILL.md)).
Installing its contents independently without rewriting and testing those references would produce a broken bundle.

## Non-goals

- Generating a Pi extension per bundle, installing or choosing a third-party Pi extension, or guaranteeing compatibility with every subagent extension.
- Translating arbitrary Claude frontmatter, tool policies, models, or instructions into another harness's semantics.
- Preserving Claude plugin names, `name@skills-dir`, or namespaced `plugin:agent` and `/plugin:skill` invocations.
- Maintaining a machine-wide index of projects or automatically migrating every project when a dotfiles catalog changes.
- Changing the fixed catalog path, runtime dependency policy, symlink ownership rules, or the `, 0 changes` summary contract.
- Translating agents into arbitrary Pi extension-specific formats; this phase supports Markdown definitions only.

## Background

### Existing artifact and manifest boundaries

`kura/catalog.py:12-35` already distinguishes skill, agent, and plugin stores, and `kura/catalog.py:180-243` contains unused registry and plugin readers.
`build_catalog()` currently returns skills only (`kura/catalog.py:246-283`).
The project manifest records selected harnesses and direct skills, with agents and plugins preserved only as inert legacy state (`kura/state.py:24-43`, `kura/state.py:80-110`).
The accepted skills-only design deliberately makes native views derived state (`ARCHITECTURE.md`, Domain boundaries; `docs/specs/multi-harness-skills.md`, Non-goals).

### Integration surfaces

`kura/harnesses.py:8-35` models one skill root per harness.
`kura/views.py:68-106` classifies symlinks by catalog store, and `kura/views.py:212-310` computes each selected harness's project skill view before writing.
The transaction system preflights and rolls back a multi-harness mutation as one logical operation (`ARCHITECTURE.md`, Transactions).
For the Pi example extension, definitions live at `~/.pi/agent/agents/*.md` or `.pi/agents/*.md`; project definitions require the extension's project scope to be enabled ([example README](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/subagent/README.md)).
Its discovery code reads `name`, `description`, optional `tools` and `model`, and the Markdown body; it does not implement all Claude agent frontmatter ([example `agents.ts`](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/subagent/agents.ts)).

## Design rules

- **Bundle** means a user-selectable, self-contained collection of agents and skills in the catalog, not an executable plugin or extension. Kura discovers its contents and projects them into harness views through a narrow selection interface; no new packaging runtime is needed.
- Bundle-owned artifacts are selected through their bundle. Reusable standalone agents and skills remain in the root stores; a bundle may explicitly depend on those without copying them. Different bundles cannot claim the same native artifact name with different sources.
- **Agent** means a reusable delegated persona; **harness** remains the application that runs it. These terms must not be interchanged (`CONTEXT.md`, Artifacts and sources).
- Catalog content remains at `~/.config/kura/catalog`; project intent remains at `<project>/kura.json`; global policy remains catalog metadata plus machine-selected harnesses.
- Every selected harness gets a native view of every selected artifact or the whole mutation refuses. Missing Pi agent locations never silently drop the Pi agent.
- Installed files link directly to catalog sources, never through another harness. Collisions, real paths, foreign symlinks, and untrusted native roots block the whole mutation.
- Agent names in native views come from canonical filenames and frontmatter, not from automatic runtime alias generation. A bundle called `backend` exposes an agent called `backend` when its source is `bundles/backend/agents/backend.md` with `name: backend`.
- Pi agent views support Markdown definitions only. Configuring paths does not prove that the extension is installed, reads those paths, enables project agents, or interprets every agent instruction correctly.
- Preserve the existing non-empty-global guard per managed artifact kind. Do not use a missing registry or empty bundle list as permission to prune all agent or skill links.

## Design

### 1. Catalog artifacts and bundles

Keep root `agents/<name>.md` and `skills/<name>/SKILL.md` for independent or shared artifacts; discover optional root `agent-registry.json` metadata for their groups, global policy, and skill dependencies (`kura/catalog.py:180-283`).
A bundle owns its own content together:

```text
catalog/
  agents/architect.md
  skills/idea-refine/SKILL.md
  bundles/backend/
    bundle.json
    agents/backend.md
    skills/backend-failure-modes/SKILL.md
```

The bundle directory name is its selection name, and `bundle.json` is a required marker: `{}` is valid for a self-contained bundle such as `backend`.
Discover bundled `agents/*.md` and `skills/*/SKILL.md` from the filesystem, requiring at least one of each and validating safe names, frontmatter names, and containment; `bundle.json` contains only external dependencies or metadata, not a duplicate inventory of the files beside it.
For example, `bundles/product-team/bundle.json` can declare `{"requires": {"skills": ["idea-refine"], "agents": ["ux-shaper"]}}` when those are shared root artifacts.
The bundle closure includes its owned artifacts, explicitly required root artifacts, and recursive skill dependencies; missing requirements block the entire plan.
Bundled artifacts are not independently selected by `kura add --type agent|skill`; move a reusable artifact to a root store and reference it from bundles instead.
Reject duplicate native names backed by different sources, including duplicates across bundles or root stores.
Keeping sibling skill directories together preserves skill-local scripts and relative sibling references, though old plugin-qualified names and `.claude/skills/product-team/skills/...` paths still require rewriting.
Bundles are project selections in this phase; global agent and skill policy continues to use metadata for standalone artifacts.

### 2. Declarative intent and command surface

Add `agents` and `bundles` as sorted direct-selection arrays in a versioned project manifest, alongside existing `skills` and `harnesses`.
Read existing version-1 manifests and preserve `legacy.agents` and `legacy.plugins` without activating them; write the new version only after a successful transaction.
Adding `backend` as a bundle does not automatically reactivate an old legacy plugin row or assume its contents are equivalent.
When a user deliberately replaces a legacy plugin selection with the same-named bundle, remove that inert row only after the new view has been successfully preflighted and applied; unrelated legacy rows remain inert.

`kura add --type bundle backend` selects the bundle, including its owned agent and skill; `kura add --type agent architect` selects a standalone agent.
Bundled agent `backend` is invocable by name in a compatible harness, but cannot be selected by itself as separate Kura intent.
`remove`, `list`, `doctor`, `restore`, `converge`, `adopt`, `init`, and `sync` must use the same typed desired-state rules, with explicit refusal or documented limits where an operation does not apply.
Keep `--type plugin` parseable during transition, but do not equate it with a bundle: direct plugin operations refuse with migration guidance until compatibility is deliberately removed.
`update` and `outdated` remain skill-upstream operations unless upstream metadata for agents is designed separately.
Preserve explicit types even when a bundle and an agent share the name `backend` (`kura/cli.py:298-306`; `tests/test_type_contract.py:1-6`).

### 3. Harness agent views

Extend harness profiles with agent capability/path selection rather than reusing their skill roots (`kura/harnesses.py:8-35`).
Claude Code gets direct links from a standalone or bundle-owned catalog agent file to `.claude/agents/<name>.md` or `~/.claude/agents/<name>.md`.
Pi gets direct links into configured agent directories read by a compatible Markdown subagent extension, not into `.agents/skills` and not through `.claude/agents`.
Skills continue to use the existing independent `.claude/skills` and `.agents/skills` views.
A bundle expands to those artifact views; Kura does not install the bundle record into either harness.

### 4. Configurable Pi agent locations

An *adapter* here means Kura's code for writing one harness's native view; it is not a package the user installs. No adapter selector is needed in the config while Kura supports only Markdown agent definitions.
Put Pi-specific machine settings under `pi`, alongside the existing `globalHarnesses` field:

```json
{
  "schemaVersion": 1,
  "globalHarnesses": ["claude", "pi"],
  "pi": {
    "agents": {
      "global": "~/.pi/agent/agents",
      "project": ".pi/agents"
    }
  }
}
```

This top-level `pi` object is shorter than nesting it inside a redundant `config` object and leaves room for future Pi settings without mixing them into project intent.
The example paths match Pi's documented example extension, not core Pi; another extension that consumes the same Markdown definitions may use different paths.
Machine config supplies destinations for portable project intent; the project manifest contains no extension-specific paths.
Each path is optional when its scope is unused, but a selected project or global agent view with no corresponding configured path refuses, while skill-only operations keep working.
Validate paths before any mutation: expand `~` against the Kura home, require a global root under that home and a project-relative root with no escape, reject symlinked or non-directory root ancestors, and refuse overlaps with existing managed roots.
Do not infer extension installation from the destination directory; `doctor` should explain that the user must enable a Markdown-compatible extension and its project-agent discovery when project agents are selected.
An extension with a different definition format needs a separately designed integration, not just a different path.
Config rewrites must retain existing unknown fields while validating the now-known `pi.agents` fields (`kura/config.py:54-67`).

### 5. Reconciliation and safety

Generalize the current link classifier and project/global planners by artifact type, harness path, and catalog source, retaining symlink ownership checks (`kura/views.py:68-106`, `kura/views.py:212-310`).
Recognize only root agent/skill stores or exact declared bundle-owned agent/skill paths as managed; a link into some other path in `catalog/bundles/` must not become deletable merely because it lies under the catalog.
Project deletion remains bounded by names derived from the old or new project declaration, including its bundles and dependencies.
Global `sync` may prune only proven managed links under that artifact type's catalog store and configured view, and must refuse an ambiguous empty desired set when managed links remain.
Preflight every harness's agent and skill view before applying any action; reuse transaction rollback and preserve closing-summary semantics.
A changed Pi destination root must be treated as a migration with an explicit old-root ownership check, not as permission to delete or sweep an arbitrary directory.

### 6. Dotfiles content migration

First, move the `backend` agent and its skill together into `catalog/bundles/backend/`, add `bundle.json` containing `{}`, and change the agent's declared name and filename to `backend` for invocation by that name.
Rewrite its `backend:backend-failure-modes` reference to the native skill name and verify invocation in Claude Code and in a Pi extension that reads the configured Markdown agent view.
For standalone global agents, remove the old Ansible link task from provisioning before the operator unlinks its known Ansible-owned links and runs a preflighted Kura `sync`; Kura itself must refuse those old links rather than claim them. Confirm provisioning cannot recreate the old links before handoff.

Ship the `backend` bundle and its verified Claude/Pi views first. Migrate multi-agent bundles separately, one at a time, after that release.
For `product-team`, audit and rewrite namespaced agent/skill calls, hardcoded `.claude/skills/product-team/skills/...` paths, shared relative resources, external global-agent requirements, and Claude-specific tool/frontmatter assumptions before declaring Pi compatibility.
Keep existing Claude plugin deployments in place until equivalent native views pass an end-to-end trial; never count a syntactically present link as proof that a workflow works.

## Runtime behaviour matrix

| Project state | Claude Code view | Pi view | Kura result |
|---|---|---|---|
| `backend` bundle selected; both harnesses selected; compatible extension reads configured Pi project path | `backend.md` plus skill | Configured Markdown agent path plus skill | One preflighted transaction; runtime invocation requires the extension to be enabled |
| `backend` bundle selected; both harnesses selected; no Pi project agent path | None changed | None changed | Refuse, naming missing project path |
| `backend` bundle selected; Claude only | Agent and skill | Unchanged | Succeed without Pi agent settings |
| Skill-only project; no Pi agent paths | Skill view | Skill view | Existing behavior unchanged |
| Bundle with missing skill or agent, or a foreign native-path occupant | None changed | None changed | Refuse, naming missing source or conflict |
| Old manifest with `legacy.plugins.backend`, no active bundle | Existing unrelated views unchanged | Existing unrelated views unchanged | Preserve inert legacy intent; no automatic install |

## Alternatives considered

- **Claude plugin per bundle and Pi extension per bundle:** Rejected because an extension executes trusted code and provides a delegation runtime, while most bundles contain only Markdown agents and skills. It would require generating and trusting code per selection.
- **Install every bundle as a Claude plugin and expand only for Pi:** Rejected for this phase because the Claude namespacing and plugin-specific paths would keep the catalog's primary format Claude-shaped. Revisit if a bundle needs hooks or another genuinely plugin-only feature.
- **Flatten every bundled agent and skill into top-level catalog stores:** Rejected because it splits one owned unit across unrelated locations and makes migration of sibling resources harder. Root stores remain available for independently selected or shared artifacts.
- **Require an adapter name in machine config:** Rejected for the first version because only Markdown definitions are supported. Configured paths choose the compatible extension's discovery locations; a different definition format will need its own reviewed integration, not a magic string that claims compatibility.
- **Treat bundle name as an automatic runtime alias:** Rejected because both harnesses load agent names from definitions, and a multi-agent bundle such as `product-team` has no unambiguous agent to alias.
- **Keep Ansible as the agent manager:** Rejected because it cannot reconcile project intent and cross-harness views with Kura's preflight and ownership rules.

## Testing Decisions

Test the externally visible selection and reconciliation boundary: co-located bundle discovery and root references, typed bundle closure, old/new manifest parsing, agent view placement per harness, duplicate-name and source collisions, foreign/real-path safety including symlinks into unrelated bundle paths, Pi path validation and missing-scope refusal, global empty-desired protection, rollback, and legacy-intent preservation.
Use literal-data tests for closure (`tests/test_type_contract.py`), `tmp_path` symlink tests for ownership (`tests/test_scope.py`), and a small number of CLI runs for command documentation and summaries (`tests/test_help.py`, `tests/test_packaging.py`).
Extend `tests/fixtures/README.md` when adding bundle or active-agent fixtures.
Separately trial `backend` and later `product-team` in real Claude Code and Pi sessions: a parser or symlink test cannot validate prompt instructions or extension discovery.

## Open questions

- No architectural decision remains open. Before accepting the first real Pi installation, identify the installed Markdown subagent extension, check that it reads the configured paths and enables project agents, and verify `backend` invocation end to end. The example extension is a reference contract, not proof of another extension's behavior.

## Appendix - affected files

- `kura/catalog.py`, `kura/config.py`, `kura/state.py`, `kura/harnesses.py`, `kura/views.py`, `kura/scope.py`, `kura/transaction.py`, `kura/cli.py`
- `kura/commands/add.py`, `kura/commands/remove.py`, `kura/commands/listing.py`, `kura/commands/provision.py`, `kura/commands/converge.py`, `kura/commands/restore.py`, `kura/commands/doctor.py`, `kura/commands/adopt.py`, `kura/commands/init.py`, `kura/commands/common.py`
- `tests/test_type_contract.py`, `tests/test_scope.py`, `tests/test_help.py`, `tests/test_packaging.py`, and relevant command, state, catalog, view, and configuration tests
- `tests/fixtures/catalog/`, `tests/fixtures/README.md`
- `README.md`, `ARCHITECTURE.md`, `CONTEXT.md`, `docs/specs/multi-harness-skills.md`, `docs/specs/multi-harness-skills-ux.md`; an ADR in `docs/adr/` recording the bundle-vs-plugin decision when implemented
- Dotfiles `roles/ai/files/kura/catalog/`, `roles/ai/files/claude/agents/`, `roles/ai/files/claude/plugins/`, `roles/ai/tasks/main.yml`, and any affected project manifests
