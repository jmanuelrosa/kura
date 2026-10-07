# Plan: explicit catalog global policy

**Status:** Implemented in Kura (catalog cutover deferred by user)
**Created:** 2026-10-06
**Scope:** Kura registry parsing, existing global-policy consumers, tests, editor schema, and documentation; coordinated migration of the fixed machine catalog's skill and agent registries, owned by `/Users/jmanuelrosa/Developer/dotfiles`.
**Research:** [Planning groundwork](../../.claude/state/research/2026-10-06-research-explicit-catalog-global-policy.md)

## Goal and non-goals

Separate catalog classification (`groups`) from the declaration that an artifact is a global installation root.
Success means a documented explicit registry field determines global root selection without accidentally changing dependency closure, native-view ownership, transactions, or project intent.

Do not design global bundles, add new dependencies, alter harness paths, or fix unrelated catalog drift.
Do not change implementation code or machine catalog files during planning.

## Evidence and constraints

- **High confidence:** global policy currently comes from `Artifact.tagged_global`, which checks membership in `groups` (`kura/catalog.py:75-76`).
- **High confidence:** registered skills and standalone agents share registry parsing; global-agent dependencies participate in native global planning (`kura/catalog.py:403-411`, `kura/views.py:353-381`).
- **High confidence:** existing boolean metadata is strictly validated and defaults to false (`kura/catalog.py:269-271`).
- **High confidence:** dependencies can be globally selected without declaring themselves global roots (`kura/scope.py:21-51`).
- Preserve the distinction between a root-policy flag and effective/physical global state already exposed by list JSON (`kura/commands/listing.py:245-256`).
- Preserve the empty-desired pruning guards and the `, 0 changes` summary contract (`ARCHITECTURE.md:208-211`, `AGENTS.md`).
- Removing a `global` group tag changes group selection as well as root policy; compatibility must be explicit, never silently chosen.
- A read-only live-catalog inspection found 20 global skill roots and four global agent roots; its 17 bundle rows independently carry unsupported groups.
- No external integration contract changes are proposed.
- **Research verification already completed:** the literal-data policy self-check, read-only catalog ownership/root inspection, and 91 focused baseline tests passed.
The full suite, builds, migration tests, and rollout verification below have not been run.
- Workflow decision: to-plan's single-question interview and planning-only boundary override grilling's batched questions and ask-user's generic instruction to proceed to implementation; no implementation follows a decision or plan approval.
- Output-contract override: planning-and-task-breakdown requests separate `tasks/plan.md` and `tasks/todo.md`; this workflow instead keeps one plan and task list at the existing path.
- Tier 2 technique override: planning-and-task-breakdown's check that no task touches more than about five files is traded against the atomic registry cutover in task 1.
The model, active consumers, fixtures, and affected test writers must change together to avoid a broken intermediate contract; narrow regression checks gate subsequent tasks.
- The research directory `.claude/state/research/` is not ignored by Git; deciding whether to commit or ignore it remains the user's choice, and no ignore rules change in this plan.

## Agreed decisions

- **2026-10-06, user:** use a registry `global` boolean, with true declaring a global root and omission or false not declaring one.
This separates installation policy from descriptive groups and matches the existing CLI terminology.
`is_global` and a scope enum were considered and not selected.
- **2026-10-06, user:** apply the flag to both registered skills and standalone agents; bundles remain project-only.
This replaces both existing uses of the global tag rather than leaving competing conventions.
Preserve root-only semantics: false or omission does not prohibit global installation as a dependency.
- **2026-10-06, user:** require migration and reject any legacy `global` group tag, even when a new flag is also present.
Do not silently ignore legacy tags or maintain dual policy sources.
Migration moves the old tag to `global: true` while preserving all other groups; `--group global` gets no compatibility alias.
This is an explicitly agreed breaking catalog-contract change.
- **2026-10-06, user:** include the actual catalog's skill and agent registry migration in the implementation plan.
Coordinate parser and catalog rollout, verify global roots are preserved, and leave unrelated bundle metadata untouched.
This initially approved planning that migration, not executing it.
- **2026-10-06, implementation:** the user explicitly requested implementation, then chose to finish Kura only for now after the dotfiles access prerequisite remained unsatisfied.
Task 5 is deferred by the user; the live catalog and installed executable must remain unchanged until a separately coordinated cutover.

## Implementation tasks

### 1. Switch the registry contract as one coherent slice

