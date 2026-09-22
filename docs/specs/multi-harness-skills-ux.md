# Multi-harness skills CLI UX

## Status

Accepted interaction specification for the first Pi and Claude Code implementation.

## Principles

- Say harness when referring to Pi or Claude Code.
- Say skill when referring to the managed artifact.
- Show logical skill changes separately from physical link changes.
- Name the harness on every partial, conflicting, skipped, or trust-sensitive result.
- Never hide actionable information behind `--verbose`.
- A prompt confirms a complete plan, not a sequence of individual filesystem operations.
- `--yes` accepts a safe plan and never overrides collision checks.
- `--dry-run` renders the application plan without writing.
- Plain and colored output contain the same words and structure.
- JSON stdout contains only JSON; notices and warnings go to stderr.

## Shared output states

| State | Meaning |
|---|---|
| Available | The catalog has the skill and the project does not declare it |
| Configured | The project directly declares the skill or derives it as a dependency |
| Linked | Every selected harness has the expected catalog link |
| Drift | At least one selected harness is missing, conflicting, or points elsewhere |
| Global | Registry policy makes the skill available through every globally enabled harness |
| Missing | Declared or registered content is absent from the catalog |
| Legacy | Preserved agent or plugin state is intentionally unmanaged in this phase |

A skill is never described as linked from one healthy harness when another selected harness is broken.
The row becomes drift and names each view.

## Interactive `init`

### Prompt order

1. Establish cwd and refuse `$HOME`.
2. Read existing root and legacy manifests.
3. Detect harness evidence.
4. Ask for project harnesses.
5. Require machine config; refuse and name `kura config` when absent.
6. Inspect instruction files.
7. Derive direct and dependency skills.
8. Preflight native views.
9. Show the plan.
10. Ask once for confirmation.
11. Apply or cancel.

### Harness evidence

Project evidence appears before executable evidence.
Each suggestion names its source.

```text
🔎 Detected harnesses in ~/work/api
  ✓ Claude Code  .claude/ and CLAUDE.md
  ✓ Pi           pi executable on PATH

Select one or more harnesses:
  [x] Claude Code
  [x] Pi
```

If nothing is detected, both supported harnesses remain available but unselected.
The user must select at least one.
There is no default-harness prompt.

### Missing machine configuration

`init` never creates machine configuration.
When it is absent:

```text
✗ Kura machine configuration does not exist.
  Run `kura config` to create it.
```

First machine setup happens through `kura config`; see its section below.

### New project plan

```text
🧭 Initialize ~/work/api

Project
  Harnesses: claude, pi
  Direct skills: none

Instructions
  + AGENTS.md
  + CLAUDE.md importing AGENTS.md

Native views
  No skill directories are needed yet.

Apply this plan? [y/N]
```

Cancellation changes nothing and exits successfully with a cancellation message.
End-of-file at a prompt is a refusal, not consent.

### Existing instructions files

If only `CLAUDE.md` exists:

```text
⚠ Preserving CLAUDE.md.
  Pi can read it natively, so no instruction migration is required.
  Kura will not create AGENTS.md or rewrite existing instructions automatically.
```

If both files exist and Claude does not import `AGENTS.md`:

```text
⚠ Instructions are split.
  Pi reads AGENTS.md.
  Claude Code reads CLAUDE.md.
  Kura will preserve both files. Run `kura doctor` for the remediation.
```

The warning does not block initialization.

### Legacy migration

The default plan summarizes counts:

```text
Migration
  3 direct skills retained
  4 dependency rows will be re-derived
  2 legacy agent/plugin records preserved
  .claude/kura.json will be removed after successful convergence
```

`--verbose` expands every row:

```text
Migration details
  + review                     direct
  + tdd                        direct
  · test-helper                derived from tdd, not persisted
  · reviewer                   legacy agent preserved
```

An old Pi directory-link conflict always shows a difference:

```text
✗ Cannot replace .agents/skills.

Only visible through the current Claude-backed view
  frontend-design   real directory
  local-review      foreign symlink -> ../team-skills/local-review

Kura can convert the old view only when every exposed entry is catalog-backed and managed.
No files were changed.
```

### Noninteractive `init`

Requires machine configuration to already exist:

```sh
kura init --harness claude --harness pi --yes
```

Missing required choices in a non-TTY session are usage errors.
`--dry-run` does not require `--yes` and writes nothing.

## `config`

Bare output:

```text
⚙ Kura machine configuration
  File: ~/.config/kura/config.json
  Catalog: ~/.config/kura/catalog
  Global harnesses: claude, pi
```

A mutation previews global link changes.

```text
kura config --harness claude --harness pi --yes
```

Repeated `--harness` values replace the complete global set.
At least one is required.
A successful mutation immediately converges global views.

### First machine setup

A mutation against an absent machine configuration bootstraps it instead of refusing, and can run from anywhere, including `$HOME`:

```sh
kura config --harness claude --harness pi --yes
```

A missing fixed catalog offers one choice on that first run:

```text
The catalog directory does not exist.
Create ~/.config/kura/catalog with an empty skills/ directory? [y/N]
```

Once machine configuration exists, a further `--global-harness`-style bootstrap has nothing left to do: repeated `--harness` on `config` simply replaces the saved set.

## `list`

### Healthy project

```text
🧩 Available skills:
  ✓ review (linked: claude, pi) [review]
  ✓ tdd (linked: claude, pi) [testing]
  · frontend-design [frontend]

✨ 3 skills, 2 configured, 4 links current
```

### Partial project

