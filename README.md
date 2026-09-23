# kura

Kura manages one declared set of coding-agent skills across the native skill directories of the harnesses a user selects.
The first supported harnesses are Claude Code and Pi.
Skills are linked directly from a user-controlled catalog, so source edits take effect without reinstalling.
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

A selected harness receives the project's complete managed skill set.
A globally enabled harness receives the complete registry-global skill set.
Executable detection affects initialization suggestions and trust eligibility, but it never suppresses skill links.

| Harness | Project skill root | Global skill root |
|---|---|---|
| Claude Code | `.claude/skills` | `~/.claude/skills` |
| Pi | `.agents/skills` | `~/.agents/skills` |

Each skill gets an independent native link.
Pi never points through Claude Code's directory.

## Machine configuration

Kura stores machine configuration at `${XDG_CONFIG_HOME:-~/.config}/kura/config.json`.

```json
{
  "schemaVersion": 1,
  "globalHarnesses": ["claude", "pi"]
}
```

`globalHarnesses` must be sorted, unique, non-empty, and contain only `claude` or `pi`.
The catalog path is not machine configuration and cannot be changed.

## Project manifest

An initialized project has `<project>/kura.json`.
The file is intended for version control.

```json
{
  "schemaVersion": 1,
  "harnesses": ["claude", "pi"],
  "skills": ["review", "tdd"]
}
```

Only directly requested skills are stored.
Dependencies are derived recursively from current optional registry metadata.
Unsupported migrated agents and plugins may be preserved under `legacy` and are not managed in this phase.
A malformed or newer manifest is never treated as an empty declaration.

## Catalog

Kura always reads the catalog at `~/.config/kura/catalog`.
There is no flag, environment variable, fallback, or saved setting that changes this path.
An older machine configuration's `catalog` field is ignored and removed when Kura rewrites the file.
Links to another catalog location are foreign and must be removed manually before Kura can recreate them from the fixed catalog.
The minimum catalog is a directory containing `skills/<name>/SKILL.md`.
`skill-registry.json` is optional metadata.
A registry-free skill is project-scoped by default and has no groups, dependencies, durable global policy, or upstream source.

When metadata exists, the registry name, directory name, and `SKILL.md` frontmatter name must agree.
A mismatch blocks installation.
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
  "version": 2,
  "local_skills": [{ "name": "review", "groups": ["global"] }]
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

Artifact commands use `--type skill` because skills are the only managed artifact type in this phase.

### `init`

```text
kura init [--harness {claude,pi}] [--yes] [--dry-run] [--verbose]
```

`init` is the first project mutation and refuses in `$HOME`.
It requires machine configuration to already exist and directs the user to `kura config` when it does not.
Repeated `--harness` values select the complete project harness set.
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
kura list --type skill [--group [TAG]] [--json]
```

Without a project manifest, `list` shows catalog and global state and sends an initialization notice to stderr.
With a manifest, a skill is linked only when every selected native view is correct.
Partial or conflicting state is `drift` and names each harness.
Bare `--group` groups the human report by metadata tag.
`--group TAG` filters by one opaque tag.
`--json` emits only JSON on stdout.

Existing row fields remain: `name`, `state`, `installed`, `global`, `groups`, `dependencies`, `reason`, `parent`, and `global_for`.
The additive `views` object is keyed by harness ID.
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
kura add [NAME...] --type skill [--group TAG] [--global]
```

Project `add` requires root `kura.json`, adds direct intent, derives dependencies, and reconciles every selected view atomically.
A fully healthy repeated add returns `ALREADY`.
A repeated add with drift repairs the selected views.
`--global` instead creates temporary scratch links in every globally enabled harness and does not write a global manifest.
The next `sync` restores registry-global policy.
`--group TAG` selects metadata-backed group members and cannot be combined with names.
Without `--global`, the project half is selected; with it, the global-policy half is selected.

### `remove`

```text
kura remove [NAME...] --type skill [--group TAG] [--global] [--no-cascade]
```

Project `remove` removes direct intent, re-derives dependencies, and reconciles every selected view atomically.
Dependencies still required by another direct skill remain.
`--global` removes matching temporary global links from every globally enabled harness until the next `sync`.
`--group TAG` follows the same partition rule as `add`.
`--no-cascade` remains accepted as a deprecated compatibility flag, but dependencies are declaratively derived from `kura.json`.

### `restore`

```text
kura restore [--type skill] [--dry-run]
```

`restore` requires root `kura.json` and creates missing direct and derived links in every selected harness.
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

`sync` projects the recursive registry-global closure into every globally enabled harness.
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

`doctor` can run without a project manifest.
It returns `DRIFT` for invalid configuration or manifests, missing desired content, unsafe collisions, and incorrect native views.
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

## Safety and transactions

A real path or foreign symlink is never replaced or deleted.
A managed link must occupy the expected native path and resolve under the fixed catalog's `skills` directory.

Every multi-path mutation follows one process:

1. Read and validate machine configuration, project intent, catalog metadata, and native state.
2. Derive the complete desired skill closure.
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
