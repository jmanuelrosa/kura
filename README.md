# kura

Kura manages one declared set of coding-agent skills and portable agent bundles across the native directories of the harnesses a user selects.
The first supported harnesses are Claude Code and Pi.
Skills and agents are linked directly from a user-controlled catalog, so source edits take effect without reinstalling.
Kura is a stdlib-only Python CLI.

## Installation

Each release ships a self-contained `kura` executable and `kura.sha256` checksum.

```sh
shasum -a 256 -c kura.sha256
chmod +x kura
mv kura ~/.local/bin/
```

Installed builds report their semver with `--version`:

```sh
kura --version
```

The release tag should match that string (without the leading `v`).
The verified asset checksum still pins the exact bytes for installers and automation.

Development commands use the shim in this checkout:

```sh
./bin/kura --version
./bin/kura -h
make test
make checksum
```

## Concepts

A project is the exact current directory.
Kura never searches Git or parent directories to find one.
`$HOME` is not a project because its native harness directories are global directories.

A selected harness receives the project's complete managed skill set and selected standalone or bundled agents.
A globally enabled harness receives the complete registry-global skill and agent set after `sync` or a machine configuration change.
When a project declares a globally tagged skill and its global link is missing, the selected harness receives a project-local link instead; `init` does not sync global skills.
After linking such a fallback, Kura warns which globally enabled harnesses lack the links and suggests `kura sync` as an optional way to install them globally.
Executable detection affects initialization suggestions and trust eligibility, but it never suppresses skill links.

| Harness | Project skill root | Global skill root | Project agent root | Global agent root |
|---|---|---|---|---|
| Claude Code | `.claude/skills` | `~/.claude/skills` | `.claude/agents` | `~/.claude/agents` |
| Pi | `.agents/skills` | `~/.agents/skills` | configured `pi.agents.project` | configured `pi.agents.global` |

Each skill or agent gets an independent native link.
Pi never points through Claude Code's directory.
Pi agent roots are only paths for a Markdown-compatible subagent extension to read.
Kura does not install that extension.

## Machine configuration

Kura stores machine configuration at `${XDG_CONFIG_HOME:-~/.config}/kura/config.json`.

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

`globalHarnesses` must be sorted, unique, non-empty, and contain only `claude` or `pi`.
`pi.agents.global` and `pi.agents.project` are optional until a global or project Pi agent view is needed.
The catalog path is not machine configuration and cannot be changed.

## Project manifest

An initialized project has `<project>/kura.json`.
The file is intended for version control.

```json
{
  "schemaVersion": 2,
  "harnesses": ["claude", "pi"],
  "skills": ["review", "tdd"],
  "agents": ["architect"],
  "bundles": ["backend"]
}
```

Only directly requested skills, standalone agents, and bundles are stored.
Dependencies are derived recursively from current optional registry metadata and bundle requirements.
Version 1 manifests are read as skill-only manifests and upgrade to version 2 when an agent or bundle selection is written after a successful project transaction.
Unsupported migrated plugins may be preserved under `legacy` and are not managed.
A malformed or newer manifest is never treated as an empty declaration.

## Catalog

Kura always reads the catalog at `~/.config/kura/catalog`.
There is no flag, environment variable, fallback, or saved setting that changes this path.
An older machine configuration's `catalog` field is ignored and removed when Kura rewrites the file.
Links to another catalog location are foreign and must be removed manually before Kura can recreate them from the fixed catalog.
The minimum catalog has a `skills/` directory, which may be empty; each discovered skill has `skills/<name>/SKILL.md`.
Standalone agents live in `agents/<name>.md`.
A bundle lives in `bundles/<name>/`, must contain `bundle.json`, and owns colocated `agents/*.md` and `skills/*/SKILL.md` sources.
`bundles/backend/bundle.json` may be `{}` for a self-contained bundle.
`skill-registry.json` and `agent-registry.json` are optional metadata.
A registry-free skill or agent is project-scoped by default and has no groups, dependencies, durable global policy, or upstream source.