- [x] Add an internal `Artifact.is_global` boolean loaded from the JSON `global` field for registered skills and standalone agents.
- [x] Default omission to false, require actual boolean values, and reject a legacy `global` group tag with migration guidance regardless of whether the new flag is also present.
- [x] Replace every active `tagged_global` consumer with the explicit field and remove the old property; do not retain an alias or refactor dependency algorithms.
- [x] Reject `global` on bundle registry rows, including false, rather than silently accepting unsupported policy metadata.
- [x] Migrate positive fixtures, literal artifacts, and affected test writers from the old tag to the new field, retaining descriptive groups and keeping legacy tags only in deliberate refusal cases.
- **Files:** `kura/catalog.py`, `kura/scope.py`, `kura/views.py`, `kura/commands/listing.py`, `tests/fixtures/catalog/skill-registry.json`, and affected existing test writers such as `tests/test_global_agents.py`, `tests/test_scope.py`, and `tests/test_multi_harness.py`.
- **Depends on:** none.
- **Acceptance criteria:** both local and upstream skill/agent rows select the same global roots after migration; groups no longer determine policy; no active old-property references remain; bundles and inert plugin behavior gain no new global lifecycle.
- **Verification:** `uv run --offline --with pytest --with pyyaml pytest -q tests/test_scope.py tests/test_global_agents.py tests/test_multi_harness_foundation.py tests/test_bundles_catalog.py tests/test_multi_harness.py`; search for `tagged_global` and audit remaining legacy-tag fixtures.

### 2. Prove validation and dependency semantics

- [x] Add parametrized cases for local/upstream rows of both supported types: true, false, absent, and invalid values such as null, integers, strings, lists, and objects.
- [x] Cover rejection of the legacy tag by itself and alongside each boolean value, plus rejection of bundle global metadata.
- [x] Prove that a false or omitted flag does not exclude a skill required by a global skill or standalone agent, including recursive and cyclic dependencies.
- [x] Prove invalid or partially migrated registry input refuses before links or declarations change, and preserve the established malformed-catalog exit handling.
- **Files:** `tests/test_multi_harness_foundation.py`, `tests/test_scope.py`, `tests/test_global_agents.py`, `tests/test_bundles_catalog.py`, and `tests/test_acceptance_contracts.py`.
- **Depends on:** task 1.
- **Acceptance criteria:** root declarations are distinct from effective global dependencies; malformed declarations never silently become project-only; refusals have no filesystem side effects.
- **Verification:** `uv run --offline --with pytest --with pyyaml pytest -q tests/test_multi_harness_foundation.py tests/test_scope.py tests/test_global_agents.py tests/test_bundles_catalog.py tests/test_acceptance_contracts.py`.

### 3. Preserve cross-harness behavior and public output

- [x] Retain sync pruning ownership, the empty-desired guards, and the exact `, 0 changes` marker.
- [x] Cover explicit false/omission in project selection, global fallback, scratch-global operations, and list JSON without adding output fields or changing existing derived `global` meaning.
- [x] Prove descriptive groups still support grouping, filtering, and recommendations independently of the flag, with no `--group global` compatibility alias.
- [x] Extend the existing registry-stamping preservation test to show `update` changes its timestamp without losing the new field or descriptive groups.
- **Files:** `tests/test_multi_harness.py`, `tests/test_multi_harness_safety.py`, `tests/test_multi_harness_surfaces.py`, and `tests/test_upstream.py`.
- **Depends on:** tasks 1 and 2.
- **Acceptance criteria:** the metadata representation changes, but selected roots, dependency behavior, view safety, JSON contracts, and provisioning summaries remain unchanged for equivalently migrated inputs.
- **Verification:** `uv run --offline --with pytest --with pyyaml pytest -q tests/test_multi_harness.py tests/test_multi_harness_safety.py tests/test_multi_harness_surfaces.py tests/test_upstream.py`; then `make test` as the implementation checkpoint.

### 4. Document and validate the authoring contract

- [x] Add the optional boolean to both local and upstream skill definitions in the existing editor schema; remove global-policy wording from groups and represent rejection of the retired tag.
- [x] Update examples and user-facing rules: descriptive groups, true global roots, false/omission defaults, dependency closure, mandatory migration, and no group-selector alias.
- [x] Explain that a registry root declaration differs from effective or physical global state in list JSON, and that registry `version` does not negotiate this breaking change.
- [x] Update fixture documentation and relevant static packaging assertions without adding schema-validation dependencies or creating a new agent schema.
- **Files:** `docs/schemas/skill-registry.schema.json`, `README.md`, `ARCHITECTURE.md`, `tests/fixtures/README.md`, and `tests/test_packaging.py`.
- **Depends on:** tasks 1-3.
- **Acceptance criteria:** schema and runtime agree on the new field and retired tag; the README describes migration for both skills and agents; accepted ADRs remain untouched.
- **Verification:** `uv run --offline --with pytest --with pyyaml pytest -q tests/test_packaging.py`; validate schema/examples and fixture JSON with existing stdlib test patterns; then `make test` and `make checksum` before rollout.

