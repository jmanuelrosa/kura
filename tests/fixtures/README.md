# The fixture catalog

`catalog/` is a committed test catalog.
Every fixture skill is named in `skill-registry.json`, which is what makes `skills/<name>/SKILL.md` part of the catalog; a skill directory without a registry entry is ignored.
`tests/conftest.py` places it at `~/.config/kura/catalog` under an isolated temporary home, while lifecycle tests that mutate catalog data create their own fixed-path catalogs.

Its `skill-registry.json` names [the registry schema](../../docs/schemas/skill-registry.schema.json) relatively, so the committed example is validated while it is edited.
Kura ignores the key, and the fixture is the worked example the schema is checked against.

The retained `plugins/` fixture describes legacy state that migration preserves without managing.
The `agents/` and `agent-registry.json` fixtures are active agent catalog inputs for bundle and agent-view tests.
Fixture agents deliberately omit the `global` flag so legacy skill tests do not accidentally grow global agent policy.
Fixture global skill roots use `global: true`, and their groups remain descriptive.

## Skill cases

| Property | Held by |
|---|---|
| Project-scoped metadata skill | `coderabbit` |
| Explicit registry-global skill root | `commit` (`global: true`) |
| Recursive global dependency | `grill-me` and `grill-with-docs` require `grilling` |
| Dependency-only skills | `grilling`, `domain-modeling` |
| Project dependency closure | `spec-driven-development` |
| Metadata group with a space | `prompt engineering` on `idea-refine` |
| Framework groups | `react` and `astro` skills |
| Upstream-backed skill | `brainstorming` |

Every fixture skill carries the minimum valid frontmatter.
PyYAML remains a test-only oracle for the stdlib frontmatter scanner.
`update` and `outdated` stub network fetching and construct upstream archives in `tmp_path`.
