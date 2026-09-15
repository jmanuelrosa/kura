"""Locating Kura's fixed catalog and the user's home directory."""

import os
from pathlib import Path

from . import config


def catalog_root():
    """Return ~/.config/kura/catalog, refusing when it is absent."""
    try:
        return config.effective_catalog(home())
    except config.Malformed as exc:
        raise SystemExit(str(exc)) from exc


def claude_dir(root=None):
    """Return an explicit catalog root or Kura's fixed catalog."""
    return root or catalog_root()


def home():
    """Read HOME through the environment rather than Path.home()."""
    return Path(os.environ.get("HOME") or Path.home())