### 5. Coordinate the actual catalog cutover (deferred by user)

- [ ] Before editing dotfiles, obtain read access to its root and harness `AGENTS.md` files, follow their instructions, and recheck both registry files for concurrent changes.
- [ ] Capture exact pre-migration registry bytes and derive the old skill/agent root sets from every local and upstream row rather than hardcoding observed names or counts.
- [ ] Coordinate activation of the tested updated executable with the operator and pause old Kura/provisioning mutations while the catalog is converted.
No old executable may reconcile the migrated catalog.
- [ ] Move only each legacy global tag to `global: true`, preserve other groups and row metadata, and leave untagged rows without an unnecessary false field.
Edit the resolved owning files without replacing the catalog symlink.
- [ ] Compare exact before/after root sets and unchanged dependency metadata; validate the migrated skill and agent rows read-only with the new parser.
Restore captured registry bytes if a partial conversion fails before any reconciliation.
- [ ] Run whole-catalog read-only checks only when existing unrelated catalog refusals are resolved through separate authorized work.
Do not remove, hide, or bypass bundle metadata errors to run `sync`.
Any native-view reconciliation remains subject to normal preflight and mutation confirmation.
- **Files:** the resolved `skill-registry.json` and `agent-registry.json` under the fixed catalog, currently owned by `dotfiles/roles/ai/files/harness/kura/catalog/`.
The bundle registry, provisioning configuration, and native links are not migration-edit targets.
- **Depends on:** tasks 1-4, readable dotfiles instructions, and operator coordination of the executable cutover.
- **Acceptance criteria:** exactly the previous roots are explicitly flagged, descriptive metadata and dependency edges are unchanged, no legacy global tags remain, and an old executable cannot silently reconcile the new metadata.
Existing bundle drift is reported without alteration.
- **Verification:** read-only JSON comparisons and updated-parser checks for both registries; `./bin/kura list --type skill --json` and `./bin/kura list --type agent --json` are whole-catalog checks and remain blocked by the known bundle refusal until separately resolved.
Use `./bin/kura doctor` to report remaining catalog/view drift, not as permission to repair it.

## Implementation progress

The user explicitly requested implementation on 2026-10-06.
Tasks 1-4 are complete.
Both dotfiles instruction-file reads still return `Sandbox: read access denied`, and the user explicitly chose to defer task 5 and finish Kura only for now.
The live catalog, installed executable, provisioning configuration, and native links remain untouched.

- `make test`: 682 passed.
- `git diff --check`: passed.
- Two consecutive `make checksum` builds produced the same SHA-256: `f84f8dec9c8f0b7a63e5ddfa6059137e600a87acb99bd20fbdf33c1943bfe217`.
- A temporary-HOME probe confirmed the built executable recognizes explicit global roots; the installed executable ignores the new flag.
The initial probe incorrectly included hidden dependency-only skills in expected list rows; correcting the expectation to visible rows resolved that probe failure without changing runtime code.
- JSON Schema authoring details were checked through ctx7 against the official [boolean](https://json-schema.org/understanding-json-schema/reference/boolean), [combining](https://json-schema.org/understanding-json-schema/reference/combining), and [const](https://json-schema.org/understanding-json-schema/reference/const) documentation.

## Risks and open questions

- **Execution prerequisite, user/operator:** the sandbox denied reading `/Users/jmanuelrosa/Developer/dotfiles/AGENTS.md` and `roles/ai/files/harness/AGENTS.md`.
Task 5 must not begin until they are readable and the implementer has followed them; plan approval does not grant sandbox access.
This known access gate does not change the settled registry design.
- **Deferred, catalog owner:** the live bundle registry's existing unsupported groups block whole-catalog checks and reconciliation.
Resolution is outside this change; per-registry migration validation and isolated tests remain possible, but no bypass or cleanup is authorized.
- **Rollout risk, operator:** the installed Kura currently reports 0.7.0 and old parsing ignores the new flag.
Do not infer support from a version string alone; activate the tested implementation and prevent old automation from running across the cutover.
- **Existing behavior difference, implementer:** `scope.global_set` and native global planning currently differ on global-agent dependencies.
Preserve existing algorithms rather than silently fixing that separate discrepancy during this field migration.
- **Design decisions:** no unresolved blocking branches remain.
The explicitly named execution prerequisites and deferred bundle issue must stay visible in implementation reporting.

## Approval

The user explicitly approved this plan on 2026-10-06, including the agreed registry contract, task sequence, catalog migration scope, and dotfiles-access prerequisite.
Implementation requires a separate explicit request; this approval authorizes no code, catalog, configuration, or native-view changes.
