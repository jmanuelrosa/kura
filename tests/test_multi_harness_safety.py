import json
import os
from pathlib import Path
import stat
import time

import pytest

from kura import catalog as cat
from kura import errors, harnesses, pi_trust, state, views
from kura.commands import common
from kura.commands import trust as trust_command
from kura.transaction import Action, Conflict, Failed, Snapshot, Transaction, snapshot
import kura.transaction as transaction_module


def add_skill(root, name):
    directory = root / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"
    )


def make_catalog(tmp_path, entries, present):
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "skills").mkdir()
    for name in present:
        add_skill(root, name)
    (root / "skill-registry.json").write_text(json.dumps({"local_skills": entries}))
    return root, cat.build_catalog(root)


def test_project_native_root_checks_existing_directory_symlink_ancestors(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    foreign = tmp_path / "foreign"
    home.mkdir()
    project.mkdir()
    (foreign / "skills").mkdir(parents=True)
    (project / ".claude").symlink_to(foreign)
    catalog_root, catalog = make_catalog(
        tmp_path,
        [{"name": "review"}],
        ["review"],
    )

    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        state.Manifest(("claude",), ()),
        state.Manifest(("claude",), ("review",)),
    )

    assert plan.refused
    assert any(
        f"{project / '.claude'} is a directory symlink" in item
        for item in plan.blocked
    )


def test_native_root_ancestor_changes_are_refused_at_apply(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    (project / ".claude" / "skills").mkdir(parents=True)
    catalog_root, catalog = make_catalog(
        tmp_path,
        [{"name": "review"}],
        ["review"],
    )
    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        state.Manifest(("claude",), ()),
        state.Manifest(("claude",), ("review",)),
    )
    foreign = tmp_path / "foreign"
    (foreign / "skills").mkdir(parents=True)
    (project / ".claude" / "skills").rmdir()
    (project / ".claude").rmdir()
    (project / ".claude").symlink_to(foreign)

    with pytest.raises(Failed) as raised:
        Transaction().run(plan.ordered_actions())

    assert isinstance(raised.value.cause, Conflict)
    assert (project / ".claude").is_symlink()
    assert not (foreign / "skills" / "review").exists()


def test_global_native_root_checks_existing_directory_symlink_ancestors(tmp_path):
    home = tmp_path / "home"
    foreign = tmp_path / "foreign"
    home.mkdir()
    (foreign / "skills").mkdir(parents=True)
    (home / ".agents").symlink_to(foreign)
    catalog_root, catalog = make_catalog(
        tmp_path,
        [{"name": "review"}],
        ["review"],
    )

    plan = views.global_plan(
        catalog,
        catalog_root,
        home,
        ("pi",),
        desired_names=("review",),
    )

    assert plan.refused
    assert any(
        f"{home / '.agents'} is a directory symlink" in item
        for item in plan.blocked
    )


@pytest.mark.parametrize("dangling", [False, True])
def test_create_refuses_foreign_symlinks_added_after_preflight(tmp_path, dangling):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "destination"
    action = Action("create", destination, source)
    foreign = tmp_path / "foreign"
    if not dangling:
        foreign.mkdir()
    destination.symlink_to(foreign)

    with pytest.raises(Failed) as raised:
        Transaction().run([action])

    assert isinstance(raised.value.cause, Conflict)
    assert destination.is_symlink()
    assert os.readlink(destination) == str(foreign)