When metadata exists, the registry name, directory name, and `SKILL.md` frontmatter name must agree.
A mismatch blocks installation.
Registry metadata separates `upstream` (GitHub repositories whose skills can be updated) from `local` (catalog-authored skills or agents without an upstream).
`local` entries may carry groups such as `global`.
The old `repos` and `local_skills` keys are rejected; rename them when migrating a catalog.
Registry metadata may add:

- `groups`, including the durable `global` policy
- `dependencies`
- `dependency_only`
- upstream repository fields used by `update` and `outdated`

[docs/schemas/skill-registry.schema.json](docs/schemas/skill-registry.schema.json) describes that file for editors.
A registry naming it is completed and validated while it is edited:

```json
{
  "$schema": "https://raw.githubusercontent.com/jmanuelrosa/kura/main/docs/schemas/skill-registry.schema.json",
  "version": 3,
  "local": [{ "name": "review", "groups": ["global"] }]
}
```

The schema is an authoring aid rather than a gate.
Kura's own refusals decide whether a registry is usable, and it ignores both `$schema` and `version`.

Kura records neither a catalog ID nor content digests.
Matching names in different developers' catalogs are intentionally treated as equivalent intent.

## Quick start

Bootstrap the machine, once, from anywhere:

```sh
kura config --harness claude --harness pi --yes
```

Then initialize a project:

```sh
kura init --harness claude --harness pi --yes
```

Then add a project skill:

```sh
kura add review --type skill
```

Kura writes one direct entry to `kura.json`, derives dependencies, preflights every selected harness, creates all links, and writes the manifest last.
A collision in any harness prevents every write.

## Commands

Artifact commands require an explicit type where the type is meaningful.
Supported project artifact types are `skill`, `agent`, and `bundle`.
`plugin` remains parseable only to refuse with migration guidance.
If agents were previously deployed by dotfiles, Ansible, or Claude Code plugins, remove or hand off those links manually before Kura can own the same native path.

### `init`

```text
kura init [--harness {claude,pi}] [--yes] [--dry-run] [--verbose]
```

`init` is the first project mutation and refuses in `$HOME`.
It requires machine configuration to already exist and directs the user to `kura config` when it does not.
Repeated `--harness` values select the complete project harness set.
Missing global links for declared skills are filled in the project rather than requiring `sync`; conflicting global paths still refuse.
`--yes` accepts the complete safe plan.
`--dry-run` writes nothing and does not require `--yes`.
`--verbose` includes every migration and native-view decision.

Kura creates `AGENTS.md` and a minimal `CLAUDE.md` importer when neither exists.
If only `AGENTS.md` exists, it creates `CLAUDE.md` containing `@AGENTS.md`.
If only `CLAUDE.md` exists, it preserves it because Pi reads it natively.
Existing split instruction files are preserved and reported by `doctor`.
Kura never runs `git add`.

Legacy `.claude/kura.json` direct skill rows migrate into root `kura.json`.
Dependency rows are dropped and re-derived.
Agent and plugin rows are preserved under `legacy`.
The old `.agents/skills -> ../.claude/skills` topology converts only when every exposed entry is proven catalog-backed and managed.

### `config`

```text
kura config [--harness {claude,pi}] [--yes] [--dry-run] [--verbose]
```

Bare `config` prints the machine configuration and fixed catalog path.
A mutation is the first machine setup when no machine configuration exists yet: `config` is the only command that creates one, and it may create an empty `~/.config/kura/catalog/skills` after confirmation when the fixed catalog is also absent.
Once machine configuration exists, repeated `--harness` values replace the complete global harness set and immediately converge global views.
`--yes`, `--dry-run`, and `--verbose` have the same planning meanings as on `init`.

### `list`

```text
kura list --type {skill,agent,bundle} [--group [TAG]] [--json]
```

