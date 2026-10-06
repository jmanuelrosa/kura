# Releasing

## Manual GitHub release

Open **Actions > Release > Run workflow**, select the repository's default branch, and enter a semver without a leading `v` in `version`.
Only repository admins may proceed: the workflow checks both the original actor and the actor requesting a re-run before the job with write permission starts.
GitHub may show the Run workflow button to other users with write access, but their runs fail the admin check without creating a commit, tag, or release.

The workflow updates `kura/__version__.py` in its checkout, runs the tests, verifies two identical builds, and smoke-tests the isolated executable with and without a catalog.
After the checks pass, it commits only the version file if it changed, pushes the version tag, and publishes `kura` and `kura.sha256` with generated notes in the same run.
The version commit belongs only to the release tag: the default branch is not pushed or changed, so its PR and signature protections remain intact.
No additional secret is needed; the workflow uses `GITHUB_TOKEN`.
Publishing happens in the same run because a tag pushed with that token does not trigger another workflow.
An existing tag is refused rather than moved or reused.
If publication fails after the tag is pushed, recover by publishing that existing tag's verified assets, rather than requesting the version again.

The workflow must be merged into the default branch before the manual trigger is available.
Only committed changes from the selected default-branch revision are released; local changes are not included.
Update the installer's pinned tag and checksum together after publication.

## Local verification and tag-triggered releases

For a manually prepared tag, set `kura/__version__.py` to the matching semver without `v`.
`kura --version` prints that string; the SHA-256 checksum identifies the exact asset bytes for installers and automation.
The existing tag-push trigger remains supported and also requires an admin actor, including on re-runs.

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
   If that GitHub Release already has both assets, publish is a no-op so a re-run does not fail or replace assets.
5. Update the installer that pins it: the release tag and the checksum change
   together, and rolling back is the same edit in reverse.

Rollback restores the command, not user state. `~/.claude`, a project's `.claude/`
and its `kura.json` are all left as they were, so a release that changed the
shape of anything it writes needs its own note here.
