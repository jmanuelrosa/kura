# Releasing

There is no `--version` flag. A release is identified by its tag for a human and by
the SHA-256 checksum of its asset for a machine, and whatever installs the command
pins both.

1. `make test`. The suite reads only `tests/fixtures/catalog/`, so it needs no
   machine state and no network.
2. `make checksum`. This builds `dist/kura` and prints its checksum. Run it
   twice: the build is deterministic, so two runs of the same source must print the
   same digest. If they differ, the asset has stopped being identifiable and the
   cause is a bug in `build.py`, not something to work around.
3. Verify the asset in isolation, since the point of it is that it needs nothing
   beside it:

   ```sh
   tmp=$(mktemp -d) && mkdir -p "$tmp/home/.config/kura" "$tmp/empty-home" && cp dist/kura "$tmp/"
   ln -s "$PWD/tests/fixtures/catalog" "$tmp/home/.config/kura/catalog"
   env -i PATH=/usr/bin:/bin HOME="$tmp/home" "$tmp/kura" list --type skill
   env -i PATH=/usr/bin:/bin HOME="$tmp/empty-home" "$tmp/kura" list --type skill
   ```

   The first must print the skill listing.
   The second must refuse and name `~/.config/kura/catalog`, because a machine with no catalog has nothing to manage and silence there would look like an empty catalog.
4. After approval, create and push a `v<major>.<minor>.<patch>` tag. The release
   workflow validates the tag, repeats the tests and deterministic build, smoke-tests
   the asset, and publishes `kura` and `kura.sha256` to the corresponding GitHub
   Release with generated notes. Do not reuse or move a published release tag.
5. Update the installer that pins it: the release tag and the checksum change
   together, and rolling back is the same edit in reverse.

Rollback restores the command, not user state. `~/.claude`, a project's `.claude/`
and its `kura.json` are all left as they were, so a release that changed the
shape of anything it writes needs its own note here.
