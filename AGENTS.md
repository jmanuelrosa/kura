# kura

A stdlib-only Python CLI that manages coding-agent artifacts from one catalog: Claude Code installs, plus Pi-compatible skill and agent views.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing behaviour: it records why each rule is the way it is, and most of them exist because the obvious alternative failed once.
[README.md](README.md) is the user-facing reference and is under test.

## Layout

| Path | What |
|---|---|
| `bin/kura` | Development entry point, a thin shim |
| `kura/` | The package: all logic |
| `kura/commands/` | One module per command, each exposing `run(args)` |
| `tests/` | The suite, beside the package rather than inside it |
| `tests/fixtures/catalog/` | The catalog every test reads, and [its own README](tests/fixtures/README.md) |
| `build.py` | Builds the single-file release asset |
| `RELEASING.md` | The release checklist |

The shim lives in `bin/` because the command and the package want the same name and one directory cannot hold both.
It puts its parent's parent on `sys.path`, so moving either without the other breaks the import.

## Working here

```sh
make test                      # the whole suite
uv run --offline --with pytest --with pyyaml pytest -q tests/test_add.py
./bin/kura list --type skill   # against ~/.config/kura/catalog
make checksum                  # build, then print the asset's digest
```

A command's own tests are the fastest way to see its shape.
Start from `tests/test_<command>.py` rather than from the module.

## Invariants

Breaking one of these is a defect even when the suite still passes.

- **Stdlib-only at runtime, no exemption.** `kura/ui.py` and `kura/colors.py` exist so there is no third-party import to reach for. PyYAML is a test dependency and the oracle for the frontmatter scanner, never its implementation.
- **The logic stays in the package.** The shim is wiring. An extensionless executable cannot be imported, so anything that grows there is code the fast tests can only reach through a subprocess.
- **The catalog is fixed, never discovered or configured.** `paths.catalog_root` reads `~/.config/kura/catalog` and refuses if it is not a directory. It does not consult `XDG_CONFIG_HOME`, machine configuration, environment overrides, an upward search, or a marker file.
- **A refusal beats a plausible empty answer.** An unresolvable catalog exits rather than reading empty registries, because an empty derived set is indistinguishable from a catalog that lost its tags.
- **`sync` is the only command that deletes what nobody named.** Its three narrowings are what make that safe: symlinks only, links resolving into the catalog's own stores only, and only when the derived set is non-empty. Weakening any of them turns a retagged registry into a silently emptied `~/.claude`.
- **`remove` never leaves the project it starts in.** Cross-scope cascade would need a machine-wide index that goes stale the moment a checkout moves.
- **`--type skill|agent|plugin` is explicit wherever a type is meaningful.** Nothing is inferred from a name, so the three namespaces may overlap.
- **The `, 0 changes` marker in `sync`'s and `converge`'s closing summary is a contract**, matched by whatever provisions a machine. Rewording it makes every provisioning run report a change.
- **No `--version` flag.** A release is identified by its tag and its asset checksum.
- **The build is deterministic.** Two builds of one tree must produce the same digest, or the checksum stops identifying the source.

## Tests

- Three altitudes, in the order you should prefer them: pure functions over literal dicts, `tmp_path` for real symlinks, and a handful of subprocess runs through the shim.
- `HOME` is the catalog and native-view seam. An autouse fixture places `tests/fixtures/catalog` at the fixed catalog path under a temporary home, so no test reads machine state.
- **Nothing imports `conftest`, and `tests/` has no `__init__.py`.** Both guard the same silent failure: a non-package `conftest.py` is named literally `conftest`, so `from conftest import X` binds to whichever suite loaded last. Shared helpers live in `tests/kit_helpers.py`, a name no other directory can claim; fixtures stay in `conftest.py`.
- Adding a case to the fixture catalog means recording what it holds in `tests/fixtures/README.md`. A module's `_fixtures_still_valid` guard is what fails when someone invalidates a choice, and it should stay that way.
- Adding a command means adding it to `COMMANDS`, `MODULE`, `FAMILIES` and `SCOPE` in `cli.py`; `test_help.py` fails if it reaches the CLI without reaching the grouped help listing.
- Adding a command, flag or exit code means documenting it in `README.md`; `test_packaging.py` asserts every one appears there in backticks.

## Style

- Default to zero comments. A comment earns its place by explaining a non-obvious **why**, never a **what**. The same bar applies to docstrings, which is why the ones here are long and few.
- Match the surrounding code. Prefer editing an existing module over adding one.
- Never use em or en dashes. A hyphen, comma, colon or parentheses instead.
- Do not hard-wrap prose. One sentence per line, and let the editor wrap.
- Refusal wording is not asserted by tests on purpose, so a message can be improved without touching the suite. Exit codes and tiers are asserted, so those are the contract.
- No hardcoded paths, names or values that could be derived. `catalog.LEAF` and `catalog.STORE` exist so no module has to know where a type lives.
