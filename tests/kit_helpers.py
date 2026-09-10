"""Helpers this suite shares, in a module no other directory can claim.

These cannot live in `conftest.py`: a test module importing them by that name is a
collision waiting for a second suite directory, since every non-package `conftest.py`
is named, literally, `conftest`, and with more than one on `sys.path` the name
resolves to whichever loaded last. The name here is unique instead.

`CATALOG` is the fixture catalog, and is the one path this suite reads. Before the
tool was extracted these constants came from the dotfiles checkout and the suite
asserted against the artifacts that repository happened to hold, which made a
registry edit able to fail the application's tests. The fixture keeps the artifact
names the suite was written against, so a case still reads concretely, but the
properties those names carry are now fixed here rather than borrowed.
"""

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
TOOL = TESTS.parent
PACKAGE = TOOL / "kura"
# In bin/ rather than beside the package: the command and the package want the same
# name, so one directory cannot hold both.
SHIM = TOOL / "bin" / "kura"

# The catalog under test: registries, skills, agents and plugins, in the layout the
# tool documents. Nothing outside tests/fixtures/ is read by this suite.
CATALOG = TESTS / "fixtures" / "catalog"


def force_colour(monkeypatch, on):
    """Turn colour on or off for an in-process render.

    colors.enabled reads the environment per call, so this is all a test needs to make
    a non-tty stdout behave like a terminal, or the reverse. The `coloured` and `plain`
    fixtures wrap it for the common case; test_help calls it directly because it
    renders the same help twice, once each way, inside a single test.
    """
    if on:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
    else:
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("NO_COLOR", "1")


def subparsers():
    """The CLI's subcommand parsers, by name.

    argparse exposes them only as the one action whose `choices` is a dict, so the
    lookup is written here rather than in each module that needs it.
    """
    from kura.cli import build_parser

    action = next(
        a for a in build_parser()._actions if hasattr(a, "choices") and isinstance(a.choices, dict)
    )
    return action.choices


def ensure_importable():
    """Put the tool's directory on sys.path, exactly as the shim does.

    Called from conftest so it happens before any test module imports kura.
    """
    if str(TOOL) not in sys.path:
        sys.path.insert(0, str(TOOL))
