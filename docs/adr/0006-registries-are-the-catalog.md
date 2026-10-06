# Registries are the catalog

A skill, agent or bundle exists for Kura only when `skill-registry.json`, `agent-registry.json` or `bundle-registry.json` has an entry for it.
Its source path is derived from that name as `skills/<name>/`, `agents/<name>.md` or `bundles/<name>/`, and the registry name, the directory or file name, and the frontmatter `name` must agree.
This supersedes the discovery part of [0005](0005-portable-agent-bundles.md): a bundle is now selected by its `bundle-registry.json` entry rather than by its directory name, and `bundle.json` remains the declaration of its content and `requires`.
Bundle-owned agents and skills are covered by registering the bundle and never get registry entries of their own.
Discovering sources from the filesystem made two sources of truth, so a stray or half-copied file could become installable and `list`, `add` and `doctor` could disagree about what the catalog held.
It also needed a registry-free special case with its own defaults (project-scoped, no groups, dependencies, global policy or upstream), which no longer exists because every artifact is registered.
The cost is that each new artifact needs a registry row, and a catalog without registries holds nothing rather than refusing.
`sync`'s existing guard against a non-empty derived set is what keeps a missing or emptied registry from pruning global links.
A source on disk with no registry entry is ignored by every command, and `doctor` reports each one as an `unregistered` note so a forgotten row is noticed without changing the exit code.
Bundle registry rows refuse `groups`, `dependencies` and `dependency_only` until global bundles are designed.