Without a project manifest, `list` shows catalog and global state and sends an initialization notice to stderr.
With a manifest, a skill is linked only when every selected native view is correct.
Partial or conflicting state is `drift` and names each harness.
Bare `--group` groups the human report by metadata tag.
`--group TAG` filters by one opaque tag.
`--json` emits only JSON on stdout.

Existing skill row fields remain: `name`, `state`, `installed`, `global`, `groups`, `dependencies`, `reason`, `parent`, and `global_for`.
Agent and bundle listings use the same state vocabulary; bundle `views` keys identify both harness and member artifact.
`--group` is currently supported only with `--type skill`.
The additive skill and agent `views` object is keyed by harness ID.
The `state` enumeration is `available`, `linked`, `drift`, or `missing`.

### `scout`

```text
kura scout [--type skill] [--focus TAG] [--add]
```

`scout` requires root `kura.json` and recommends only skills with relevant registry group metadata.
`--focus` promotes one opaque tag.
`--add` sends strong matches through the same project transaction as `add`.

### `add`

```text
kura add [NAME...] --type {skill,agent,bundle} [--group TAG] [--global]
```

Project `add` requires root `kura.json`, adds direct intent, derives dependencies and bundle closures, and reconciles every selected view atomically.
`--type agent` selects standalone root agents only.
`--type bundle` selects a bundle and installs its owned agents and skills plus explicit requirements.
A fully healthy repeated add returns `ALREADY`.
A repeated add with drift repairs the selected views.
`--global` instead creates temporary scratch links in every globally enabled harness and does not write a global manifest.
The next `sync` restores registry-global policy.
`--group TAG` selects skill group members and cannot be combined with names; agent and bundle selections require explicit names.
Without `--global`, the project half is selected; with it, the global-policy half is selected.
Global `add` supports skills only; typed non-skill global adds are refused.

### `remove`

```text
kura remove [NAME...] --type {skill,agent,bundle} [--group TAG] [--global] [--no-cascade]
```

Project `remove` removes direct intent, re-derives dependencies and bundle closures, and reconciles every selected view atomically.
Dependencies still required by another direct skill, agent, or bundle remain.
`--global` removes matching temporary global links from every globally enabled harness until the next `sync`.
`--group TAG` follows the same skill-only partition rule as `add`.
`--no-cascade` remains accepted as a deprecated compatibility flag, but dependencies are declaratively derived from `kura.json`.
Global `remove` supports skills only; typed non-skill global removes are refused.

### `restore`

```text
kura restore [--type skill] [--dry-run]
```

`restore` currently supports `--type skill` only.
Bare `restore` requires root `kura.json` and creates missing direct and derived skill and agent links in every selected harness.
It deletes nothing.
A conflicting path refuses before any write.
A missing direct catalog skill remains declared and returns `DRIFT`.

### `converge`

```text
kura converge [--type skill] [--all] [--root PATH] [--dry-run] [--verbose | --quiet]
```

Single-project `converge` requires root `kura.json` and reconciles every selected view.
`--all` recursively scans cwd, or repeatable `--root` trees when supplied.
The scan has no depth cap, does not follow directory symlinks, prunes hidden, VCS, dependency, cache, build, and vendor trees, and recognizes only root `kura.json` files.
`--dry-run` uses the application plan without writing.
`--verbose` includes steady projects.
`--quiet` keeps stdout empty while warnings remain on stderr.

### `adopt`

```text
kura adopt [--type skill] [--dry-run]
```

`adopt` requires root `kura.json` and considers catalog-backed links from selected harnesses.
Same-name links resolving to different targets refuse.
Inferred direct roots augment existing direct intent, derived dependencies are omitted from the manifest, and missing selected views are filled.
Real directories and foreign links are never adopted.

### `sync`

```text
kura sync [--type skill] [--dry-run]
```

