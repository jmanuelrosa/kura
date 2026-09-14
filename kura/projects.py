"""Root-manifest project scanning for `converge --all`."""

from dataclasses import dataclass
import os
from pathlib import Path

from . import state

SKIP = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "bower_components",
    "vendor",
    "dist",
    "build",
    "target",
}


@dataclass(frozen=True)
class TraversalError:
    path: Path
    message: str


class ScanIncomplete(Exception):
    def __init__(self, errors):
        self.errors = tuple(errors)
        detail = "; ".join(f"{error.path}: {error.message}" for error in self.errors)
        super().__init__(detail)


def marks_a_project(directory):
    return (Path(directory) / state.FILENAME).is_file()


def _resolved(path):
    path = Path(path)
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def scan(root):
    root = Path(root)
    found = []
    errors = []

    def onerror(error):
        filename = os.fsdecode(error.filename) if error.filename is not None else str(root)
        errors.append(TraversalError(Path(filename), error.strerror or str(error)))

    for current, directories, _ in os.walk(root, onerror=onerror, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if name not in SKIP and not name.startswith(".")
        )
        here = Path(current)
        if marks_a_project(here):
            found.append(here)
    if errors:
        raise ScanIncomplete(sorted(errors, key=lambda error: (str(error.path), error.message)))
    return found


def discover(home=None, roots=None):
    roots = [Path.cwd()] if roots is None else [Path(root) for root in roots]
    excluded = _resolved(home) if home is not None else None
    seen = {}
    errors = []
    for root in roots:
        try:
            found = scan(root)
        except ScanIncomplete as exc:
            errors.extend(exc.errors)
            continue
        for project in found:
            resolved = _resolved(project)
            if resolved == excluded:
                continue
            seen[str(resolved)] = project
    if errors:
        raise ScanIncomplete(sorted(errors, key=lambda error: (str(error.path), error.message)))
    return [seen[key] for key in sorted(seen)]
