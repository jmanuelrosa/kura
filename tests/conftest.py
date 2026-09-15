"""Fixtures for the kura suite.

Three altitudes, in order of how many tests should live at each:

  pure         import kura.* and call functions on literal data. No I/O.
  filesystem   `home` + `project` fixtures, real symlinks under tmp_path.
  subprocess   the `kit` fixture, running the shim end to end. A handful only.

HOME is the tool's filesystem seam, so every test points it at tmp_path and places
the fixture catalog at ~/.config/kura/catalog.

**Fixtures only.** Paths and helpers come from `kit_helpers`. Nothing here is imported by a test module: `conftest` is not a unique
name once a second suite directory exists, and a test doing `from conftest import X`
binds to whichever suite loaded last. `test_suites.py` asserts none do.
"""

import os
import subprocess
import sys

import pytest
from kit_helpers import CATALOG, SHIM, ensure_importable, force_colour

# Make the package importable for the pure altitude. Mirrors what the shim does, and
# must happen before any test module imports kura, which is why it is here.
ensure_importable()


@pytest.fixture(autouse=True)
def _catalog_home(tmp_path, monkeypatch):
    """Give every test an isolated HOME containing the fixture catalog."""
    h = tmp_path / "default-home"
    catalog = h / ".config" / "kura" / "catalog"
    catalog.parent.mkdir(parents=True)
    catalog.symlink_to(CATALOG, target_is_directory=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return h


@pytest.fixture
def home(_catalog_home):
    """A throwaway HOME with ~/.claude present, exported so the tool sees it."""
    (_catalog_home / ".claude").mkdir(parents=True)
    return _catalog_home


@pytest.fixture
def project(tmp_path):
    """A throwaway project, which is to say a directory.

    A bare mkdir is faithful: project_root() takes cwd at face value, so there is
    nothing to initialise. This used to `git init` because the project was the git
    top level.
    """
    p = tmp_path / "project"
    p.mkdir()
    return p


@pytest.fixture
def kit(tmp_path):
    """Run the shim as a subprocess against a throwaway HOME.

    Returns kit("add", "commit", cwd=...) -> CompletedProcess, with .home exposed
    for asserting on what landed. Reserved for end-to-end wiring checks; anything
    testable by import belongs at the pure altitude instead.
    """
    h = tmp_path / "kit-home"
    (h / ".claude").mkdir(parents=True)
    catalog = h / ".config" / "kura" / "catalog"
    catalog.parent.mkdir(parents=True)
    catalog.symlink_to(CATALOG, target_is_directory=True)

    def run(*argv, cwd=None, extra_env=None):
        env = {**os.environ, "HOME": str(h)}
        env.pop("XDG_CONFIG_HOME", None)
        env.pop("KURA_CATALOG", None)
        if extra_env:
            env.update(extra_env)
            if extra_env.get("FORCE_COLOR"):
                env.pop("NO_COLOR", None)
        return subprocess.run(
            [sys.executable, str(SHIM), *argv],
            cwd=str(cwd or h),
            env=env,
            capture_output=True,
            text=True,
        )

    run.home = h
    return run


@pytest.fixture
def coloured(monkeypatch):
    """Force colour on, as if stdout were a terminal."""
    force_colour(monkeypatch, True)


@pytest.fixture
def plain(monkeypatch):
    """Force colour off, whatever the surrounding environment says."""
    force_colour(monkeypatch, False)


@pytest.fixture(scope="session")
def catalog():
    """The fixture catalog, built once for the whole run.

    Seven modules each declared this and its `effective` companion at module scope,
    which re-parsed both registries and re-scanned the plugins directory per module.
    Both are read-only, so one session-scoped build serves every test.
    """
    from kura import catalog as cat

    return cat.build_catalog(CATALOG)


@pytest.fixture(scope="session")
def effective(catalog):
    from kura import scope

    return scope.global_set(catalog)