Bare `sync` projects the recursive registry-global skill closure and registry-global standalone agents into every globally enabled harness.
Bare `sync` also includes skill dependencies of selected global agents.
`sync --type skill` narrows to the skill registry policy without changing agent views.
It prunes only symlinks resolving under the fixed catalog and never touches real paths or foreign links.
If desired global state and managed state are both empty, the result succeeds.
If desired global state is empty while managed global links exist, it returns `DRIFT` and deletes nothing.
The closing report retains the exact `, 0 changes` marker used by provisioning automation.

### `update` and `outdated`

```text
kura update [NAME...] --type skill
kura outdated [NAME...] --type skill
```

These registry-wide commands act only on skills carrying upstream metadata.
Registry-free skills are omitted unless explicitly named for an explanatory report.
They do not require project initialization.
`update` requires valid saved machine configuration because it mutates the fixed catalog.
Read-only `outdated` may inspect the fixed catalog without saved machine configuration.

### `doctor`

```text
kura doctor [--type skill]
```

Bare `doctor` checks the catalog, machine configuration, project manifest when present, and implemented skill and agent views.
It can run without a project manifest.
It returns `DRIFT` for invalid configuration or manifests, missing desired content, unsafe collisions, missing required Pi agent paths, and incorrect native views.
Missing executables, trust state, split instructions, and preserved legacy rows are informational notes.

### `trust`

```text
kura trust [--on | --off] [--dry-run]
```

`trust` is cwd-only and requires root `kura.json`.
It intersects selected harnesses with executables available on `PATH`, reports unavailable selected harnesses, and refuses when none are available.
`--on` and `--off` update each available selected harness.
`--dry-run` shows native targets without acquiring Pi's write lock or changing either store.

Claude Code keeps its existing Git-root and main-worktree key behavior in `~/.claude.json`.
Pi uses canonical cwd and nearest-parent semantics in `~/.pi/agent/trust.json`.
Pi trust writes follow its private proper-lockfile-compatible lock directory protocol.
A multi-harness mutation preflights every store and restores exact original bytes and modes if a later write fails.
The backend dotfiles pilot bundle is documented as the intended first migration shape, but it is not deployed yet.

## Safety and transactions

A real path or foreign symlink is never replaced or deleted.
A managed skill link must occupy the expected native path and resolve under the fixed catalog's `skills` directory or an exact declared bundle-owned skill source.
A managed agent link must occupy the expected native path and resolve under a root agent source or an exact bundle-owned agent source.

Every multi-path mutation follows one process:

1. Read and validate machine configuration, project intent, catalog metadata, and native state.
2. Derive the complete desired skill, agent, and bundle closure.
3. Build and preflight a deterministic cross-harness plan.
4. Apply filesystem changes.
5. Write configuration or manifest state last.
6. Roll back changed files and links if a later step fails.

`--yes` accepts only a safe plan and never bypasses collision checks.

## Exit codes

| Code | Name | Meaning |
|---:|---|---|
| 0 | `OK` | The operation succeeded. |
| 1 | `USAGE` | Arguments, configuration use, or an unsafe operation were invalid. |
| 2 | `NOT_FOUND` | A named skill or source was not found. |
| 3 | `DEPENDENCY_ONLY` | A dependency-only skill was named directly. |
| 4 | `WRONG_SCOPE` | Registry-global policy requires explicit global scope. |
| 5 | `ALREADY` | The requested state was already fully current. |
| 6 | `NO_PROJECT` | The command needs initialization in the exact cwd. |
| 7 | `NOT_INSTALLED` | The requested direct or global state was absent. |
| 8 | `FETCH_FAILED` | An upstream fetch failed. |
| 9 | `DRIFT` | Desired state is incomplete, invalid, conflicting, or unsafe to reconcile. |

## Development

```sh
make test
make build
make checksum
```

Runtime code uses only the Python standard library.
Tests may use pytest and PyYAML as an oracle for the intentionally narrow frontmatter scanner.
The release zipapp is deterministic: source members are sorted and receive fixed archive timestamps.
