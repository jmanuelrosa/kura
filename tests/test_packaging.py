"""The shape of the thing that ships.

These guard the repository layout and the documentation contract rather than the
tool's behaviour: the shim stays thin and executable, the package stays importable
from beside it, the runtime stays stdlib-only, and every command, flag and exit code
appears in the README.

How the command reaches a machine is the installer's business and is asserted where
that installer lives, which after the extraction is the dotfiles `ai` role rather
than this repository.
"""

import ast
import os
import re
import stat
import subprocess
import sys

import pytest


from kit_helpers import CATALOG, PACKAGE, SHIM, TOOL, subparsers

def test_the_shim_is_executable():
    """It is symlinked onto PATH, so a lost +x makes kura unrunnable."""
    assert SHIM.stat().st_mode & stat.S_IXUSR


def test_the_readme_lives_beside_the_tool_it_documents():
    """No `when` guard needed any more: a file inside a tool directory is not on PATH,
    because only <name>/<name> is linked."""
    assert (TOOL / "README.md").is_file()


def test_the_shim_stays_thin():
    """Logic belongs in the importable package. An extensionless executable cannot be
    imported, so anything that grows here is code the fast tests cannot reach."""
    body = [
        line
        for line in SHIM.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    code = [line for line in body if not line.strip().startswith(('"""', "'''"))]
    assert len(code) < 20, f"the shim has grown to {len(code)} lines; move logic into kura/"


def test_the_package_is_importable_from_above_the_shim():
    """The shim inserts its parent's parent on sys.path, so the package has to sit one
    level above it. Move one without the other and kura stops importing.

    The shim lives in bin/ because the command and the package want the same name and
    one directory cannot hold both, which is the whole reason this is not the simpler
    sibling assertion it used to be.
    """
    assert (PACKAGE / "__init__.py").is_file()
    assert PACKAGE.parent == TOOL == SHIM.parent.parent


def test_the_shim_finds_the_package_with_the_implicit_path_entry_suppressed(tmp_path):
    """The only invocation that actually exercises the shim's sys.path insert.

    CPython already resolves a symlinked script before deriving sys.path[0], so running
    through ~/.local/bin/kura lands the real scripts directory there and the
    package imports whether or not the shim inserts anything. PYTHONSAFEPATH suppresses
    that implicit entry, which is what makes the insert load-bearing and is the only
    condition under which a wrong one is observable: a stale `.parent.parent` pointing at
    roles/ai/files/ passes every other subprocess test in this suite.

    Run through a symlink as well, since a development checkout is reached that way
    even though the released command is a regular file.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    link = bin_dir / "kura"
    link.symlink_to(SHIM)

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "KURA_CATALOG": str(CATALOG),
        "PYTHONSAFEPATH": "1",
    }
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [str(link), "list", "--type", "skill"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Available skills:" in result.stdout


def test_the_tests_live_beside_the_package():
    """Inside the tool directory, and with no __init__.py.

    Every suite directory in this repo is named `tests`, and an __init__.py in any of
    them makes pytest name its modules `tests.test_x`. sys.modules['tests'] is then
    claimed by whichever suite loads first and the others resolve against the wrong
    package, with nothing reporting it as a collision.

    """
    tests = TOOL / "tests"
    assert tests.is_dir()
    assert (tests / "conftest.py").is_file()
    assert not (tests / "__init__.py").exists(), "an __init__.py here collides across suites"


def test_every_command_and_flag_is_documented():
    """The README is the reference, so a new flag shipping undocumented is a defect.

    Read from the parser rather than from `--help` output so this needs no subprocess
    and cannot drift from what argparse actually accepts.
    """
    readme = (TOOL / "README.md").read_text()

    commands = subparsers()
    assert commands, "expected a subcommand parser"

    undocumented = []
    for command, parser in commands.items():
        if f"`{command}`" not in readme:
            undocumented.append(f"command {command}")
        for action in parser._actions:
            for flag in action.option_strings:
                if flag in ("-h", "--help"):
                    continue
                # Backtick then the flag, so `--group GROUP` counts alongside
                # `--group`. Requiring a closing backtick would reject documenting a
                # flag together with its metavariable, which reads better.
                if f"`{flag}" not in readme:
                    undocumented.append(f"{command} {flag}")
    assert undocumented == [], "undocumented in README.md: " + ", ".join(undocumented)


def test_every_exit_code_is_documented():
    """Callers branch on these, so the table has to stay complete."""
    from kura import errors

    readme = (TOOL / "README.md").read_text()
    missing = [name for name in errors.NAMES.values() if f"`{name}`" not in readme]
    assert missing == [], f"exit codes absent from README.md: {missing}"


def test_the_runtime_imports_only_the_standard_library():
    """PyYAML is a test dependency. A runtime import of it, or of anything else
    third-party, would break kura on a machine that only has python3.

    There is no exemption. checks.py held the last one until the frontmatter scanner
    replaced it, and that import is what made G8 skip itself on a real machine.

    tests/ is skipped: it lives inside the package but is never imported at runtime, and
    it imports pytest and PyYAML by design.
    """
    local = {PACKAGE.name}
    local.update(path.stem for path in PACKAGE.glob("*.py"))
    local.update(path.name for path in PACKAGE.iterdir() if (path / "__init__.py").is_file())
    allowed = set(sys.stdlib_module_names) | local
    offenders = []
    for module in sorted(PACKAGE.rglob("*.py")):
        if module.is_relative_to(PACKAGE / "tests"):
            continue
        source = module.read_text()
        for node in ast.walk(ast.parse(source, filename=str(module))):
            if isinstance(node, ast.Import):
                imports = ((alias.name.split(".")[0], node) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imports = ((node.module.split(".")[0], node),)
            else:
                continue
            for root, imported in imports:
                if root in allowed:
                    continue
                statement = ast.get_source_segment(source, imported)
                offenders.append(
                    f"{module.relative_to(PACKAGE)}:{imported.lineno}: {statement}"
                )
    assert offenders == [], "non-stdlib imports at runtime: " + "; ".join(offenders)
