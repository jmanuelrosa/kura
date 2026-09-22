# Multi-harness skill management

## Status

Accepted for implementation.

## Goal

Kura manages one declared skill set across the native skill directories of the harnesses a user selects.
The first supported harnesses are Claude Code and Pi.
Kura remains a stdlib-only artifact manager, not a harness launcher, package manager, or replacement discovery mechanism.

The domain language is defined in [CONTEXT.md](../../CONTEXT.md).
The CLI behavior is defined in [multi-harness-skills-ux.md](multi-harness-skills-ux.md).

## Non-goals

- Managing agents, plugins, rules, hooks, extensions, prompts, themes, or settings in this phase
- Translating one harness's resource format into another
- Shipping a catalog or a fixed list of skills
- Locking skill content or requiring every developer to use an identical catalog
- Selecting or launching a default harness
- Discovering a project from Git or an ancestor directory
- Persisting an index of initialized projects
- Granting trust to a harness that is not installed locally

## Invariants

- Runtime code remains stdlib-only.
- Package logic remains under `kura/`; `bin/kura` remains wiring.
- Every project operation uses cwd as the exact project.
- `$HOME` is not a project.
- Every selected harness receives the same managed project skill set.
- Every globally enabled harness receives the same registry-global skill set.
- Each harness reads its own documented native directory without settings changes.
- Native links point directly to the fixed catalog skill.
- A real path or foreign symlink is never replaced or deleted.
- A mutation that spans harnesses is preflighted before any write.
- `sync` retains the exact `, 0 changes` closing marker.
- Builds remain deterministic.

## Configuration contracts

### Machine configuration

Path:

```text
${XDG_CONFIG_HOME:-~/.config}/kura/config.json
```

Schema:

```json
{
  "schemaVersion": 1,
  "globalHarnesses": ["claude", "pi"]
}
```

Rules:

- `schemaVersion` must be `1`.
- `globalHarnesses` is a sorted, unique, non-empty list of known harness IDs.
- Unknown fields may be preserved when Kura rewrites a valid file, but they do not affect behavior.
- The retired `catalog` field is accepted from an older configuration and removed on rewrite.
- Unknown schema versions and harness IDs are refusals.
- First-run `config` may create machine config after explicit confirmation. `init` never creates it and refuses without one.

### Project manifest

Path:

```text
<project>/kura.json
```

Normal schema:

```json
{
  "schemaVersion": 1,
  "harnesses": ["claude", "pi"],
  "skills": ["review", "tdd"]
}
```

A migrated manifest may also hold inert state:

```json
{
  "schemaVersion": 1,
  "harnesses": ["claude", "pi"],
  "skills": ["review"],
  "legacy": {
    "agents": {
      "reviewer": "direct"
    },
    "plugins": {
      "backend": "direct"
    }
  }
}
```

Rules:

- `harnesses` is a sorted, unique, non-empty list of known harness IDs.
- `skills` is a sorted, unique list of directly requested skill names.
- Dependencies are derived from current catalog metadata and are never persisted.
- `legacy` is omitted unless migration has unsupported state to preserve.
- The file is declarative and may be edited by a user.
- Unknown schema versions, unknown harnesses, malformed values, and duplicate entries are refusals for mutations.
- `doctor` can inspect an invalid manifest without treating it as an empty declaration.
- The file is intended for version control.

### Catalog

The catalog path is always `~/.config/kura/catalog`, derived from `$HOME` without consulting `XDG_CONFIG_HOME`.
No CLI flag, environment variable, fallback, or machine configuration field changes it.

The minimum valid catalog is:

```text
catalog/
└── skills/
    └── <name>/
        └── SKILL.md
```

`skill-registry.json` is optional metadata.
A registry-free skill:

- Is discovered from `skills/<name>/SKILL.md`
- Is project-scoped by default
- May be installed as a scratch global skill through explicit `--global`
- Has no groups, dependencies, durable global policy, or upstream update source
- Is omitted from registry-driven scouting and updates

When metadata exists, the registry key, directory name, and `SKILL.md` frontmatter name must agree.
A mismatch is a catalog error and blocks installation of that skill.

Kura does not record a catalog ID or skill digest.
Matching names across developers' catalogs are accepted as equivalent by explicit product choice.

## Harness contracts

### Profiles

A built-in declarative profile contains:

- Stable harness ID
- Display name
- Executable used only for detection and trust eligibility
- Project skill root
- Global skill root
- Instruction-file conventions

Version-sensitive behavior is implemented by an optional Python adapter beside the profile.
Trust adapters are code, not profile data.
A future skills-only harness should require a profile but no changes to skill planning.

