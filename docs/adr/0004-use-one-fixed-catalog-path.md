# Use one fixed catalog path

Kura reads its only catalog from `~/.config/kura/catalog`.
The path is not stored in machine configuration and cannot be changed by a flag, `KURA_CATALOG`, `XDG_CONFIG_HOME`, or a fallback rule.
This supersedes [0003](0003-do-not-retain-catalog-history.md), which assumed users could select and move catalogs.
Supporting configurable catalogs required precedence rules, first-run catalog selection, catalog-move transactions, temporary ownership of old links, project scanning, and recovery behavior that the current product does not need.
A fixed path removes those behaviors while preserving `$HOME` as the test seam.
Older configuration files remain readable, but their `catalog` field has no effect and is removed when Kura rewrites the file.
Links to a previous catalog location are foreign and require manual cleanup before Kura can recreate them from the fixed catalog.
Multiple or configurable catalogs can be reconsidered if a concrete use case justifies restoring that complexity.