```text
🧩 Available skills:
  ! review (drift: pi missing; claude linked) [review]

→ kura converge --type skill
✨ 1 skill configured, 1 harness view needs repair
```

### Uninitialized directory

`list` remains useful and shows catalog plus global state.
It sends this notice to stderr:

```text
This directory is not initialized. Project state is omitted; run `kura init` here.
```

### JSON

Existing fields remain unchanged where their meaning still applies.
The `state` field adds `drift`.
A `views` object is additive:

```json
{
  "name": "review",
  "state": "drift",
  "global": false,
  "groups": ["review"],
  "dependencies": [],
  "views": {
    "claude": {
      "state": "linked"
    },
    "pi": {
      "state": "missing"
    }
  }
}
```

JSON mode emits no heading, hint, color, or count on stdout.
Warnings remain on stderr.

## `add` and `remove`

A project add reports one logical change and all physical views:

```text
✓ Added 'review' to kura.json
  ✓ Claude Code  .claude/skills/review
  ✓ Pi           .agents/skills/review

✨ 1 skill configured, 2 links created
```

Dependencies appear indented under their direct skill and are labeled derived.
Global dependencies name every global harness view.

Re-adding a fully healthy direct skill returns `ALREADY`.
Re-adding a drifted direct skill repairs it:

```text
✓ 'review' was already configured
  ✓ Claude Code  already linked
  + Pi           link restored

✨ 0 skill changes, 1 link created
```

A collision refuses the entire operation:

```text
✗ Cannot add 'review'.
  Claude Code: .claude/skills/review is a real directory
  Pi: .agents/skills/review is available

No manifest or links were changed.
```

Global add and remove name all globally enabled harnesses and state that the change is temporary until `sync`.

## `restore`

`restore` reports missing links it will recreate and explicitly says it will delete nothing.

Empty state:

```text
✓ Project declaration is already restored across claude and pi.
✨ 0 skill changes, 0 link changes
```

Missing catalog content returns `DRIFT`, retains the manifest entry, and names each affected harness.

## `converge`

Single-project output reports both views even in steady state:

```text
✓ Claude Code skill view is current.
✓ Pi skill view is current.
✨ 2 skills configured, 4 links current, 0 changes
```

`--all` scans cwd unless one or more `--root` values replace it.
Default output suppresses steady projects and prints changed or warning projects.
`--verbose` includes every project and per-skill decision.
`--quiet` keeps stdout empty while warnings and failures remain on stderr.

No-result state:

```text
✨ No initialized projects found under ~/work
```

## `adopt`

The plan separates inferred direct roots from omitted dependencies:

```text
Adopt as direct
  + spec-driven-development

Derived, not persisted
  · context-engineering
  · test-driven-development

Fill missing views
  + Pi  spec-driven-development
  + Pi  context-engineering
  + Pi  test-driven-development
```

Conflicting same-name targets show both harness paths and targets and refuse before writing.

## `sync`

Rows are grouped by logical skill, with harness detail in verbose mode.
The closing summary keeps the exact automation marker:

```text
✨ 12 global skills across 2 harnesses, 0 changes
```

When desired global state is empty and managed links remain:

```text
✗ Global metadata resolved to an empty set while managed links still exist.
  Nothing was removed.
```

An explicit global harness removal through `config` is not subject to that ambiguity because the user named the removed harness.

## `doctor`

Order findings by actionability:

1. Invalid machine configuration
2. Invalid project manifest
3. Missing catalog content or dependencies
4. Unsafe native-view collisions
5. Missing or stale managed links
6. Trust notes
7. Executable-availability notes
8. Instruction-split notes
9. Legacy-state notes

Drift findings make the command exit `DRIFT`.
Informational notes do not make a healthy project fail.

An uninitialized cwd is reported with the exact initialization remedy while global and catalog checks still run.

## `trust`

Bare `trust` reports each selected, installed harness:

```text
🔐 Trust for ~/work/api
  Claude Code  trusted as ~/work/api
  Pi           inherited trust from ~/work
```

Selected but unavailable harnesses are explicit:

```text
  Pi           skipped, executable not found on PATH
```

`--on` and `--off` preview native targets before confirmation or application:

```text
Trust targets
  Claude Code  ~/.claude.json key ~/work/api
  Pi           ~/.pi/agent/trust.json key /real/path/work/api
```

If no selected harness executable is available, refuse.
If Claude state does not exist, refuse before creating Pi state.
If a later write fails, report rollback of earlier stores.
`--dry-run` acquires no write lock and changes nothing.

## Refusal and recovery language

Every refusal answers four questions:

1. What operation stopped?
2. Which path or harness caused it?
3. What was preserved?
4. What command or manual step resolves it?

A missing project manifest uses existing `NO_PROJECT` and says:

```text
✗ This directory is not initialized for project operations.
  Run `kura init` in this exact directory.
```

Do not imply ancestor discovery.

A malformed or newer manifest never reads as an empty project.
A real path is never called Kura-managed.
A foreign symlink always prints its target.

## Accessibility and streams

- Preserve the current terminal-only color behavior.
- `NO_COLOR` wins over `FORCE_COLOR`.
- Markers and words carry meaning without color.
- Prompts use text labels and visible selection state.
- stdout carries successful human reports or machine payloads.
- stderr carries warnings, refusals, and out-of-band notices.
- Piped human output remains plain text.
- JSON output contains no ANSI escapes.

## Responsive terminal behavior

The CLI does not hard-wrap paths, command lines, or semantic-diff rows.
It follows the terminal's natural wrapping.
Indentation remains meaningful when color is disabled.
Very long paths appear in full in verbose and refusal output because truncation would make remediation unsafe.
