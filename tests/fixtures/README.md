# The fixture catalog

`catalog/` is a committed test catalog.
The multi-harness implementation discovers skills from `skills/<name>/SKILL.md` and merges optional `skill-registry.json` metadata.
`tests/conftest.py` places it at `~/.config/kura/catalog` under an isolated temporary home, while lifecycle tests that mutate catalog data create their own fixed-path catalogs.

Its `skill-registry.json` names [the registry schema](../../docs/schemas/skill-registry.schema.json) relatively, so the committed example is validated while it is edited.
Kura ignores the key, and the fixture is the worked example the schema is checked against.

The retained `agents/`, `plugins/`, and `agent-registry.json` fixtures describe the legacy state that migration preserves without managing.
They are no longer catalog inputs for the skills-only phase.

## Skill cases

| Property | Held by |
|---|---|
| Project-scoped metadata skill | `coderabbit` |
| Registry-global skill | `commit` |
| Recursive global dependency | `grill-me` and `grill-with-docs` require `grilling` |
| Dependency-only skills | `grilling`, `domain-modeling` |
| Project dependency closure | `spec-driven-development` |
| Metadata group with a space | `prompt engineering` on `idea-refine` |
| Framework groups | `react` and `astro` skills |
| Upstream-backed skill | `brainstorming` |

Every fixture skill carries the minimum valid frontmatter.
PyYAML remains a test-only oracle for the stdlib frontmatter scanner.
`update` and `outdated` stub network fetching and construct upstream archives in `tmp_path`.
