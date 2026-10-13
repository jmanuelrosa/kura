# Seat bundles on Claude, Pi and Codex

## Context

The 16 seat plugins (`backend` and its 14 template siblings, plus `product-team`) live in dotfiles at `roles/ai/files/harness/plugins/` and are hand-linked into projects as Claude skills-dir plugins.
Nothing provisions them, Pi and Codex never see them, and their agent frontmatter is written for one harness (the backend seat's `model: openai-codex/gpt-5.6-sol` is a Pi id Claude cannot use).
Kura already has the container: ADR 0005 portable bundles (`catalog/bundles/<name>/bundle.json`), installed as symlinks into Claude and Pi native views, with backend named as the unshipped pilot.
The goal is to move the seats into the catalog as bundles and make their agents correct on Claude, Pi and Codex, with the smallest mechanism that does it.

## Decisions

1. **Name:** the unit is a **bundle** (already in `CONTEXT.md` and ADR 0005). "Seat" stays dotfiles vocabulary. "Plugin" survives only in the legacy refusal.
2. **Location:** bundles live in the dotfiles catalog, `roles/ai/files/harness/kura/catalog/bundles/<name>/`. The kura repo stays the tool.
3. **Bundles stay project-only.**
4. **`requires.skills`** holds only skills the seat always needs; stack skills stay prose-routed with the existing gap report.
5. **Agents are rendered, then linked.** Kura renders each agent per harness into a fixed kura-owned store, `~/.config/kura/rendered/<harness>/agents/`, and native views are symlinks into that store. `sync`'s three narrowings hold unchanged, the store just joins the set of kura's own stores. Skills stay direct catalog links. Every agent goes through this path, root agents included.
6. **Source vs overrides:** agent frontmatter keeps only portable fields (`name`, `description`, `skills`) plus the body. Everything harness-native (`model`, `effort`, `thinking`, `memory`, `tools`, `sandbox_mode`) goes in a per-harness JSON block, copied verbatim into that harness's output: `bundle.json` for bundle-owned agents, `agent-registry.json` for root agents. No translation tables.
7. **Codex:** a `codex` profile with skills at `.agents/skills` / `~/.agents/skills` (shared with Pi, so the planner dedupes identical view paths) and agents as TOML at `.codex/agents` / `~/.codex/agents`. The body becomes `developer_instructions`. `skills` is not emitted for Codex; the skills themselves are still installed where Codex finds them.
8. **Freshness:** every mutating command (`sync`, `converge`, `add`, `remove`) re-renders deterministically. No hashes, headers, watchers or `doctor` staleness check.
9. **Existing agent links** into `catalog/agents` are already provably kura's, so `sync` and `converge` repoint them at the store.

Trimmed for simplicity against the round-3 recommendations: no neutral tool vocabulary (tools live in the per-harness block, only `security` and `product-team` declare them), no derived Codex `sandbox_mode`, no source-hash header or `doctor` check.

## Kura changes

- `kura/harnesses.py`: add the `codex` profile, with an agent dialect per profile (`md` for Claude and Pi, `toml` for Codex).
- New `kura/render.py`: pure `render(agent, harness, overrides) -> text`, flat Markdown frontmatter for Claude and Pi, hand-written TOML for Codex (stdlib only, multiline string escaping for the body), plus writing the store. A new module is justified because rendering is its own concern; everything else edits existing modules.
- `kura/paths.py`: the fixed render root beside `catalog_root`.
- `kura/catalog.py`: read per-harness agent blocks from `bundle.json` (`agents.<name>.<harness>`) and `agent-registry.json`; refuse an unknown harness key.
- `kura/views.py`: agent link targets point into the render store; dedupe view paths shared by two selected harnesses; render before linking in the project and global planners.
- `kura/scope.py` (store classification around lines 151, 230, 253): recognise the render store as a kura store, and keep recognising `catalog/agents` links so they can be repointed.
- `kura/config.py`: accept `codex` in `globalHarnesses` and in project `harnesses`.
- Docs: `README.md` (codex harness, render store; `test_packaging.py` enforces it), `ARCHITECTURE.md` harness profiles and desired-state sections, `CONTEXT.md` (new term **Rendered agent**; widen **Managed link** and **Native view**), and `docs/adr/0006-render-agents-per-harness.md` superseding ADR 0005's translation non-goal.
- Bump `kura/__version__.py` at release time per `RELEASING.md`.

## Dotfiles changes (separate PR)

- Move `harness/plugins/<seat>/` to `harness/kura/catalog/bundles/<seat>/`, replacing `.claude-plugin/plugin.json` with `bundle.json` (`requires.skills` from `skillDependencies`, plus the per-harness agent blocks lifted out of the agent frontmatter).
- Rewrite agent prose: bare skill names, no `.claude/` paths, "the repo's agent instructions (`AGENTS.md`, or `CLAUDE.md` where present)". Fix `nestjs` (missing) and `fastify` (catalog has `fastify-best-practices`).
- Remove the "seat plugins remain outside its managed catalog" carve-out in `roles/ai/tasks/main.yml`, the plugins paths in the sandbox allowlists, and the stale `kura add <seat> --type plugin` line in `adapters/claude/README.md`.
- Order: backend pilot, then the 13 template seats, then `design` and `security`, then `product-team` after its audit.

## Verification

- `make test`; new `tests/test_render.py` over literal dicts for each dialect, and `tmp_path` tests that a bundle with overrides produces store files and links per harness, that Pi plus Codex yield one skill link, and that an old `catalog/agents` link is repointed. Add the fixture bundle to `tests/fixtures/catalog/` and record it in `tests/fixtures/README.md`.
- `make checksum` twice to confirm the build is still deterministic.
- Pilot: in a scratch project, `kura init` with harnesses `claude, pi, codex`, `kura add --type bundle backend`, then confirm the agent appears in Claude's `/agents`, in Pi's subagent list, and in Codex's agents, and that each picks the configured model. Confirm Codex follows a symlinked agent TOML; if it does not, that is the one fact that would change decision 5 for Codex.
