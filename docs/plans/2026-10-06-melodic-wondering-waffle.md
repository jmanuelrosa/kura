# Registry-first catalog

## Context

`kura list --type agent` shows bundle-owned agents such as `backend-staff-engineer`, which cannot be selected as agents.
The root cause is that the catalog is filesystem-first: `kura/catalog.py` discovers every `skills/*/SKILL.md`, `agents/*.md` and `bundles/*/bundle.json`, then merges optional registry metadata on top, and registry-free artifacts get special project-scoped defaults.

The decision is to make the registries the catalog's source of truth.
An artifact exists for kura only if its registry has an entry: `skill-registry.json`, `agent-registry.json`, and a new `bundle-registry.json` with the same `upstream`/`local` shape.
`bundle.json` stays as the bundle's own content declaration (its `requires`), the same split as a registry row versus a `SKILL.md`.
Bundle-owned agents and skills are never registry entries; registering the bundle covers them, and they appear only under `--type bundle`.

Decisions taken with the user:
- A file on disk with no registry entry is ignored by every command, but `kura doctor` reports it as an unregistered warning (not an error).
- A missing registry file means no artifacts of that type. No refusal; sync's existing non-empty guard still blocks mass pruning.
- Bundle registry entries carry `name`, `note`, `updated_at` and the upstream fields only. `groups`, `dependencies` and `dependency_only` are refused for bundles until global bundles are designed.

The user's dotfiles catalog was checked: all 83 skills and 4 agents are registered, nothing registered is missing, and the 17 bundles need `bundle-registry.json` entries.

## Changes

### `kura/catalog.py`

- Add `BUNDLE` to `REGISTRY_FILE` (`bundle-registry.json`), `COLLECTION` (`bundles`), `STORE` and `SUFFIX` (directory, `""`), so `_from_registry` and `registry_entries` serve all three types unchanged.
- `_from_registry`: validate per kind. For `BUNDLE`, refuse `groups`, `dependencies` and `dependency_only` keys. Fix the "registry skill" label to name the actual kind.
- `build_catalog` becomes registry-driven:
  - Skills: start from `_from_registry(claude, SKILL)`; apply `_containment_error` to entries whose source exists. Delete the directory scan, the frontmatter-name fallback and the registry-free `Artifact(..., metadata=False)` branch. The name-mismatch check already lives in `_from_registry` (runs when the source exists).
  - Agents: `_root_agents` iterates registered agents only, keeping the existing description and name checks for sources that exist. Delete the registry-free branch.
  - Bundles: `_from_bundles` iterates registered bundles only. A registered bundle with no directory yields a `Bundle` whose `source` does not exist, so `checks.py`, `add._validate_bundle` and `listing._bundle_rows` report it through their existing `source.is_dir()` paths. Owned members and `bundle.json` parsing (`_bundle_requires`, `_owned_skill`, `_owned_agent`) are unchanged.
- Remove `Artifact.metadata`; every root artifact is registered now. Update the gates that read it: `kura/scope.py:27`, `kura/views.py:358,367`, `kura/commands/listing.py:109`, `kura/commands/scout.py:98`, `global_resolution` in `catalog.py`, and the "metadata-backed" help text in `kura/cli.py:43,147`.
- Add `unregistered(claude)`: returns `(kind, path)` for each `skills/*/SKILL.md`, `agents/*.md` and `bundles/*/bundle.json` on disk with no registry entry. This is the only scan of the stores left, and only doctor calls it.
- Update the module and `build_catalog` docstrings.

### Commands

- `kura/commands/listing.py`: delete the bundle-owned agent loop in `_agent_rows` (lines 128-137). `--type agent` lists registered root agents plus `MISSING` rows for declared-but-unknown agents.
- `kura/commands/doctor.py`: emit one warning-level finding per `cat.unregistered(...)` entry, following the existing finding shape in `kura/checks.py`. Must not change doctor's exit code on its own.
- `kura/commands/pull.py`: no behaviour change. It already refuses non-skill types; confirm the refusal covers `--type bundle` wording if it enumerates types.

### Tests

- Add a shared helper to `tests/kit_helpers.py`, `register(root, kind, name, **fields)`: read-modify-write the kind's registry file, appending a `local` entry (creating the file with `{"upstream": {}, "local": []}` if absent).
- Route the duplicated writers through it so each writes its source and its registry entry together: `_write_skill`, `_write_agent`, `_write_bundle` in `test_agent_listing.py`, `test_agent_commands.py`, `test_agent_lifecycle.py`, `test_agent_views.py`, `test_bundles_catalog.py`, `test_agent_doctor.py`, and the unregistered cases in `test_global_agents.py`, `test_config.py`, `test_init.py`, `test_multi_harness.py:426`.
- Where a test exists to model "not in the catalog" (for example `test_agent_commands.py:173`, which deletes `bundle.json`, and `test_init.py:203`'s `rogue`), keep its meaning explicit: decide per test whether it means "registered but absent" or "unregistered", and write it that way.
- Replace `test_multi_harness.py::test_registry_free_skill_is_discovered` with `test_unregistered_skill_is_ignored`.
- Replace `test_agent_listing.py::test_list_type_agent_shows_root_and_bundle_owned_agent_views` with a test asserting `--type agent` omits bundle-owned agents while `--type bundle` still reports their member views.
- New cases in `test_bundles_catalog.py`: an unregistered bundle directory is absent from the catalog; a registered bundle with no directory is reported missing by `list --type bundle` and `doctor`; a bundle entry with `groups` makes the catalog invalid.
- New case in `test_agent_doctor.py`: unregistered skill, agent and bundle each produce a warning, and doctor's exit code is unaffected.
- The fixture catalog already registers everything; only `tests/fixtures/README.md:4` changes wording.

### Docs

- New `docs/adr/0006-registries-are-the-catalog.md`, in the 0004 shape: registries are the source of truth for skills, agents and bundles; supersedes the discovery part of [0005](../adr/0005-portable-agent-bundles.md) (the `{}`-marker-alone sentence). 0005 is not edited.
- `ARCHITECTURE.md`: rewrite "Filesystem-first catalog" (lines 72-84) as "Registry-first catalog", and fix lines 12, 29, 80, 87, 90-91, 116.
- `README.md`: lines 83, 106, 117-124, the schema section 136-148 (mention `agent-registry.json` and `bundle-registry.json`), 233 (agent listing omits bundle-owned agents), 336, and doctor's unregistered warning.
- `AGENTS.md`: add an invariant, "The registries are the catalog. A file nobody registered is invisible to every command except doctor's warning."
- `CONTEXT.md:27-29` ("Registry metadata: optional..."), `docs/design/portable-agent-bundles.md` (56, 70, 83-84, 152), `docs/specs/multi-harness-skills.md` (122-138, 426, 441, 457), and the description in `docs/schemas/skill-registry.schema.json`.

### Rollout to the dotfiles catalog

The dotfiles catalog must gain `bundle-registry.json` with 17 `local` entries before the upgraded kura runs there.
Otherwise every bundle disappears and projects that select one see it as missing.
This lives in the dotfiles repo, outside this sandbox, so the plan produces the file content and the user adds it.

## Verification

- `make test` passes.
- Against the real catalog after adding `bundle-registry.json`:
  - `./bin/kura list --type agent` shows only the 4 root agents, no `backend-staff-engineer`.
  - `./bin/kura list --type bundle` shows the 17 bundles.
  - `./bin/kura doctor` reports no unregistered warnings.
- Temporarily drop a stray `agents/scratch.md` into a test catalog under `tmp_path` and confirm `list` ignores it and `doctor` warns.
- `make checksum` twice gives the same digest.
