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
    path.write_text(f"---\nname: {name}\ndescription: Fixture agent {name}.\n---\n")
    return path


def _write_bundle(root, name, *, agent=None, skill=None):
    directory = root / "bundles" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bundle.json").write_text(json.dumps({}))
    bundle_skill = _write_skill(directory, skill or name)
    bundle_agent = _write_agent(directory, agent or name)
    return directory, bundle_skill, bundle_agent


def _configure(home, harnesses=("claude", "pi"), pi_project=".pi/agents"):
    extra = {"pi": {"agents": {"project": pi_project}}} if "pi" in harnesses else {}
    machine = config.Config(tuple(sorted(harnesses)), extra)
    config.write(machine, home)
    return machine


def _assert_link(path, target):
    assert path.is_symlink()
    assert path.resolve() == target.resolve()


def test_add_and_remove_bundle_projects_skills_and_agents_to_both_harnesses(home, project, monkeypatch):
    root = _catalog(home)
    _, bundle_skill, bundle_agent = _write_bundle(root, "backend")
    machine = _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ()))
    monkeypatch.chdir(project)

    assert cli.main(["add", "backend", "--type", "bundle"]) == errors.OK

    manifest = state.read_strict(project)
    assert manifest.schema_version == state.V2_SCHEMA_VERSION
    assert manifest.bundles == ("backend",)
    assert manifest.skills == ()
    for harness_id in ("claude", "pi"):
        _assert_link(harnesses.skill_path(harness_id, "backend", home, project), bundle_skill)
    _assert_link(harnesses.agent_path("claude", "backend", home, project), bundle_agent)
    _assert_link(harnesses.agent_path("pi", "backend", home, project, machine), bundle_agent)

    assert cli.main(["remove", "backend", "--type", "bundle"]) == errors.OK

    manifest = state.read_strict(project)
    assert manifest.bundles == ()
    assert not harnesses.skill_path("claude", "backend", home, project).exists()
    assert not harnesses.skill_path("pi", "backend", home, project).exists()
    assert not harnesses.agent_path("claude", "backend", home, project).exists()
    assert not harnesses.agent_path("pi", "backend", home, project, machine).exists()


def test_type_agent_selects_only_the_standalone_root_agent_when_bundle_shares_the_name(home, project, monkeypatch):
    root = _catalog(home)
    root_agent = _write_agent(root, "backend")
    _write_bundle(root, "backend", agent="worker", skill="worker")
    machine = _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ()))
    monkeypatch.chdir(project)

    assert cli.main(["add", "backend", "--type", "agent"]) == errors.OK

    manifest = state.read_strict(project)
    assert manifest.agents == ("backend",)
    assert manifest.bundles == ()
    _assert_link(harnesses.agent_path("claude", "backend", home, project), root_agent)
    _assert_link(harnesses.agent_path("pi", "backend", home, project, machine), root_agent)
    assert not harnesses.skill_path("claude", "worker", home, project).exists()
    assert not harnesses.skill_path("pi", "worker", home, project).exists()


def test_missing_machine_config_refuses_agent_add_without_writing(home, project, monkeypatch):
    root = _catalog(home)
    _write_agent(root, "architect")
    state.write(project, state.Manifest(("claude",), ()))
    manifest_path = state.path_for(project)
    before = manifest_path.read_bytes()
    monkeypatch.chdir(project)

    assert cli.main(["add", "architect", "--type", "agent"]) == errors.NO_PROJECT

    assert manifest_path.read_bytes() == before
    assert not harnesses.agent_path("claude", "architect", home, project).exists()


def test_remove_agent_drops_intent_without_claiming_a_foreign_bundle_link(home, project, monkeypatch):
    root = _catalog(home)
    _write_agent(root, "architect")
    foreign = root / "bundles" / "other" / "agents" / "architect.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("---\nname: architect\n---\n")
    machine = _configure(home)
    state.write(
        project,
        state.Manifest(
            ("claude", "pi"),
            (),
            schema_version=state.V2_SCHEMA_VERSION,
            agents=("architect",),
        ),
    )
    claude_link = harnesses.agent_path("claude", "architect", home, project)
    pi_link = harnesses.agent_path("pi", "architect", home, project, machine)
    claude_link.parent.mkdir(parents=True)
    pi_link.parent.mkdir(parents=True)
    claude_link.symlink_to(foreign)
    pi_link.symlink_to(foreign)
    monkeypatch.chdir(project)

    assert cli.main(["remove", "architect", "--type", "agent"]) == errors.OK

    assert state.read_strict(project).agents == ()
    _assert_link(claude_link, foreign)
    _assert_link(pi_link, foreign)


def test_bundle_add_promotes_same_named_legacy_plugin_only_after_success(home, project, monkeypatch):
    root = _catalog(home)
    _write_skill(root, "review")
    _write_bundle(root, "backend")
    _configure(home, harnesses=("claude",))
    state.write(
        project,
        state.Manifest(
            ("claude",),
            ("review",),
            legacy={
                "agents": {"old-agent": "direct"},
                "plugins": {"backend": "direct", "data": "direct"},
            },
        ),
    )
    monkeypatch.chdir(project)

    assert cli.main(["add", "backend", "--type", "bundle"]) == errors.OK

    manifest = state.read_strict(project)
    assert manifest.schema_version == state.V2_SCHEMA_VERSION
    assert manifest.skills == ("review",)
    assert manifest.bundles == ("backend",)
    assert manifest.legacy == {
        "agents": {"old-agent": "direct"},
        "plugins": {"data": "direct"},
    }


def test_remove_missing_bundle_refuses_instead_of_abandoning_managed_links(home, project, monkeypatch):
    root = _catalog(home)
    directory, _, _ = _write_bundle(root, "backend")
    _configure(home, harnesses=("claude",))
    state.write(project, state.Manifest(("claude",), ()))
    monkeypatch.chdir(project)
    assert cli.main(["add", "backend", "--type", "bundle"]) == errors.OK
    manifest_before = state.path_for(project).read_bytes()
    agent_link = harnesses.agent_path("claude", "backend", home, project)
    directory.joinpath("bundle.json").unlink()

    assert cli.main(["remove", "backend", "--type", "bundle"]) == errors.DRIFT
    assert state.path_for(project).read_bytes() == manifest_before
    assert agent_link.is_symlink()


def test_plugin_type_refuses_with_migration_guidance(home, project, monkeypatch, capsys):
    _catalog(home)
    state.write(project, state.Manifest(("claude",), ()))
    monkeypatch.chdir(project)

    assert cli.main(["add", "backend", "--type", "plugin"]) == errors.USAGE

    message = capsys.readouterr().err
    assert "Migrate" in message
    assert "--type bundle" in message


def test_bundle_global_and_group_refuse_without_acting(home, project, monkeypatch):
    root = _catalog(home)
    _write_bundle(root, "backend")
    _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ()))
    before = state.path_for(project).read_bytes()
    monkeypatch.chdir(project)

    assert cli.main(["add", "backend", "--type", "bundle", "--global"]) == errors.WRONG_SCOPE
    assert cli.main(["add", "--group", "backend", "--type", "bundle"]) == errors.USAGE

    assert state.path_for(project).read_bytes() == before
    assert not (project / ".claude" / "agents").exists()
    assert not (project / ".pi" / "agents").exists()


def test_list_plugin_type_refuses_instead_of_printing_skills(kit):
    result = kit("list", "--type", "plugin")

    assert result.returncode == errors.USAGE
    assert "--type skill" in result.stderr
