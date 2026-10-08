# Plan: agents and bundles in every type-aware command

**Status:** Implemented
**Created:** 2026-10-08
**Scope:** `scout`, `sync`, `doctor`, `adopt`, `restore` and `converge` accept `--type agent` and `--type bundle`; plan provenance in `views.py`; tests and docs.

## Goal

`kura <command> --type agent|bundle` works wherever `--type skill` does, instead of the blanket refusal in `cli._dispatch`.
Only the legacy `plugin` refusal stays in the dispatcher.

## Evidence

- `doctor`, `restore` and `converge` already plan skills, standalone agents and bundle members through `views.project_plan`; they ignore `args.type` entirely.
- `sync` narrows only through `desired_names` (`commands/provision.py`), and `global_plan` can do skills+agents or skills only, never agents only.
- `adopt` and `scout` are skill-only, and `adopt` carries a second refusal of its own.
- Nothing on a plan records an entry's kind or the bundle that contributed it: `Action.skill` holds any name, `Plan.current`/`Plan.missing` are bare tuples, and `Plan.blocked` is plain strings. `checks._action_kind` guesses kind from the path.

## Decisions

1. **Provenance lives on the plan, filtering is a pure function over it.** Every `Action`, `current`, `missing` and `blocked` entry records its kind and name where it has one. A pure `narrow(plan, selection)` keeps the matching entries, testable over literal plans.
   Narrowing the manifest before planning was rejected: in `converge`, which deletes, a narrowed manifest makes every other kind look unwanted.
2. **Untyped entries survive every filter.** A harness root collision or missing native root blocks all kinds, so a narrowed run must still refuse on it.
3. **Selection semantics.**
   - `skill`: every skill entry, including bundle-member skills.
   - `agent`: every agent entry, including bundle-member agents.
   - `bundle`: entries contributed by at least one bundle in the manifest (its skills, its agents, `requires.skills`, `requires.agents`, and their dependency closure), even when also direct intent; plus bundle-level findings (missing or invalid bundle).
4. **A narrowed run deletes only inside its own selection.** `converge --type agent` may delete stale agent links; `converge --type bundle` never prunes orphans, because an orphan belongs to no bundle by definition. Unfiltered `converge` remains the pruning path.
5. **`sync --type agent`** converges global agents only; `global_plan` gains the agents-only mode it lacks. **`sync --type bundle`** refuses with `USAGE`: bundles are project-only (the same reasoning as `add --global --type bundle`).
6. **`adopt`**: `--type agent` adopts catalog-backed agent links that agree in every selected harness into `manifest.agents`. `--type bundle` adopts a bundle only when every member is linked and agrees in every selected harness; partial bundles are reported, never adopted. Untyped `adopt` does all three, bundles first, so members of an adopted bundle are not also recorded as direct intent.
7. **`scout`**: agents and bundles are matched by their registry `groups`, as skills are. A separate bundle `tags` key was rejected: `groups` already are the descriptive tags (ADR 0007), and a second field would be a parallel tag system. Untyped `scout` covers all three kinds, showing the kind suffix.
8. **Doctor** filters findings by kind; machine, project and trust findings are untyped and always shown. Its summaries name the selected kind instead of hardcoding "skills".

## Invariants to preserve

- `sync` deletion narrowings and the empty-desired guards; the `, 0 changes` marker.
- `remove`-style project confinement: nothing here crosses projects.
- Stdlib-only runtime; refusal wording untested, exit codes tested.

## Tasks

1. Provenance on `Action`, `Plan.current`, `Plan.missing`, `Plan.blocked`; `narrow`; replace `checks._action_kind` guessing with the recorded kind.
2. `doctor`, `restore`, `converge` narrowing.
3. `global_plan` agents-only mode; `sync --type agent`; `sync --type bundle` refusal.
4. `adopt` for agents and bundles; drop its module guard.
5. `scout` for agents and bundles.
6. Remove the dispatcher guard (keep legacy `plugin`); fix the stale `converge` comment in `cli.py`; update `SCOPE`, README usage lines and the skill-only statements, ARCHITECTURE.md.

## Verification

`make test` green, plus per-command tests asserting exit codes and the narrowed effects (a narrowed `converge` leaves other kinds' links untouched; `adopt --type bundle` refuses a partial bundle), and a manual `./bin/kura doctor --type bundle` against the fixture catalog.
