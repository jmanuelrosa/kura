import json

import pytest
from kit_helpers import register

from kura import catalog as cat
from kura import cli, config, errors, state


def write_skill(root, name, **fields):
    source = root / "skills" / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n")
    register(root, cat.SKILL, name, **fields)


def write_agent(root, name, **fields):
    directory = root / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n")
    register(root, cat.AGENT, name, **fields)


def workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(project)
    catalog = config.catalog_path(home)
    (catalog / "skills").mkdir(parents=True)
    config.write(config.Config(("claude",), {}), home)
    write_skill(catalog, "old", deprecated=True)
    write_skill(catalog, "fresh")
    return home, project, catalog


def initialise(project, skills=(), agents=()):
    state.write(
        project,
        state.Manifest(("claude",), tuple(skills), schema_version=state.V2_SCHEMA_VERSION, agents=tuple(agents)),
    )


def test_every_row_kind_carries_the_flag(tmp_path):
    root = tmp_path / "catalog"
    (root / "skills").mkdir(parents=True)
    (root / cat.REGISTRY_FILE[cat.SKILL]).write_text(json.dumps({
        "upstream": {"owner/repo": {"branch": "main", "skills": [{"upstream_path": "skills/vendored", "deprecated": True}]}},
        "local": [
            {"name": "local", "deprecated": True, "dependency_only": True},
            {"name": "current", "deprecated": False},
            {"name": "unmarked"},
        ],
    }))
    register(root, cat.AGENT, "planner", deprecated=True)
    register(root, cat.BUNDLE, "team", deprecated=True)

    catalog = cat.build_catalog(root)

    assert catalog[(cat.SKILL, "vendored")].deprecated is True
    assert catalog[(cat.SKILL, "local")].deprecated is True
    assert catalog[(cat.SKILL, "current")].deprecated is False
    assert catalog[(cat.SKILL, "unmarked")].deprecated is False
    assert catalog[(cat.AGENT, "planner")].deprecated is True
    assert catalog[(cat.BUNDLE, "team")].deprecated is True


@pytest.mark.parametrize("kind", [cat.SKILL, cat.AGENT, cat.BUNDLE])
@pytest.mark.parametrize("value", ["yes", 1, {"since": "2026-11-13"}])
def test_a_non_boolean_flag_refuses_the_catalog(tmp_path, kind, value):
    root = tmp_path / "catalog"
    register(root, kind, "old", deprecated=value)

    with pytest.raises(ValueError, match="malformed deprecated"):
        cat.build_catalog(root)


def test_list_marks_a_deprecated_row_and_keeps_it_visible(tmp_path, monkeypatch, capsys):
    workspace(tmp_path, monkeypatch)

    assert cli.main(["list", "--type", "skill"]) == errors.OK
    lines = capsys.readouterr().out.splitlines()
    assert any(" old" in line and "(deprecated)" in line for line in lines)
    assert not any(" fresh" in line and "(deprecated)" in line for line in lines)

    assert cli.main(["list", "--type", "skill", "--json"]) == errors.OK
    rows = {row["name"]: row for row in json.loads(capsys.readouterr().out)}
    assert rows["old"]["deprecated"] is True
    assert rows["fresh"]["deprecated"] is False


def test_add_warns_on_stderr_and_still_adds(tmp_path, monkeypatch, capsys):
    _, project, _ = workspace(tmp_path, monkeypatch)
    initialise(project)

    assert cli.main(["add", "old", "--type", "skill"]) == errors.OK
    captured = capsys.readouterr()
    assert "skill 'old' is deprecated" in captured.err
    assert "deprecated" not in captured.out
    assert state.read_strict(project).skills == ("old",)


def test_add_of_a_current_artifact_warns_about_nothing(tmp_path, monkeypatch, capsys):
    _, project, _ = workspace(tmp_path, monkeypatch)
    initialise(project)

    assert cli.main(["add", "fresh", "--type", "skill"]) == errors.OK
    assert "deprecated" not in capsys.readouterr().err


def test_add_warns_for_a_deprecated_agent(tmp_path, monkeypatch, capsys):
    _, project, catalog = workspace(tmp_path, monkeypatch)
    write_agent(catalog, "planner", deprecated=True)
    initialise(project)

    assert cli.main(["add", "planner", "--type", "agent"]) == errors.OK
    assert "agent 'planner' is deprecated" in capsys.readouterr().err
    assert state.read_strict(project).agents == ("planner",)


def test_sync_still_links_a_deprecated_global_and_warns_off_stdout(tmp_path, monkeypatch, capsys):
    home, _, catalog = workspace(tmp_path, monkeypatch)
    register(catalog, cat.SKILL, "old", deprecated=True, **{"global": True})

    assert cli.main(["sync"]) == errors.OK
    captured = capsys.readouterr()
    assert (home / ".claude" / "skills" / "old").is_symlink()
    assert "skill 'old' is deprecated" in captured.err
    assert "deprecated" not in captured.out

    assert cli.main(["sync"]) == errors.OK
    assert ", 0 changes" in capsys.readouterr().out


def test_doctor_notes_a_deprecated_artifact_still_in_use(tmp_path, monkeypatch, capsys):
    _, project, _ = workspace(tmp_path, monkeypatch)
    initialise(project)
    assert cli.main(["add", "old", "--type", "skill"]) == errors.OK
    capsys.readouterr()

    assert cli.main(["doctor"]) == errors.OK
    out = capsys.readouterr().out
    assert "skill 'old': deprecated, still in project views" in out
    assert "0 problem(s)" in out


def test_doctor_is_silent_about_a_deprecated_artifact_nobody_uses(tmp_path, monkeypatch, capsys):
    _, project, _ = workspace(tmp_path, monkeypatch)
    initialise(project, skills=("fresh",))

    cli.main(["doctor"])
    assert "deprecated" not in capsys.readouterr().out
