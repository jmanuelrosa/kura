import errno
import io
import json
import sys
from pathlib import Path

import pytest

from kura import cli, config, errors, harnesses, projects, state


def _skill(catalog, name):
    directory = catalog / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"
    )


def _catalog(path, names, entries=None):
    path.mkdir()
    (path / "skills").mkdir()
    for name in names:
        _skill(path, name)
    if entries is not None:
        (path / "skill-registry.json").write_text(json.dumps({"local_skills": entries}))
    return path


def _configure(home, catalog, selected=("claude", "pi")):
    config.write(config.Config(catalog, selected), home)


def _link(home, catalog, harness_id, name, project=None):
    destination = harnesses.skill_path(harness_id, name, home, project)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(catalog / "skills" / name)
    return destination


@pytest.fixture
def machine(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("KURA_CATALOG", raising=False)
    monkeypatch.chdir(cwd)
    return home, cwd


def test_all_project_discovery_excludes_home_but_scans_descendants(machine):
    home, _ = machine
    child = home / "work" / "project"
    child.mkdir(parents=True)
    declaration = state.Manifest(("claude",), ())
    state.write(home, declaration)
    state.write(child, declaration)

    assert projects.discover(home, [home]) == [child]


def test_scan_surfaces_every_traversal_error_with_its_path(tmp_path, monkeypatch):
    paths = [tmp_path / "first", tmp_path / "second"]

    def failed_walk(root, onerror, followlinks):
        onerror(PermissionError(errno.EACCES, "permission denied", paths[0]))
        onerror(OSError(errno.EIO, "input/output error", paths[1]))
        return iter(())

    monkeypatch.setattr(projects.os, "walk", failed_walk)

    with pytest.raises(projects.ScanIncomplete) as raised:
        projects.scan(tmp_path)

    assert [error.path for error in raised.value.errors] == paths
    assert "permission denied" in str(raised.value)
    assert "input/output error" in str(raised.value)


def test_converge_all_refuses_an_incomplete_scan_before_writing(machine, tmp_path, monkeypatch, capsys):
    home, _ = machine
    catalog = _catalog(tmp_path / "catalog", ("review",))
    _configure(home, catalog)
    root = tmp_path / "projects"
    project = root / "project"
    project.mkdir(parents=True)
    state.write(project, state.Manifest(("claude",), ("review",)))
    unreadable = root / "unreadable"
    real_walk = projects.os.walk

    def incomplete_walk(root_path, onerror, followlinks):
        yield from real_walk(root_path, onerror=onerror, followlinks=followlinks)
        onerror(PermissionError(errno.EACCES, "permission denied", unreadable))

    monkeypatch.setattr(projects.os, "walk", incomplete_walk)

    assert cli.main(["converge", "--all", "--root", str(root)]) == errors.DRIFT
    assert not harnesses.skill_path("claude", "review", home, project).exists()
    assert str(unreadable) in capsys.readouterr().err


def test_config_catalog_scan_failure_preserves_links_and_saved_catalog(machine, tmp_path, monkeypatch, capsys):
    home, _ = machine
    old_catalog = _catalog(tmp_path / "old", ("review",))
    new_catalog = _catalog(tmp_path / "new", ("review",))
    _configure(home, old_catalog)
    root = tmp_path / "projects"
    project = root / "project"
    project.mkdir(parents=True)
    state.write(project, state.Manifest(("claude",), ("review",)))
    link = _link(home, old_catalog, "claude", "review", project)
    unreadable = root / "unreadable"
    real_walk = projects.os.walk

    def incomplete_walk(root_path, onerror, followlinks):
        yield from real_walk(root_path, onerror=onerror, followlinks=followlinks)
        onerror(PermissionError(errno.EACCES, "permission denied", unreadable))

    monkeypatch.setattr(projects.os, "walk", incomplete_walk)

    result = cli.main(
        ["config", "--catalog", str(new_catalog), "--root", str(root), "--yes"]
    )

    assert result == errors.DRIFT
    assert config.read(home).catalog == old_catalog
    assert link.is_symlink() and link.resolve() == old_catalog / "skills" / "review"
    assert str(unreadable) in capsys.readouterr().err


def test_config_root_requires_a_catalog_mutation(machine, tmp_path, capsys):
    assert cli.main(["config", "--root", str(tmp_path)]) == errors.USAGE
    assert "--root applies only with --catalog" in capsys.readouterr().err


def test_catalog_move_retains_the_empty_global_policy_guard(machine, tmp_path):
    home, _ = machine
    old_catalog = _catalog(
        tmp_path / "old",
        ("global-tool",),
        [{"name": "global-tool", "groups": ["global"]}],
    )
    new_catalog = _catalog(tmp_path / "new", ("global-tool",), [])
    _configure(home, old_catalog)
    links = [
        _link(home, old_catalog, harness_id, "global-tool")
        for harness_id in ("claude", "pi")
    ]

    assert cli.main(["config", "--catalog", str(new_catalog), "--yes"]) == errors.DRIFT
    assert config.read(home).catalog == old_catalog
    assert all(link.is_symlink() for link in links)


def test_explicit_harness_removal_keeps_retained_empty_policy_links(machine, tmp_path):
    home, _ = machine
    catalog = _catalog(tmp_path / "catalog", ("scratch",), [])
    _configure(home, catalog)
    retained = _link(home, catalog, "claude", "scratch")
    removed = _link(home, catalog, "pi", "scratch")

    assert cli.main(["config", "--harness", "claude", "--yes"]) == errors.OK
    assert config.read(home).global_harnesses == ("claude",)
    assert retained.is_symlink()
    assert not removed.exists()


def test_catalog_move_refuses_included_project_with_missing_direct_content(machine, tmp_path):
    home, cwd = machine
    old_catalog = _catalog(tmp_path / "old", ("vanished",))
    new_catalog = _catalog(tmp_path / "new", ())
    _configure(home, old_catalog)
    state.write(cwd, state.Manifest(("claude",), ("vanished",)))
    link = _link(home, old_catalog, "claude", "vanished", cwd)

    assert cli.main(["config", "--catalog", str(new_catalog), "--yes"]) == errors.DRIFT
    assert config.read(home).catalog == old_catalog
    assert link.is_symlink()


def test_catalog_move_refuses_removed_dependency_when_old_catalog_is_absent(machine, tmp_path, capsys):
    home, cwd = machine
    old_catalog = _catalog(
        tmp_path / "old",
        ("review", "helper"),
        [
            {"name": "review", "dependencies": ["helper"]},
            {"name": "helper", "dependency_only": True},
        ],
    )
    _configure(home, old_catalog)
    state.write(cwd, state.Manifest(("claude",), ("review",)))
    review = _link(home, old_catalog, "claude", "review", cwd)
    helper = _link(home, old_catalog, "claude", "helper", cwd)
    old_catalog.rename(tmp_path / "unavailable-old")
    new_catalog = _catalog(
        tmp_path / "new",
        ("review", "helper"),
        [{"name": "review"}, {"name": "helper", "dependency_only": True}],
    )

    assert cli.main(["config", "--catalog", str(new_catalog), "--yes"]) == errors.DRIFT
    assert config.read(home).catalog == old_catalog
    assert review.is_symlink() and helper.is_symlink()
    report = capsys.readouterr().err
    assert "Unresolved catalog-move drift" in report
    assert "helper" in report


def test_catalog_move_scans_each_explicit_root_without_scanning_cwd(machine, tmp_path):
    home, cwd = machine
    old_catalog = _catalog(tmp_path / "old", ("review",))
    new_catalog = _catalog(tmp_path / "new", ("review",))
    _configure(home, old_catalog)
    roots = [tmp_path / "first-root", tmp_path / "second-root"]
    included = []
    for root in roots:
        project = root / "project"
        project.mkdir(parents=True)
        state.write(project, state.Manifest(("claude",), ("review",)))
        included.append(_link(home, old_catalog, "claude", "review", project))
    omitted_project = cwd / "omitted"
    omitted_project.mkdir()
    state.write(omitted_project, state.Manifest(("claude",), ("review",)))
    omitted = _link(home, old_catalog, "claude", "review", omitted_project)

    result = cli.main(
        [
            "config",
            "--catalog",
            str(new_catalog),
            "--root",
            str(roots[0]),
            "--root",
            str(roots[1]),
            "--yes",
        ]
    )

    assert result == errors.OK
    assert all(link.resolve() == new_catalog / "skills" / "review" for link in included)
    assert omitted.is_symlink() and omitted.resolve() == old_catalog / "skills" / "review"


def test_config_dry_run_renders_saved_catalog_without_writing(machine, tmp_path, capsys):
    home, _ = machine
    old_catalog = _catalog(tmp_path / "old", ())
    new_catalog = _catalog(tmp_path / "new", ())
    _configure(home, old_catalog)

    assert cli.main(["config", "--catalog", str(new_catalog), "--dry-run"]) == errors.OK
    assert config.read(home).catalog == old_catalog
    output = capsys.readouterr()
    assert "Saved catalog:" in output.out
    assert str(new_catalog) in output.out


@pytest.mark.parametrize(
    "response,expected,message",
    [
        ("n\n", errors.OK, "cancelled"),
        ("", errors.USAGE, "Input ended before confirmation"),
    ],
)
def test_interactive_config_preview_never_writes_on_cancel_or_eof(
    machine, tmp_path, monkeypatch, capsys, response, expected, message
):
    home, _ = machine
    old_catalog = _catalog(tmp_path / "old", ())
    new_catalog = _catalog(tmp_path / "new", ())
    _configure(home, old_catalog)

    class TerminalInput(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", TerminalInput(response))

    assert cli.main(["config", "--catalog", str(new_catalog)]) == expected
    assert config.read(home).catalog == old_catalog
    output = capsys.readouterr()
    assert "Saved catalog:" in output.out
    assert message in output.out + output.err


def test_catalog_move_warnings_are_sent_to_stderr(machine, tmp_path, capsys):
    home, _ = machine
    old_catalog = _catalog(tmp_path / "old", ())
    new_catalog = _catalog(tmp_path / "new", ())
    _configure(home, old_catalog)

    assert cli.main(["config", "--catalog", str(new_catalog), "--dry-run"]) == errors.OK
    output = capsys.readouterr()
    assert "Kura remembers the old catalog" in output.err
    assert "will become foreign" in output.err
    assert "Kura remembers the old catalog" not in output.out


def test_bare_config_names_catalog_environment_source_without_saved_config(machine, tmp_path, monkeypatch, capsys):
    catalog = _catalog(tmp_path / "catalog", ())
    monkeypatch.setenv(config.ENV_CATALOG, str(catalog))

    assert cli.main(["config"]) == errors.OK
    assert f"Source: {config.ENV_CATALOG}" in capsys.readouterr().out
