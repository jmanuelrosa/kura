# Use portable agent bundles instead of Claude Code plugins

Kura now treats a bundle as catalog content, not as a Claude Code plugin or a Pi extension.
A bundle lives under `~/.config/kura/catalog/bundles/<name>/`, is selected by its directory name, and is marked by `bundle.json`.
An empty marker such as `bundles/backend/bundle.json` containing `{}` is valid when the bundle owns at least one agent and one skill.
Owned agents and skills stay beside that marker, while reusable root agents and skills stay in the root stores and may be named as requirements.

The extended project manifest schema is version 2 and records direct `skills`, `agents`, and `bundles` arrays.
Version 1 manifests remain readable as skill-only intent and are upgraded when agent or bundle intent is written after a successful transaction.
Legacy plugin rows remain inert.
`--type plugin` continues to parse so users get migration guidance, but plugin operations do not install or remove portable bundles.

Claude Code receives agent links in its native agent directories.
Pi receives agent links only in configured `pi.agents.global` and `pi.agents.project` directories.
Those Pi paths are for a Markdown-compatible subagent extension to read.
Kura does not install the extension, prove that it is enabled, or translate Claude-specific plugin behavior.

Global `sync` now reconciles registry-global skills and standalone registry-global agents, including skill dependencies required by those agents.
Project add and remove use explicit `--type skill`, `--type agent`, or `--type bundle`.
Typed non-skill operations that are not implemented refuse instead of claiming partial support.

The backend bundle layout is the pilot shape, but the backend dotfiles pilot is not deployed yet.
Operators must perform a manual handoff from any old dotfiles or plugin-managed agent links before asking Kura to own the native view.
