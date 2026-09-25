import json

from kura import cli, config, errors, harnesses, state


def _catalog(home):
    root = home / ".config" / "kura" / "catalog"
    if root.is_symlink():
        root.unlink()
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    return root


def _write_skill(root, name):
    directory = root / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    return directory


def _write_agent(root, name):
    directory = root / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(f"---\nname: {name}\ndescription: {name}\n---\n")
    return path


def _write_bundle(root, name):
    directory = root / "bundles" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bundle.json").write_text(json.dumps({}))
    skill = _write_skill(directory, name)
    agent = _write_agent(directory, name)
    return skill, agent


def _configure(home):
    machine = config.Config(("claude", "pi"), {"pi": {"agents": {"project": ".pi/agents"}}})
    config.write(machine, home)
    return machine


def test_list_type_agent_shows_root_and_bundle_owned_agent_views(home, project, monkeypatch, capsys):
    root = _catalog(home)
    root_agent = _write_agent(root, "architect")
    _, bundle_agent = _write_bundle(root, "backend")
    machine = _configure(home)
    manifest = state.Manifest(("claude", "pi"), (), schema_version=state.V2_SCHEMA_VERSION, agents=("architect",), bundles=("backend",))
    state.write(project, manifest)
    harnesses.agent_path("claude", "architect", home, project).parent.mkdir(parents=True)
    harnesses.agent_path("claude", "architect", home, project).symlink_to(root_agent)
    harnesses.agent_path("pi", "architect", home, project, machine).parent.mkdir(parents=True)
    harnesses.agent_path("pi", "architect", home, project, machine).symlink_to(root_agent)
    harnesses.agent_path("claude", "backend", home, project).parent.mkdir(parents=True, exist_ok=True)
    harnesses.agent_path("claude", "backend", home, project).symlink_to(bundle_agent)
    harnesses.agent_path("pi", "backend", home, project, machine).parent.mkdir(parents=True, exist_ok=True)
    harnesses.agent_path("pi", "backend", home, project, machine).symlink_to(bundle_agent)
    monkeypatch.chdir(project)

    assert cli.main(["list", "--type", "agent", "--json"]) == errors.OK

    rows = json.loads(capsys.readouterr().out)
    assert [(row["name"], row["reason"], row["state"]) for row in rows] == [
        ("architect", "direct", "linked"),
        ("backend", "dep-of:backend", "linked"),
    ]
    assert rows[1]["views"]["pi"]["state"] == "linked"


def test_list_type_bundle_requires_all_member_links_current(home, project, monkeypatch, capsys):
    root = _catalog(home)
    bundle_skill, bundle_agent = _write_bundle(root, "backend")
    machine = _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), (), schema_version=state.V2_SCHEMA_VERSION, bundles=("backend",)))
    harnesses.skill_path("claude", "backend", home, project).parent.mkdir(parents=True)
    harnesses.skill_path("claude", "backend", home, project).symlink_to(bundle_skill)
    harnesses.skill_path("pi", "backend", home, project).parent.mkdir(parents=True)
    harnesses.skill_path("pi", "backend", home, project).symlink_to(bundle_skill)
    harnesses.agent_path("claude", "backend", home, project).parent.mkdir(parents=True, exist_ok=True)
    harnesses.agent_path("claude", "backend", home, project).symlink_to(bundle_agent)
    harnesses.agent_path("pi", "backend", home, project, machine).parent.mkdir(parents=True, exist_ok=True)
    harnesses.agent_path("pi", "backend", home, project, machine).symlink_to(bundle_agent)
    monkeypatch.chdir(project)

    assert cli.main(["list", "--type", "bundle", "--json"]) == errors.OK
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["state"] == "linked"

    harnesses.agent_path("pi", "backend", home, project, machine).unlink()
    assert cli.main(["list", "--type", "bundle", "--json"]) == errors.OK
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["state"] == "drift"
    assert rows[0]["views"]["pi agent backend"]["state"] == "missing"


def test_list_type_bundle_does_not_include_unrelated_project_intent(home, project, monkeypatch, capsys):
    root = _catalog(home)
    bundle_skill, bundle_agent = _write_bundle(root, "backend")
    _write_agent(root, "architect")
    machine = _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), (), schema_version=state.V2_SCHEMA_VERSION, agents=("architect",), bundles=("backend",)))
    for harness_id in ("claude", "pi"):
        skill = harnesses.skill_path(harness_id, "backend", home, project)
        skill.parent.mkdir(parents=True)
        skill.symlink_to(bundle_skill)
        agent = harnesses.agent_path(harness_id, "backend", home, project, machine)
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.symlink_to(bundle_agent)
    monkeypatch.chdir(project)

    assert cli.main(["list", "--type", "bundle", "--json"]) == errors.OK
    assert json.loads(capsys.readouterr().out)[0]["state"] == "linked"


def test_list_type_bundle_can_be_empty(home, project, monkeypatch, capsys):
    _catalog(home)
    monkeypatch.chdir(project)

    assert cli.main(["list", "--type", "bundle", "--json"]) == errors.OK
    assert json.loads(capsys.readouterr().out) == []


def test_list_type_agent_human_output_is_allowed(home, project, monkeypatch, capsys):
    root = _catalog(home)
    _write_agent(root, "architect")
    _configure(home)
    state.write(project, state.Manifest(("claude",), (), schema_version=state.V2_SCHEMA_VERSION, agents=("architect",)))
    monkeypatch.chdir(project)

    assert cli.main(["list", "--type", "agent"]) == errors.OK

    assert "Available agents" in capsys.readouterr().out
