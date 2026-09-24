import errno
import io
import json
import sys

import pytest

from kura import cli, config, errors, harnesses, projects, state


def _skill(catalog, name):
    directory = catalog / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"
    )


def _catalog(path, names, entries=None):
    path.mkdir(parents=True)
    (path / "skills").mkdir()
    for name in names:
        _skill(path, name)
    if entries is not None:
        (path / "skill-registry.json").write_text(json.dumps({"local": entries}))
    return path


def _configure(home, selected=("claude", "pi")):
    config.write(config.Config(selected), home)


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
    catalog = _catalog(config.catalog_path(home), ("review", "scratch"), [])
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(cwd)
    return home, cwd, catalog


def test_all_project_discovery_excludes_home_but_scans_descendants(machine):
    home, _, _ = machine
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


def test_converge_all_refuses_an_incomplete_scan_before_writing(
    machine, tmp_path, monkeypatch, capsys
):
    home, _, _ = machine
    _configure(home)
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


def test_explicit_harness_removal_keeps_retained_empty_policy_links(machine):
    home, _, catalog = machine
    _configure(home)
    retained = _link(home, catalog, "claude", "scratch")
    removed = _link(home, catalog, "pi", "scratch")

    assert cli.main(["config", "--harness", "claude", "--yes"]) == errors.OK
    assert config.read(home).global_harnesses == ("claude",)
    assert retained.is_symlink()
    assert not removed.exists()


def test_bare_config_shows_the_fixed_catalog_without_saved_configuration(machine, capsys):
    home, _, _ = machine

    assert cli.main(["config"]) == errors.OK

    output = capsys.readouterr().out
    assert "Catalog: ~/.config/kura/catalog" in output
    assert "Saved configuration: none" in output
    assert not config.path_for(home).exists()


def test_config_dry_run_reports_harness_change_without_writing(machine, capsys):
    home, _, _ = machine
    _configure(home)

    assert cli.main(["config", "--harness", "claude", "--dry-run"]) == errors.OK
    assert config.read(home).global_harnesses == ("claude", "pi")
    output = capsys.readouterr().out
    assert "Global harnesses: claude, pi -> claude" in output
    assert "Nothing written (--dry-run)" in output


@pytest.mark.parametrize(
    "response,expected,message",
    [
        ("n\n", errors.OK, "cancelled"),
        ("", errors.USAGE, "Input ended before confirmation"),
    ],
)
def test_interactive_config_preview_never_writes_on_cancel_or_eof(
    machine, monkeypatch, capsys, response, expected, message
):
    home, _, _ = machine
    _configure(home)

    class TerminalInput(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", TerminalInput(response))

    assert cli.main(["config", "--harness", "claude"]) == expected
    assert config.read(home).global_harnesses == ("claude", "pi")
    captured = capsys.readouterr()
    assert message in captured.out + captured.err


def test_config_mutation_refuses_when_the_fixed_catalog_is_missing(machine, capsys):
    home, _, catalog = machine
    _configure(home)
    (catalog / "skills" / "review" / "SKILL.md").unlink()
    (catalog / "skills" / "review").rmdir()
    (catalog / "skills" / "scratch" / "SKILL.md").unlink()
    (catalog / "skills" / "scratch").rmdir()
    (catalog / "skills").rmdir()
    (catalog / "skill-registry.json").unlink()
    catalog.rmdir()

    assert cli.main(["config", "--harness", "claude", "--yes"]) == errors.DRIFT
    assert str(config.catalog_path(home)) in capsys.readouterr().err
    assert config.read(home).global_harnesses == ("claude", "pi")


# --- first-run bootstrap: config, not init, creates machine configuration ---


def test_bootstrap_creates_machine_configuration_with_an_existing_catalog(machine):
    home, _, _ = machine

    assert cli.main(["config", "--harness", "claude", "--harness", "pi", "--yes"]) == errors.OK

    assert config.read(home).global_harnesses == ("claude", "pi")


def test_bootstrap_dry_run_writes_nothing(machine):
    home, _, _ = machine

    assert cli.main(["config", "--harness", "claude", "--dry-run"]) == errors.OK

    assert config.read(home) is None


def test_bootstrap_noninteractive_missing_catalog_requires_yes(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(cwd)

    assert cli.main(["config", "--harness", "claude"]) == errors.USAGE

    assert "does not exist" in capsys.readouterr().err
    assert not config.catalog_path(home).exists()
    assert config.read(home) is None


def test_bootstrap_interactive_creates_the_missing_catalog_after_confirmation(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(cwd)

    class TerminalInput(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", TerminalInput("yes\nyes\n"))

    assert cli.main(["config", "--harness", "claude"]) == errors.OK

    assert (config.catalog_path(home) / "skills").is_dir()
    assert config.read(home).global_harnesses == ("claude",)


def test_bootstrap_declining_the_missing_catalog_changes_nothing(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(cwd)

    class TerminalInput(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", TerminalInput("no\n"))

    assert cli.main(["config", "--harness", "claude"]) == errors.USAGE

    assert "nothing was changed" in capsys.readouterr().err
    assert not config.catalog_path(home).exists()
    assert config.read(home) is None


def test_init_without_machine_configuration_directs_the_user_to_config(machine, capsys):
    assert cli.main(["init", "--harness", "claude", "--yes"]) == errors.NO_PROJECT

    assert "kura config" in capsys.readouterr().err