### Native paths

| Harness | Project skill root | Global skill root |
|---|---|---|
| Claude Code | `.claude/skills` | `~/.claude/skills` |
| Pi | `.agents/skills` | `~/.agents/skills` |

For a selected skill named `review`, Kura creates independent links:

```text
<project>/.claude/skills/review -> <catalog>/skills/review
<project>/.agents/skills/review -> <catalog>/skills/review
```

No harness view points through another harness.
There is no Kura-managed catalog alias.
Executable absence does not suppress skill projection.
Native roots are created lazily when the first managed skill needs them.

### Instructions

Kura uses `AGENTS.md` as the shared instruction surface and Claude's documented import syntax as the bridge.

| Existing state | `init` behavior |
|---|---|
| Neither file | Create `AGENTS.md` containing `# Project instructions` and `CLAUDE.md` containing `@AGENTS.md` |
| `AGENTS.md` only | Create the minimal `CLAUDE.md` importer |
| `CLAUDE.md` only | Preserve it; Pi reads it natively; offer migration but do not rewrite |
| Both files | Preserve both; report split instructions when Claude does not import `AGENTS.md` |

New instruction files are intended for version control.
Kura never runs `git add`.

## Desired-state derivation

Let `direct` be the names in the project manifest.
Let `closure(direct)` be the deterministic dependency closure from current registry metadata.
Let `global` be the dependency closure of registry-global roots.

The project intent is `closure(direct)`.
The project native view is `closure(direct) - global`.
The global native view is `global` plus temporary scratch global additions until the next `sync`.

A direct skill that later becomes global remains in the project manifest.
Its project links become redundant and may be removed after global availability is verified.
If global policy later disappears, project convergence restores its project links.

A project operation requiring a global skill refuses unless every selected project harness is globally enabled and has a current global link.
The refusal names the missing harnesses and the required `config` and `sync` actions.

A missing direct catalog skill preserves manifest intent and produces `DRIFT`.
A missing dependency blocks preflight because no complete desired state can be formed.

## Link ownership

A project link is managed only when all conditions hold:

1. It occupies the selected harness's expected path for the skill name.
2. The skill belongs to direct or derived project state.
3. It is a symlink.
4. It resolves under the fixed catalog's `skills` directory.

A global link uses the same test against registry-global or scratch-global state.

Consequences:

- A real directory is always user-owned.
- A symlink outside the fixed catalog is foreign.
- Repairing a link to an old catalog requires manual removal before `restore`.

## Transaction model

Every multi-path mutation follows one shape:

1. Read and validate configuration, manifest, catalog, and native state.
2. Derive the complete desired state.
3. Build a deterministic plan.
4. Preflight every source, destination, collision, and required global dependency.
5. Display the plan when interactive, dry-run, or verbose behavior requires it.
6. Apply filesystem changes.
7. Write configuration or manifest state last.
8. Roll back changed files and links if a later application step fails.

Rollback restores the original bytes of regular files and the original targets of changed symlinks.
Rollback failure is reported separately and returns `DRIFT`.
No safety predicate is bypassed by `--yes` or `--verbose`.

## Command contracts

### `init`

`init` is the first project mutation.
It uses cwd and refuses in `$HOME`.
It suggests harnesses from project footprints and installed executables, with footprints ranked above executables.
The user must select at least one harness and is never asked for a default.

`init` requires machine config to already exist and refuses, naming `kura config`, when it is absent.
Noninteractive runs accept repeated `--harness` and `--yes`.

Re-running `init` replaces the selected harness set, retains direct skills, and converges views.
Removing a harness deletes only its managed links.
Instruction files remain under the shared rules above.

`--dry-run` writes nothing.
`--verbose` shows every manifest conversion, direct and derived skill, native link, topology change, and preserved legacy entry.

### Legacy migration

Legacy state is read from `.claude/kura.json`.
Rows considered direct become project `skills` entries.
`dep-of:` rows are dropped and re-derived.
Agent and plugin rows are preserved under `legacy` without being managed.

Migration writes root `kura.json`, converges native views, and deletes the old manifest only after success.
If both manifests exist, agreeing state may be merged and conflicting state refuses with a semantic diff.

The old Pi topology `.agents/skills -> ../.claude/skills` converts only when every exposed entry is proven managed.
Foreign or real content causes refusal and an unconditional per-entry difference report.

### `config`

Bare `config` prints the saved machine configuration and fixed catalog path.
A mutation against an absent machine config creates it instead of refusing: `config` is the only command that does, and it may create the fixed catalog's empty `skills/` directory after confirmation on that first run.
Once machine config exists, repeated `--harness` replaces `globalHarnesses`.
Mutations support `--yes`, `--dry-run`, and `--verbose` and converge global views in the same transaction.