def test_relink_refuses_a_target_changed_after_preflight(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    foreign = tmp_path / "foreign"
    for directory in (old, new, foreign):
        directory.mkdir()
    destination = tmp_path / "destination"
    destination.symlink_to(old)
    action = Action("relink", destination, new, expected=snapshot(destination))
    destination.unlink()
    destination.symlink_to(foreign)

    with pytest.raises(Failed) as raised:
        Transaction().run([action])

    assert isinstance(raised.value.cause, Conflict)
    assert os.readlink(destination) == str(foreign)


def test_write_refuses_bytes_or_mode_changed_after_preflight(tmp_path):
    path = tmp_path / "state.json"
    path.write_bytes(b"old")
    path.chmod(0o640)
    action = Action("write", path, data=b"new", mode=0o640, expected=snapshot(path))
    path.chmod(0o600)

    with pytest.raises(Failed) as raised:
        Transaction().run([action])

    assert isinstance(raised.value.cause, Conflict)
    assert path.read_bytes() == b"old"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rollback_restores_exact_file_bytes_and_full_mode(tmp_path):
    path = tmp_path / "state.json"
    path.write_bytes(b"before\n")
    path.chmod(0o1751)
    blocked = tmp_path / "blocked"
    blocked.mkdir()

    with pytest.raises(Failed) as raised:
        Transaction().run(
            [
                Action("write", path, data=b"after\n", mode=0o600),
                Action("write", blocked, data=b"no"),
            ]
        )

    assert not raised.value.rollback_errors
    assert path.read_bytes() == b"before\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o1751


def test_rollback_preserves_a_concurrent_file(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_bytes(b"before")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    transaction = Transaction()
    original_write = transaction.write

    def write_with_collision(destination, data, mode=None, expected=None):
        if Path(destination) == blocked:
            path.write_bytes(b"concurrent")
            path.chmod(0o640)
        return original_write(destination, data, mode, expected)

    monkeypatch.setattr(transaction, "write", write_with_collision)
    with pytest.raises(Failed) as raised:
        transaction.run(
            [
                Action("write", path, data=b"transaction", mode=0o600),
                Action("write", blocked, data=b"no"),
            ]
        )

    assert path.read_bytes() == b"concurrent"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert any(error_path == path and "rollback conflict" in str(error) for error_path, error in raised.value.rollback_errors)


def test_legacy_pi_bridge_is_restored_after_rollback(tmp_path):
    project = tmp_path / "project"
    claude_root = project / ".claude" / "skills"
    claude_root.mkdir(parents=True)
    source = tmp_path / "catalog" / "skills" / "review"
    source.mkdir(parents=True)
    (claude_root / "review").symlink_to(source)
    pi_parent = project / ".agents"
    pi_parent.mkdir()
    pi_root = pi_parent / "skills"
    bridge_target = Path("..") / ".claude" / "skills"
    pi_root.symlink_to(bridge_target)
    blocked = tmp_path / "blocked"
    blocked.mkdir()

    with pytest.raises(Failed) as raised:
        Transaction().run(
            [
                Action("delete", pi_root),
                Action("create", pi_root / "review", source, expected=Snapshot("absent")),
                Action("write", blocked, data=b"no"),
            ]
        )

    assert not raised.value.rollback_errors
    assert pi_root.is_symlink()
    assert os.readlink(pi_root) == str(bridge_target)
    assert (claude_root / "review").is_symlink()


def test_directory_pruning_failures_are_rollback_errors(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "new" / "skill"
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    transaction = Transaction()
    original_write = transaction.write

    def write_with_extra(destination_path, data, mode=None, expected=None):
        if Path(destination_path) == blocked:
            (destination.parent / "concurrent").write_text("keep")
        return original_write(destination_path, data, mode, expected)

    monkeypatch.setattr(transaction, "write", write_with_extra)
    with pytest.raises(Failed) as raised:
        transaction.run(
            [
                Action("create", destination, source),
                Action("write", blocked, data=b"no"),
            ]
        )

    assert any(path == destination.parent for path, _ in raised.value.rollback_errors)
    assert (destination.parent / "concurrent").read_text() == "keep"


def test_project_remove_cleans_a_missing_dependency_link(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    catalog_root, catalog = make_catalog(
        tmp_path,
        [
            {"name": "review", "dependencies": ["missing-helper"]},
            {"name": "missing-helper", "dependency_only": True},
        ],
        ["review"],
    )
    helper = harnesses.skill_path("claude", "missing-helper", home, project)
    helper.parent.mkdir(parents=True)
    helper.symlink_to(catalog_root / "skills" / "missing-helper")

    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        state.Manifest(("claude",), ("review",)),
        state.Manifest(("claude",), ()),
    )

    assert any(action.operation == "delete" and action.path == helper for action in plan.actions)


def test_unrelated_broken_global_metadata_does_not_block_project_plan(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    catalog_root, catalog = make_catalog(
        tmp_path,
        [
            {"name": "local"},
            {"name": "broken-global", "groups": ["global"], "dependencies": ["missing"]},
        ],
        ["local", "broken-global"],
    )

    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        state.Manifest(("claude",), ()),
        state.Manifest(("claude",), ("local",)),
    )

    assert not plan.refused
    assert any(action.skill == "local" for action in plan.actions)


def test_replaced_native_root_suppresses_old_descendant_deletes(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    catalog_root, catalog = make_catalog(
        tmp_path,
        [{"name": "review"}],
        ["review"],
    )
    claude_root = harnesses.project_skill_root(project, "claude")
    claude_root.mkdir(parents=True)
    (claude_root / "review").symlink_to(catalog_root / "skills" / "review")
    pi_parent = project / ".agents"
    pi_parent.mkdir()
    pi_root = harnesses.project_skill_root(project, "pi")
    pi_root.symlink_to(Path("..") / ".claude" / "skills")

    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        state.Manifest(("pi",), ("review",)),
        state.Manifest(("pi",), ()),
        replace_roots=("pi",),
    )

    assert not any(action.path != pi_root and pi_root in action.path.parents for action in plan.actions)
    assert (claude_root / "review").is_symlink()


def test_pi_lock_reclaims_only_an_empty_stale_directory(tmp_path):
    store = tmp_path / "trust.json"
    lock_path = Path(str(store) + pi_trust.LOCK_SUFFIX)
    lock_path.mkdir()
    old = time.time() - pi_trust.LOCK_STALE_SECONDS - 1
    os.utime(lock_path, (old, old))
    with pi_trust.lock(store):
        assert lock_path.is_dir()
        assert lock_path.stat().st_mtime > old

    assert not lock_path.exists()


def test_pi_lock_retries_an_active_lock_ten_times(tmp_path, monkeypatch):
    store = tmp_path / "trust.json"
    lock_path = Path(str(store) + pi_trust.LOCK_SUFFIX)
    lock_path.mkdir()
    waits = []
    monkeypatch.setattr(pi_trust.time, "sleep", waits.append)

    with pytest.raises(OSError, match="is locked"):
        with pi_trust.lock(store):
            pass

    assert waits == [pi_trust.LOCK_WAIT_SECONDS] * (pi_trust.LOCK_ATTEMPTS - 1)
    assert lock_path.is_dir()


def test_pi_lock_does_not_reclaim_a_nonempty_stale_directory(tmp_path, monkeypatch):
    store = tmp_path / "trust.json"
    lock_path = Path(str(store) + pi_trust.LOCK_SUFFIX)
    lock_path.mkdir()
    (lock_path / "owner").write_text("active")
    old = time.time() - pi_trust.LOCK_STALE_SECONDS - 1
    os.utime(lock_path, (old, old))
    monkeypatch.setattr(pi_trust.time, "sleep", lambda _: None)

    with pytest.raises(OSError, match="is locked"):
        with pi_trust.lock(store):
            pass

    assert (lock_path / "owner").read_text() == "active"


def test_pi_lock_cleanup_does_not_remove_a_replacement(tmp_path):
    store = tmp_path / "trust.json"
    lock_path = Path(str(store) + pi_trust.LOCK_SUFFIX)

    with pytest.raises(OSError, match="before cleanup"):
        with pi_trust.lock(store):
            lock_path.rmdir()
            lock_path.mkdir()
            (lock_path / "owner").write_text("other")

    assert (lock_path / "owner").read_text() == "other"


def test_trust_dry_run_does_not_wait_for_the_pi_lock(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    lock_path = Path(str(pi_trust.store_path(home)) + pi_trust.LOCK_SUFFIX)
    lock_path.mkdir(parents=True)
    monkeypatch.setattr(
        pi_trust.time,
        "sleep",
        lambda _: pytest.fail("dry-run tried to acquire the Pi lock"),
    )

    assert trust_command._mutate(home, project, ["pi"], True, True) == errors.OK
    assert "Nothing written (--dry-run)" in capsys.readouterr().out
    assert lock_path.is_dir()


def test_bare_pi_trust_read_occurs_while_locked(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    store = pi_trust.store_path(home)
    store.parent.mkdir(parents=True)
    store.write_text(pi_trust.dump({str(project.resolve()): True}))
    lock_path = Path(str(store) + pi_trust.LOCK_SUFFIX)
    original = trust_command._pi_state

    def checked(home_path, project_path):
        assert lock_path.is_dir()
        return original(home_path, project_path)

    monkeypatch.setattr(trust_command, "_pi_state", checked)
    assert trust_command._report(home, project, ["pi"]) == errors.OK
    capsys.readouterr()


def test_cross_harness_trust_collision_rolls_back_claude(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    claude_path = home / ".claude.json"
    claude_path.write_bytes(b'{"projects": {}}')
    claude_path.chmod(0o1640)
    claude_before = snapshot(claude_path)
    pi_path = pi_trust.store_path(home)
    concurrent = b'{"foreign": true}'
    original_write = transaction_module.Transaction.write
    collided = False

    def write_with_pi_collision(self, path, data, mode=None, expected=None):
        nonlocal collided
        if Path(path) == pi_path and not collided:
            collided = True
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(concurrent)
        return original_write(self, path, data, mode, expected)

    monkeypatch.setattr(transaction_module.Transaction, "write", write_with_pi_collision)
    with pytest.raises(common.Refusal) as raised:
        trust_command._mutate(home, project, ["claude", "pi"], True, False)

    assert raised.value.code == errors.DRIFT
    assert snapshot(claude_path) == claude_before
    assert pi_path.read_bytes() == concurrent


def test_claude_trust_target_uses_the_main_checkout_key(tmp_path):
    home = tmp_path / "home"
    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    gitdir = main / ".git" / "worktrees" / "topic"
    home.mkdir()
    worktree.mkdir()
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..")
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n")

    targets = trust_command._targets(home, worktree, ["claude"])

    assert targets == [("claude", home / ".claude.json", str(main))]


def test_trust_output_keeps_inherited_claude_truth_and_restart_guidance(tmp_path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "parent" / "project"
    home.mkdir()
    project.mkdir(parents=True)
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "projects": {
                    str(project.parent): {"hasTrustDialogAccepted": True},
                }
            }
        )
    )

    assert trust_command._mutate(home, project, ["claude", "pi"], False, False) == errors.OK
    output = capsys.readouterr().out
    assert "trust remains inherited" in output
    assert "Trust decisions were saved" in output
    assert "Restart live harness sessions" in output
