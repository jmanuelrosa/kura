# Kura

Kura coordinates reusable coding-agent artifacts across the harnesses a user chooses, while preserving each harness's native view of those artifacts.

## Artifacts and sources

**Harness**:
A coding-agent application through which a user works, such as Pi or Claude Code.
_Avoid_: Agent, client, provider

**Agent**:
A reusable delegated persona that a harness may load as an artifact.
_Avoid_: Harness

**Skill**:
A portable instruction package that gives a harness an on-demand capability.
_Avoid_: Plugin, agent

**Catalog**:
A user-controlled collection that is the source of artifact content available to Kura.
_Avoid_: Installation, native view

**Registry metadata**:
Optional catalog information that adds policy and relationships to artifacts without owning their content.
_Avoid_: Catalog, manifest

**Direct skill intent**:
A project declaration that a user wants a skill independently of any other skill.
_Avoid_: Installed skill

**Dependency skill**:
A skill required by direct skill intent and derived from current registry metadata.
_Avoid_: Direct skill

## Scope and views

**Project**:
A directory explicitly initialized with its own Kura intent.
_Avoid_: Repository, workspace

**Project manifest**:
The portable declaration of a project's selected harnesses and direct artifact intent.
_Avoid_: Machine configuration, lockfile

**Selected harness**:
A harness that a project intends to make its managed artifacts available through.
_Avoid_: Default harness, installed harness

**Globally enabled harness**:
A harness that a machine intends to make global artifacts available through.
_Avoid_: Selected harness, default harness

**Native view**:
The representation of managed artifacts in a location and shape a harness discovers without additional configuration.
_Avoid_: Catalog, canonical store

**Managed link**:
A native-view link that Kura can prove belongs to its declared or derived state.
_Avoid_: Any symlink

**Global skill**:
A skill whose registry policy makes it available through every globally enabled harness rather than one project.
_Avoid_: Shared skill

**Instruction bridge**:
A minimal harness-native instruction file that refers to the project's shared instructions without duplicating them.
_Avoid_: Generated instructions

## Lifecycle

**Convergence**:
Reconciliation that makes managed native views match declared intent and current catalog policy.
_Avoid_: Installation

**Restore**:
Conservative reconciliation that recreates missing managed artifacts without deleting extras.
_Avoid_: Convergence

**Adoption**:
The act of turning eligible catalog-backed native links into declared project intent.
_Avoid_: Import, discovery

**Legacy state**:
Preserved intent for artifact types that the current Kura feature set does not manage.
_Avoid_: Drift