### `add` and `remove`

Project operations require root `kura.json` in cwd.
Global operations retain explicit `--global` and need only machine config.
`--type skill` remains required where the current CLI requires a type.

Project `add` adds direct intent and reconciles all selected views atomically.
Adding an already declared and fully current skill returns `ALREADY`.
Adding an already declared but drifted skill repairs the views.

Project `remove` removes direct intent, re-derives dependencies, and reconciles all selected views atomically.
A dependency that remains required is kept.

Global `add` and `remove` apply scratch changes to every globally enabled harness.
No global manifest is created.
The next `sync` restores registry-global policy.

Existing group partition and scope confirmation behavior remains where metadata supports it.

### `restore`

`restore` requires a project manifest.
It creates missing direct and derived links in every selected harness and deletes nothing.
Conflicts refuse before any change.

### `converge`

Single-project `converge` requires a project manifest and exactly reconciles all selected views.
It may delete only managed links.

`converge --all` scans cwd recursively, or the roots supplied by repeatable `--root`.
Explicit roots replace cwd.
The scan includes each root, has no depth cap, does not follow directory symlinks, and prunes hidden, VCS, dependency, cache, and vendor trees.
Only directories with root `kura.json` are projects.
Claude's private project registry and a Kura project index are not used.

Existing `--quiet`, `--verbose`, and `--dry-run` behavior remains, generalized across harnesses.

### `adopt`

`adopt` considers catalog-backed links from any selected harness.
It refuses same-name links resolving to different targets.
It infers direct roots, records only those names, lists omitted derived dependencies, and fills missing selected views.
Real directories and foreign links are not adopted.
An ambiguous standalone skill is direct.

### `sync`

`sync` projects the registry-global closure into every globally enabled harness.
It prunes only symlinks resolving under the current catalog.
It never touches real paths, foreign links, or preserved agent/plugin state.

If desired global state and existing managed state are both empty, the result is successful with `0 changes`.
If desired global state is empty while managed global links exist, `sync` returns `DRIFT` and deletes nothing.
An explicit global harness removal through `config` may remove that harness's managed links.

### `list`

Without a project manifest, `list` shows catalog and global state only.
With a manifest, a configured skill is `linked` only when every selected native view is correct.
Partial or conflicting state is `drift` and identifies each harness.

Existing JSON fields remain.
A `views` object is added, keyed by harness ID.
The documented `state` enumeration adds `drift`.

### `scout`, `update`, and `outdated`

`scout` requires a project manifest and only recommends skills whose metadata supplies relevant groups.
`scout --add` uses the same project transaction as `add`.

`update` and `outdated` remain registry-wide and act only on skills with upstream source metadata.
They do not require project initialization.

### `doctor`

`doctor` can run without a project manifest.
It returns `DRIFT` for invalid configuration or manifests, missing desired content, unsafe collisions, and incorrect native views.
It reports these as informational without failing health:

- Selected executable absent
- Harness trust not granted
- Split instruction files
- Preserved legacy agent/plugin state

### `trust`

The interface is cwd-only:

```text
kura trust
kura trust --on
kura trust --off
```

Bare `trust` reports.
Mutations support `--dry-run`.
The command requires `./kura.json`, intersects selected harnesses with executables available on `PATH`, skips selected unavailable harnesses, and refuses when none are available.

Claude keeps its existing Git-root and main-worktree identity and refuses if `~/.claude.json` is absent.
Pi uses canonical cwd and nearest-parent semantics and may create `~/.pi/agent/trust.json`.
Kura follows Pi's current private file format and lock protocol, which is a version-sensitive adapter contract rather than a documented public API.

A multi-harness trust mutation preflights every store.
If a later write fails, earlier stores are restored to their exact original bytes and modes.

## CLI surface

The command set is:

```text
init config list scout add remove sync update outdated doctor adopt restore converge trust
```

Artifact commands retain `--type` but accept only `skill` in this phase.
The existing required-versus-optional placement of `--type` remains where meaningful.

A project manifest is required for project `add`, project `remove`, `scout`, `adopt`, `restore`, single-project `converge`, and `trust`.
It is not required for `init`, `config`, `doctor`, global `add`, global `remove`, `sync`, `update`, `outdated`, `converge --all`, or `list`.
Missing project scope uses existing `NO_PROJECT` with an initialization-specific remedy.

## Reporting

