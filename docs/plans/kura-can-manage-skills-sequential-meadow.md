# Seat bundles on Claude, Pi and Codex

## Context

The 16 seat plugins (`backend` and its 14 template siblings, plus `product-team`) live in dotfiles at `roles/ai/files/harness/plugins/` and are hand-linked into projects as Claude skills-dir plugins.
Nothing provisions them and Pi and Codex never see them.
Kura already has the container: ADR 0005 portable bundles (`catalog/bundles/<name>/bundle.json`), installed as plain symlinks into Claude and Pi native views, with backend named as the unshipped pilot.
The goal is to move the seats into the catalog as bundles with no new mechanism in kura.

## Decisions

1. **Name:** the unit is a **bundle**. "Seat" stays dotfiles vocabulary. "Plugin" survives only in the legacy refusal.
2. **Location:** `roles/ai/files/harness/kura/catalog/bundles/<name>/` in dotfiles. The kura repo stays the tool.
3. **Bundles stay project-only.**
4. **`requires.skills`** holds only skills the seat always needs; stack skills stay prose-routed with the existing gap report.
5. **Plain symlinks, as ADR 0005 ships them.** Edits to a bundle are live in every harness, the same as standalone agents and skills.
6. **No `model` in bundle agents.** One agent file cannot carry a model id that Claude and Pi both accept, so seat agents inherit the session's model. Harness-specific keys that do not collide (Claude `effort`, Pi `thinking`) may stay side by side.
7. **Codex gets bundle skills only**, through the `.agents/skills` view Pi already uses. Codex agents (TOML) are out of scope.

Rejected: rendering agents per harness into a kura-owned store. It fixed per-harness models and Codex agents, but a rendered file is not live, so an edit to a bundle did not reach any harness until kura ran again.

## Kura changes

None beyond `CONTEXT.md` naming Codex as a harness.

## Dotfiles changes

- Move `harness/plugins/<seat>/` to `harness/kura/catalog/bundles/<seat>/`, replacing `.claude-plugin/plugin.json` with `bundle.json` (`requires.skills` from `skillDependencies`).
- Remove `model:` from the seat agents.
- Rewrite agent prose: bare skill names (no `backend:` prefix), no `.claude/` paths, "the repo's agent instructions (`AGENTS.md`, or `CLAUDE.md` where present)". Fix `nestjs` (missing) and `fastify` (catalog has `fastify-best-practices`).
- Remove the "seat plugins remain outside its managed catalog" carve-out in `roles/ai/tasks/main.yml`, the plugins paths in the sandbox allowlists, and the stale `kura add <seat> --type plugin` line in `adapters/claude/README.md`.
- Order: backend pilot, then the 13 template seats, then `design` and `security`, then `product-team` after its audit.

## Verification

Pilot in a scratch project: `kura init` with harnesses `claude, pi`, `kura add --type bundle backend`, then confirm the agent appears in Claude's `/agents` and Pi's subagent list, that an edit to the catalog agent is visible without rerunning kura, that Claude ignores `thinking` and Pi ignores `effort`, and that Codex lists the bundle's skills.
