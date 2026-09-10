# The fixture catalog

`catalog/` is a small artifact catalog in the layout `kura.catalog.build_catalog`
reads: two registries, a `skills/` and `agents/` store, and `plugins/<name>/.claude-plugin/plugin.json`
manifests. `tests/conftest.py` exports it as `KURA_CATALOG` for every test.

The suite used to read the catalog of the dotfiles repository this tool was extracted
from, which meant a registry edit there could fail the application's tests, and meant
the tests could not run without that checkout. The artifact **names** are kept from
that catalog so a case still reads concretely, but the properties those names carry
are fixed here.

## What the cases depend on

Change any of these and something fails, usually in a module's `_fixtures_still_valid`
guard rather than in the case that cared:

| Property | Held by |
|---|---|
| A project-scoped skill | `coderabbit` |
| A skill global by tag | `commit` |
| A skill global by derivation, untagged | `planning-and-task-breakdown`, `domain-modeling`, `documentation-and-adrs`, `grilling` |
| A global agent whose dependencies expand two levels | `architect` names `planning-and-task-breakdown` and `domain-modeling`; `domain-modeling` names `documentation-and-adrs` |
| Dependency-only skills | `grilling`, `domain-modeling` |
| A parent straddling both scopes | `spec-driven-development`: one global dependency, three project ones |
| A tag straddling both scopes | `planning` |
| A tag whose whole membership is global | `architecture`, on agents |
| A tag with a space in it | `prompt engineering`, on `idea-refine` |
| A project-scoped plugin shipping an agent | `backend` |
| A plugin declaring a skill through `skillDependencies` | `product-team` names `idea-refine` |
| Seat boilerplate: a tag most plugins carry, which must earn nothing | `observability` |
| Tech tags a project can be fingerprinted for | `react`, `astro` |
| A topic tag with no tech tag beside it | `testing`, on `test-driven-development` |
| A repo-tracked skill with an upstream | `brainstorming`, under `fixture-org/fixture-skills` |

`update` and `outdated` never reach the network in the suite: `upstream.fetch` is
stubbed and the tarballs are built in `tmp_path`, so no fixture here describes an
upstream payload.

Nothing in here is a real skill. Every `SKILL.md` and agent file carries the minimum
frontmatter the scanner accepts, which is what the fixture sweep in
`test_frontmatter.py` guards. The evidence that the scanner does not raise false
problems is a real catalog's hand-written corpus, and that sweep belongs to whoever
owns the catalog.
