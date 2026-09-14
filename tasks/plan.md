# Implementation Plan: Multi-harness skill management

## Overview

Implement the accepted multi-harness skill model in dependency order, replacing Claude-owned project projection with one declarative project manifest and independent native Claude Code and Pi links.

## Architecture Decisions

- Filesystem skills are authoritative and optional registry rows add metadata.
- Root `kura.json` stores selected harnesses and direct skill intent only.
- Shared planners preflight every harness path before a transaction writes anything.
- The accepted declarative model takes precedence over incompatible legacy behavior.
- `converge` accepts optional skill-only `--type`; `remove --no-cascade` remains a deprecated compatibility no-op.

## Task List

### Phase 1: Foundation

- [x] Add strict machine configuration and built-in harness profiles.
- [x] Replace registry-authoritative catalog loading with filesystem-first skill loading and deterministic closures.
- [x] Replace provenance state with the versioned root manifest and legacy migration reader.
- [x] Add reusable native-view planning, preflight, transaction, and rollback primitives.

### Checkpoint: Foundation

- [x] Focused configuration, catalog, state, path, and transaction tests pass.

### Phase 2: Lifecycle

- [x] Implement `init`, instruction scaffolding, legacy migration, and old Pi topology conversion.
- [x] Implement `config`, catalog moves, global harness replacement, and global reconciliation.
- [x] Rework project `add`, `remove`, `restore`, and `converge` around direct intent.
- [x] Rework `sync` and scratch global operations across globally enabled harnesses.
- [x] Rework `adopt`, project scanning, and metadata-backed scouting.

### Checkpoint: Lifecycle

- [x] Project and global filesystem tests pass, including collisions, dry runs, and rollback.

### Phase 3: Diagnostics and integration

- [x] Add per-harness list state while preserving existing JSON fields.
- [x] Rework doctor findings for configuration, manifests, native views, instructions, executables, trust, and legacy state.
- [x] Generalize trust to Claude Code and Pi with Pi-compatible locking and rollback.
- [x] Wire the final CLI surface and skills-only type contract.
- [x] Rewrite README and ARCHITECTURE for the implemented model.

### Checkpoint: Complete

- [x] `make test` passes.
- [x] Two clean builds have identical checksums.
- [x] Runtime imports remain stdlib-only.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| A collision in one harness causes a partial mutation | High | Build and validate the complete cross-harness plan before applying it. |
| A later filesystem failure leaves mixed state | High | Snapshot changed links and file bytes, then roll back in reverse order. |
| Catalog moves make omitted project links foreign | High | Scope old-catalog ownership to the config transaction and print the mandatory warning. |
| Empty global metadata prunes valid state | High | Preserve the existing empty-desired pruning guard across every harness. |
| Legacy state is lost during migration | High | Preserve unsupported agent and plugin rows under `legacy` and remove the old manifest last. |

## Open Questions

No blocking questions remain.
The user chose the accepted declarative model over incompatible legacy semantics.
