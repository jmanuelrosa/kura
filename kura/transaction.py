"""Filesystem transactions for native links and JSON state."""

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tempfile


class Failed(Exception):
    def __init__(self, cause, rollback_errors=()):
        super().__init__(str(cause))
        self.cause = cause
        self.rollback_errors = tuple(rollback_errors)


class Conflict(OSError):
    pass


@dataclass(frozen=True)
class Snapshot:
    kind: str
    target: str = None
    data: bytes = None
    mode: int = None


def snapshot(path):
    path = Path(path)
    try:
        found = path.lstat()
    except FileNotFoundError:
        return Snapshot("absent")
    if stat.S_ISLNK(found.st_mode):
        return Snapshot("symlink", target=os.readlink(path))
    if stat.S_ISREG(found.st_mode):
        return Snapshot("file", data=path.read_bytes(), mode=stat.S_IMODE(found.st_mode))
    return Snapshot("other")


@dataclass(frozen=True)
class Action:
    operation: str
    path: Path
    target: Path = None
    data: bytes = None
    mode: int = None
    harness: str = None
    skill: str = None
    expected: Snapshot = None
    expected_ancestors: tuple = ()

    def __post_init__(self):
        if self.expected is None:
            object.__setattr__(self, "expected", snapshot(self.path))


def _description(value):
    if value.kind == "symlink":
        return f"symlink -> {value.target}"
    if value.kind == "file":
        return f"file with mode {value.mode:#o}"
    return value.kind