Reports count logical skill changes separately from physical link changes.
A multi-harness operation must not make two links look like two selected skills.
Refusals show actionable differences without requiring `--verbose`.
`--verbose` adds per-skill and per-harness decisions.
`--dry-run` uses the same plan and rendering as application.

The existing no-color behavior, terminal detection, stdout/stderr separation, and `list --json` payload discipline remain.

## File-level implementation plan

### Foundation

- Add machine configuration parsing and writing under a new focused module such as `kura/config.py`.
- Add harness profiles and path derivation under `kura/harnesses.py`.
- Narrow `kura/catalog.py` and `kura/registry.py` to filesystem-first skills with optional metadata.
- Change `kura/state.py` to the root versioned declarative manifest and legacy migration parser.
- Add transaction planning and rollback primitives in the package, not the shim.

### Project lifecycle

- Add `kura/commands/init.py` and `kura/commands/config.py`.
- Generalize `add.py`, `remove.py`, `restore.py`, and `adopt.py` around direct intent plus derived closure.
- Replace Claude-owned projection in `kura/pi.py` with independent profile-driven native views.
- Rework `commands/converge.py` and `projects.py` around root manifests and cwd or explicit-root scanning.

### Global lifecycle

- Generalize `commands/provision.py` across global harness profiles while preserving pruning guards and summary wording.
- Preserve scratch-global add/remove behavior across every globally enabled harness.
- Ensure update and outdated tolerate absent registry metadata and ignore registry-free skills.

### Diagnostics and presentation

- Update `commands/listing.py` with configured and per-harness view states while preserving existing JSON fields.
- Rework `checks.py` around machine config, project manifest, native views, instructions, trust, and inert legacy notes.
- Generalize `commands/trust.py`, preserve Claude logic in `workspace.py`, and implement Pi writes beside `pi_trust.py` with lock compatibility.
- Add commands, families, scopes, and flags to `cli.py`.
- Add any new exit naming to `errors.py` only if implementation proves existing `NO_PROJECT` cannot represent the agreed refusal.

## Test plan

### Pure tests

- Parse, validate, normalize, and round-trip both versioned JSON formats.
- Reject unknown versions, harnesses, duplicates, and malformed shapes.
- Merge optional registry metadata with filesystem skills.
- Derive project and global closures without persisted dependency rows.
- Derive paths from profiles.
- Classify current-catalog, old-catalog-during-migration, foreign, real, and broken entries.
- Build deterministic plans and rollback snapshots.
- Render additive JSON view state.

### Filesystem tests

- Create independent Claude and Pi project links to the same catalog skill.
- Create independent global links.
- Repair one missing harness view through `add` and `converge`.
- Refuse every real-path and foreign-link collision before any write.
- Restore without deletion and converge with safe deletion.
- Reconfigure selected and global harnesses.
- Migrate legacy manifests and the old Pi directory link.
- Refuse migration with an unconditional semantic difference.
- Move a catalog across global, cwd, and explicit roots without retaining old history.
- Preserve omitted old links as foreign.
- Exercise empty-global pruning guards.
- Create instruction scaffolds for every existing-file combination.
- Acquire and refuse Pi trust locks and roll back cross-harness trust updates.

### CLI and packaging tests

- Cover interactive and noninteractive `init` and `config`.
- Verify cwd-only project behavior and `$HOME` refusal.
- Verify uninitialized command matrix.
- Verify `--all` scanning and pruning.
- Preserve every documented command, flag, exit code, JSON field, and `, 0 changes` marker.
- Update fixture documentation whenever catalog fixtures change.
- Run `make test` and deterministic build checks.

## Documentation changes

- Rewrite `README.md` around `init`, machine config, root manifest, native harness views, and skills-only support.
- Update `ARCHITECTURE.md` rather than layering a contradictory appendix over the current Claude-owned model.
- Document Pi trust as a version-sensitive private integration.
- Document catalog-move consequences prominently.
- Keep `CONTEXT.md` implementation-free.

## Work breakdown

1. Foundation owner: config schemas, manifest schema, catalog discovery, harness profiles, path derivation, and plan primitives.
2. Project lifecycle owner: init, migration, add/remove, restore, adopt, and instruction scaffolding.
3. Global lifecycle owner: config mutations, global projection, scratch global operations, and sync safety.
4. Trust owner: Claude preservation, Pi lock-compatible writer, native identity reporting, and rollback.
5. Diagnostics owner: list states, JSON compatibility, doctor checks, and report counts.
6. Integration owner: CLI wiring, help families, README, architecture rewrite, packaging assertions, and full-suite verification.

Foundation contracts land first.
Project, global, trust, and diagnostics work can then proceed independently before integration.
