# Separate bundle groups from install policy

This supersedes the bundle group restriction in [0006](0006-registries-are-the-catalog.md).
Bundle registry rows now accept descriptive `groups` in both local and upstream entries.
Groups select catalog names, not installation scope, so they do not depend on designing global bundles.
The retired `global` tag remains refused across all three registry types.

`list --type bundle --group TAG` filters by exact tag, and bare `--group` groups the human report.
`add --type bundle --group TAG` records every member as direct project intent and installs its full closure in one transaction.
`remove --type bundle --group TAG` removes only directly configured members and retains requirements still needed by other direct intent.
An unknown group refuses; removing a known group with no configured members changes nothing.
Group tags are not copied to bundle-owned agents or skills, and standalone agent CLI group selection remains unsupported.

Bundles remain project-only, and their registry rows still refuse `global`, `dependencies`, and `dependency_only`.
Explicit requirements remain in `bundle.json`.
