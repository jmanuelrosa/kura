"""Locating the two roots the tool reads.

Two unrelated notions of "root" live here and conflating them is a real bug
source:

  catalog_root()  the artifact catalog, where skills, agents and plugins are kept
  project_root()  the user's cwd, where they get linked

project_root lives in scope.py, next to the rules that use it. This module only
finds the catalog.

The catalog is injected, never discovered. An earlier version walked up from this
file looking for a `dotfiles.yml` marker, which worked only because the command on
PATH was a symlink back into that checkout: resolve() followed it and the walk
landed inside the repo. Installed as a release asset there is no link to follow and
no repo to find, so the catalog is named instead.
"""

import os
from pathlib import Path

ENV_CATALOG = "KURA_CATALOG"
# Under $HOME rather than absolute, so a test pointing HOME at tmp_path cannot reach
# the real catalog by accident.
DEFAULT_CATALOG = ".local/share/kura/catalog"


def catalog_root():
    """The directory holding the registries and the artifact stores.

    Two sources, in order: the environment, then one known location. No search.

    Both failures raise rather than returning a path that does not exist.
    build_catalog against a missing root reads empty registries, an empty derived
    set makes every existing link look stale, and `sync` then has to catch it
    through the DRIFT guard that exists for a registry which genuinely lost its
    `global` tags. Refusing here keeps that guard a backstop.
    """
    override = os.environ.get(ENV_CATALOG)
    if override:
        root = Path(override)
        if not root.is_dir():
            raise SystemExit(f"{ENV_CATALOG} points at {root}, which is not a directory")
        return root
    root = home() / DEFAULT_CATALOG
    if not root.is_dir():
        raise SystemExit(
            f"no artifact catalog at {root}. Link one there, or set {ENV_CATALOG}"
        )
    return root


def claude_dir(root=None):
    """Where skills, agents and plugins are stored.

    Kept as the name every command calls, and kept taking an explicit root, which is
    the seam tests use.
    """
    return root or catalog_root()


def home():
    """Read HOME through the environment rather than Path.home().

    Path.home() consults the password database on some platforms, which would
    ignore the HOME a test sets and let a run escape into the real ~/.claude. The
    default catalog path is derived from this, so it decides more than it used to.
    """
    return Path(os.environ.get("HOME") or Path.home())
