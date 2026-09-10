#!/usr/bin/env python3
"""Build the release asset: one executable file holding the whole package.

A zipapp rather than a flattened script, because the package layout is what lets
pytest import the code instead of only driving a subprocess, and flattening would
trade that away for nothing. Python executes a zip with a shebang directly, so the
asset stays a single file that needs no installer and no sibling directory.

The archive is written by hand rather than through `zipapp`, for determinism: zipapp
stamps each member with its mtime, so two builds of identical sources produce
different bytes and a checksum stops meaning "this source". Every member here gets a
fixed timestamp and the members are sorted, so the same tree always yields the same
asset and the same checksum.
"""

import io
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "kura"
DIST = ROOT / "dist"
TARGET = DIST / "kura"

# Python's own convention for "no timestamp", and what every reproducible-build tool
# writes. The zip format cannot express an absent one.
EPOCH = (1980, 1, 1, 0, 0, 0)
SHEBANG = b"#!/usr/bin/env python3\n"

ENTRY = '''"""Zipapp entry point. The package is what holds the logic."""

import sys

from kura.cli import main

if __name__ == "__main__":
    sys.exit(main())
'''


def members():
    """Every runtime file, relative to the archive root, name-ordered.

    `tests/` is not here and neither is anything outside the package: the asset holds
    what the tool imports at runtime and nothing that only a checkout needs.
    """
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path.relative_to(ROOT).as_posix(), path.read_bytes()
    yield "__main__.py", ENTRY.encode()


def main():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members():
            info = zipfile.ZipInfo(name, date_time=EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)

    DIST.mkdir(exist_ok=True)
    TARGET.write_bytes(SHEBANG + buffer.getvalue())
    TARGET.chmod(TARGET.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"built {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
