# Deprecation lives on the registry row

Any skill, agent or bundle registry row, local or upstream, may set `deprecated: true`, a boolean beside `global` and `dependency_only`.
The marker belongs on the row because the row is the one place that covers every artifact and survives every command: `update` overwrites an upstream skill's files, so a frontmatter marker would be lost on the next sync, and the registries are already what decides that an artifact exists ([0006](0006-registries-are-the-catalog.md)).
A structured object naming the date, reason and replacements was rejected in favour of the plain flag; a row's free-form `note` can carry that context.
Deprecation is advice, not policy.
A deprecated artifact stays visible to `list`, addable by `add`, and provisioned by `sync` when it is `global: true`; removing global policy remains the job of the `global` field, so `sync` never prunes a link because of deprecation.
`list` marks the row, `add` and `sync` warn on stderr so `sync`'s `, 0 changes` stdout contract is untouched, and `doctor` reports a deprecated artifact still in project or global views as a note that does not change the exit code.
Hiding deprecated rows, refusing `add` without `--force`, or returning `DRIFT` were rejected because each turns a catalog edit into failures in projects that have not migrated yet.
Kura does not rewrite installed descriptions: native views are symlinks into the catalog, so an installed copy with an edited description would no longer be a view.
A catalog that wants the agent itself to see the deprecation keeps a prefix in the artifact's own description.
A non-boolean value refuses the catalog, as a malformed `global` or `dependency_only` does.
