import json

from kura import cli, config, errors, state


def write_skill(root, name):
    source = root / "skills" / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n")


def write_agent(root, name, directory=None, declared=None):
    directory = root / "agents" if directory is None else directory
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(
        f"---\nname: {declared or name}\ndescription: {name}\n---\n\n# {name}\n"
    )
    return path


def configure(home, harnesses=("claude", "pi"), pi=True):
    extra = {}
    if pi:
        extra = {"pi": {"agents": {"global": "~/.pi/agent/agents", "project": ".pi/agents"}}}
    config.write(config.Config(harnesses, extra), home)


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
    catalog.mkdir(parents=True)
    (catalog / "skills").mkdir()
    return home, project, catalog


def test_doctor_uses_machine_config_for_pi_agent_views(tmp_path, monkeypatch, capsys):
    home, project, catalog = workspace(tmp_path, monkeypatch)
    write_skill(catalog, "helper")
    write_agent(catalog, "architect")
    configure(home)
    state.write(
        project,
        state.Manifest(("pi",), (), schema_version=state.V2_SCHEMA_VERSION, agents=("architect",)),
    )
    root = project / ".pi" / "agents"
    root.mkdir(parents=True)
    (root / "architect.md").symlink_to(catalog / "agents" / "architect.md")

    assert cli.main(["doctor"]) == errors.OK
    captured = capsys.readouterr()
    assert "Pi view: architect: create required" not in captured.out
    assert "Pi agent discovery" in captured.out
    assert captured.err == ""


def test_doctor_reports_agent_missing_and_path_drift(tmp_path, monkeypatch, capsys):
    home, project, catalog = workspace(tmp_path, monkeypatch)
    write_agent(catalog, "architect")
    configure(home, ("claude",))
    state.write(
        project,
        state.Manifest(
            ("claude",),
            (),
            schema_version=state.V2_SCHEMA_VERSION,
            agents=("architect", "ghost"),
        ),
    )
    root = project / ".claude" / "agents"
    root.mkdir(parents=True)
    old = catalog / "agents" / "old.md"
    old.write_text("---\nname: old\ndescription: old\n---\n")
    (root / "architect.md").symlink_to(old)

    assert cli.main(["doctor"]) == errors.DRIFT
    captured = capsys.readouterr()
    assert "agent 'ghost' is missing from the catalog" in captured.out
    assert "Claude Code view: architect: relink required" in captured.out
    assert captured.err == ""


def test_doctor_reports_global_agent_missing_config(tmp_path, monkeypatch, capsys):
    home, project, catalog = workspace(tmp_path, monkeypatch)
    write_agent(catalog, "architect")
    (catalog / "agent-registry.json").write_text(
        json.dumps({"local": [{"name": "architect", "groups": ["global"]}]})
    )
    configure(home, ("pi",), pi=False)

    assert cli.main(["doctor"]) == errors.DRIFT
    captured = capsys.readouterr()
    assert "global agent path is not configured" in captured.out
    assert captured.err == ""


def test_doctor_reports_selected_bundle_missing_and_invalid(tmp_path, monkeypatch, capsys):
    home, project, catalog = workspace(tmp_path, monkeypatch)
    bundle = catalog / "bundles" / "broken"
    (bundle / "agents").mkdir(parents=True)
    (bundle / "skills" / "broken-skill").mkdir(parents=True)
    (bundle / "bundle.json").write_text("{}")
    write_agent(catalog, "broken-agent", bundle / "agents", declared="wrong")
    (bundle / "skills" / "broken-skill" / "SKILL.md").write_text(
        "---\nname: broken-skill\ndescription: broken\n---\n"
    )
    configure(home, ("claude",))
    state.write(
        project,
        state.Manifest(
            ("claude",),
            (),
            schema_version=state.V2_SCHEMA_VERSION,
            bundles=("broken", "missing"),
        ),
    )

    assert cli.main(["doctor"]) == errors.DRIFT
    captured = capsys.readouterr()
    assert "bundle 'missing' is missing from the catalog" in captured.out
    assert "bundle 'broken'" in captured.out
    assert "agent name 'broken-agent', source name 'wrong'" in captured.out
    assert captured.err == ""


def test_doctor_reports_invalid_catalog_agent_and_bundle(tmp_path, monkeypatch, capsys):
    home, project, catalog = workspace(tmp_path, monkeypatch)
    write_agent(catalog, "architect", declared="wrong")
    bundle = catalog / "bundles" / "empty"
    bundle.mkdir(parents=True)
    (bundle / "bundle.json").write_text("{}")
    configure(home, ("claude",))

    assert cli.main(["doctor"]) == errors.DRIFT
    captured = capsys.readouterr()
    assert "agent 'architect'" in captured.out
    assert "source name 'wrong'" in captured.out
    assert "bundle 'empty'" in captured.out
    assert "must contain at least one agent and one skill" in captured.out
    assert captured.err == ""