class Transaction:
    def __init__(self):
        self.snapshots = {}
        self.outputs = {}
        self.order = []
        self.created_dirs = []

    def remember(self, path, before=None):
        path = Path(path)
        if path not in self.snapshots:
            self.snapshots[path] = snapshot(path) if before is None else before
            self.order.append(path)
        return self.snapshots[path]

    def _check(self, path, expected):
        path = Path(path)
        live = snapshot(path)
        if live != expected:
            raise Conflict(
                f"{path} changed since preflight: expected {_description(expected)}, "
                f"found {_description(live)}"
            )
        return live

    def _check_ancestors(self, expected_ancestors):
        for path, expected in expected_ancestors:
            live = snapshot(path)
            created = (
                expected.kind == "absent"
                and live.kind == "other"
                and path in self.created_dirs
                and path.is_dir()
                and not path.is_symlink()
            )
            if live != expected and not created:
                raise Conflict(
                    f"{path} changed since preflight: expected {_description(expected)}, "
                    f"found {_description(live)}"
                )
            if live.kind == "symlink":
                raise Conflict(f"refusing directory symlink {path}")
            if live.kind in ("file", "other") and not path.is_dir():
                raise Conflict(f"refusing non-directory ancestor {path}")

    def mkdirs(self, parent, allow_directory_symlinks=False):
        missing = []
        current = Path(parent)
        while True:
            if current.is_symlink():
                if allow_directory_symlinks and current.is_dir():
                    break
                raise OSError(f"refusing directory symlink {current}")
            if current.exists():
                if not current.is_dir():
                    raise OSError(f"refusing non-directory ancestor {current}")
                break
            missing.append(current)
            if current == current.parent:
                break
            current = current.parent
        for directory in reversed(missing):
            directory.mkdir()
            self.created_dirs.append(directory)

    def mkdir(self, path, expected=None):
        path = Path(path)
        expected = snapshot(path) if expected is None else expected
        self._check(path, expected)
        if expected.kind != "absent":
            if expected.kind != "other" or not path.is_dir() or path.is_symlink():
                raise OSError(f"refusing to replace non-directory {path}")
            return
        self.mkdirs(path.parent, allow_directory_symlinks=True)
        self._check(path, expected)
        self.remember(path, expected)
        self.outputs[path] = expected
        path.mkdir()
        self.created_dirs.append(path)
        self.outputs[path] = snapshot(path)

    def symlink(
        self,
        path,
        target,
        expected=None,
        replace=False,
        expected_ancestors=(),
    ):
        path = Path(path)
        expected = snapshot(path) if expected is None else expected
        self._check_ancestors(expected_ancestors)
        self._check(path, expected)
        required_kind = "symlink" if replace else "absent"
        if expected.kind != required_kind:
            verb = "relink" if replace else "create"
            raise Conflict(f"refusing to {verb} over {_description(expected)} at {path}")
        self.mkdirs(path.parent)
        self._check_ancestors(expected_ancestors)
        self._check(path, expected)
        self.remember(path, expected)
        if replace:
            path.unlink()
        self.outputs[path] = Snapshot("absent")
        path.symlink_to(target)
        self.outputs[path] = snapshot(path)

    def unlink(self, path, expected=None, expected_ancestors=()):
        path = Path(path)
        expected = snapshot(path) if expected is None else expected
        self._check_ancestors(expected_ancestors)
        self._check(path, expected)
        if expected.kind != "symlink":
            raise OSError(f"refusing to unlink non-link {path}")
        self.remember(path, expected)
        path.unlink()
        self.outputs[path] = Snapshot("absent")

    def _atomic_write(self, path, data, mode):
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        except BaseException:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise

    def write(self, path, data, mode=None, expected=None):
        path = Path(path)
        expected = snapshot(path) if expected is None else expected
        self._check(path, expected)
        if expected.kind not in ("absent", "file"):
            raise OSError(f"refusing to replace non-file {path}")
        self.mkdirs(path.parent, allow_directory_symlinks=True)
        selected_mode = mode if mode is not None else expected.mode if expected.kind == "file" else 0o644
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.chmod(temporary, selected_mode)
            self._check(path, expected)
            self.remember(path, expected)
            self.outputs[path] = expected
            os.replace(temporary, path)
            self.outputs[path] = snapshot(path)
        except BaseException:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise

    def delete_file(self, path, expected=None):
        path = Path(path)
        expected = snapshot(path) if expected is None else expected
        self._check(path, expected)
        if expected.kind != "file":
            raise OSError(f"refusing to delete non-file {path}")
        self.remember(path, expected)
        path.unlink()
        self.outputs[path] = Snapshot("absent")

    def run(self, actions):
        try:
            for action in actions:
                if action.operation == "mkdir":
                    self.mkdir(action.path, action.expected)
                elif action.operation == "create":
                    self.symlink(
                        action.path,
                        action.target,
                        action.expected,
                        expected_ancestors=action.expected_ancestors,
                    )
                elif action.operation == "relink":
                    self.symlink(
                        action.path,
                        action.target,
                        action.expected,
                        replace=True,
                        expected_ancestors=action.expected_ancestors,
                    )
                elif action.operation == "delete":
                    self.unlink(
                        action.path,
                        action.expected,
                        action.expected_ancestors,
                    )
                elif action.operation == "write":
                    self.write(action.path, action.data, action.mode, action.expected)
                elif action.operation == "delete-file":
                    self.delete_file(action.path, action.expected)
                else:
                    raise ValueError(f"unknown transaction operation {action.operation}")
        except BaseException as exc:
            rollback_errors = self.rollback()
            raise Failed(exc, rollback_errors) from exc

    def _forget_created(self, path):
        self.created_dirs = [directory for directory in self.created_dirs if directory != path]

    def _restore(self, path, before, after):
        live = snapshot(path)
        if (
            before.kind == "symlink"
            and after.kind == "absent"
            and live.kind == "other"
            and path in self.created_dirs
            and path.is_dir()
            and not path.is_symlink()
        ):
            try:
                path.rmdir()
            except OSError as exc:
                raise Conflict(f"rollback conflict at {path}: transaction-created directory is not empty") from exc
            self._forget_created(path)
            live = snapshot(path)
        if live != after:
            raise Conflict(
                f"rollback conflict at {path}: expected transaction output {_description(after)}, "
                f"found {_description(live)}"
            )
        if before == after:
            return
        if live.kind in ("symlink", "file"):
            path.unlink()
        elif live.kind == "other":
            if path not in self.created_dirs or not path.is_dir() or path.is_symlink():
                raise Conflict(f"rollback conflict at {path}: refusing to remove {_description(live)}")
            path.rmdir()
            self._forget_created(path)
        if before.kind == "symlink":
            self.mkdirs(path.parent)
            path.symlink_to(before.target)
        elif before.kind == "file":
            self.mkdirs(path.parent, allow_directory_symlinks=True)
            self._atomic_write(path, before.data, before.mode)

    def rollback(self):
        failures = []
        failed_paths = set()
        for path in reversed(self.order):
            try:
                self._restore(path, self.snapshots[path], self.outputs[path])
            except BaseException as exc:
                failures.append((path, exc))
                failed_paths.add(path)
        for directory in reversed(self.created_dirs):
            if directory in failed_paths:
                continue
            found = snapshot(directory)
            if found.kind == "absent":
                continue
            if found.kind != "other" or not directory.is_dir() or directory.is_symlink():
                failures.append(
                    (
                        directory,
                        Conflict(
                            f"rollback conflict at {directory}: refusing to prune {_description(found)}"
                        ),
                    )
                )
                continue
            try:
                directory.rmdir()
            except OSError as exc:
                failures.append((directory, exc))
        return failures


def apply(actions):
    transaction = Transaction()
    transaction.run(actions)
    return transaction
