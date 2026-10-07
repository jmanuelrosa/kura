import json

import pytest
from kit_helpers import register

from kura import catalog as cat
from kura import cli, config, errors, harnesses, state


def _catalog(home):
    root = home / ".config" / "kura" / "catalog"
    if root.is_symlink():
        root.unlink()
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    return root


def _write_skill(root, name, *, registered=True):
    directory = root / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    if registered:
        register(root, cat.SKILL, name)
    return directory


def _write_agent(root, name, *, registered=True):
    directory = root / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(f"---\nname: {name}\ndescription: Fixture agent {name}.\n---\n")
    if registered:
        register(root, cat.AGENT, name)
    return path


def _write_bundle(root, name, *, agent=None, skill=None):
    directory = root / "bundles" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bundle.json").write_text(json.dumps({}))
    bundle_skill = _write_skill(directory, skill or name, registered=False)
    bundle_agent = _write_agent(directory, agent or name, registered=False)
    register(root, cat.BUNDLE, name)
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


@pytest.mark.parametrize("selection", [["backend"], ["--group", "workflow"]])
def test_remove_missing_bundle_refuses_instead_of_abandoning_managed_links(home, project, monkeypatch, selection):
    root = _catalog(home)
    directory, _, _ = _write_bundle(root, "backend")
    register(root, cat.BUNDLE, "backend", groups=["workflow"])
    _configure(home, harnesses=("claude",))
    state.write(project, state.Manifest(("claude",), ()))
    monkeypatch.chdir(project)
    assert cli.main(["add", "backend", "--type", "bundle"]) == errors.OK
    manifest_before = state.path_for(project).read_bytes()
    agent_link = harnesses.agent_path("claude", "backend", home, project)
    directory.joinpath("bundle.json").unlink()

    assert cli.main(["remove", *selection, "--type", "bundle"]) == errors.DRIFT
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


@pytest.mark.parametrize("command", ["add", "remove"])
def test_bundle_global_refuses_without_acting(home, project, monkeypatch, command):
    root = _catalog(home)
    _write_bundle(root, "backend")
    _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ()))
    before = state.path_for(project).read_bytes()
    monkeypatch.chdir(project)

    assert cli.main([command, "--group", "backend", "--type", "bundle", "--global"]) == errors.WRONG_SCOPE

    assert state.path_for(project).read_bytes() == before
    assert not (project / ".claude" / "agents").exists()
    assert not (project / ".pi" / "agents").exists()


def test_bundle_group_add_and_remove_preserves_other_direct_intent(home, project, monkeypatch):
    root = _catalog(home)
    shared = _write_skill(root, "shared")
    root_agent = _write_agent(root, "architect")
    register(root, cat.SKILL, "shared", groups=["workflow"])
    register(root, cat.AGENT, "architect", groups=["workflow"])
    sources = {}
    for name in ("backend", "data", "other"):
        directory, skill, agent = _write_bundle(root, name)
        (directory / "bundle.json").write_text(json.dumps({"requires": {"skills": ["shared"]}}))
        register(root, cat.BUNDLE, name, groups=["workflow"] if name != "other" else [])
        sources[name] = (skill, agent)
    machine = _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ()))
    monkeypatch.chdir(project)
    assert cli.main(["add", "other", "--type", "bundle"]) == errors.OK

    assert cli.main(["add", "--group", "workflow", "--type", "bundle"]) == errors.OK

    manifest = state.read_strict(project)
    assert manifest.bundles == ("backend", "data", "other")
    assert manifest.skills == ()
    assert manifest.agents == ()
    for harness_id in machine.global_harnesses:
        for name, (skill, agent) in sources.items():
            _assert_link(harnesses.skill_path(harness_id, name, home, project), skill)
            _assert_link(harnesses.agent_path(harness_id, name, home, project, machine), agent)
        _assert_link(harnesses.skill_path(harness_id, "shared", home, project), shared)
        assert not harnesses.agent_path(harness_id, root_agent.stem, home, project, machine).exists()

    assert cli.main(["add", "--group", "workflow", "--type", "bundle"]) == errors.ALREADY
    assert cli.main(["remove", "--group", "workflow", "--type", "bundle"]) == errors.OK

    assert state.read_strict(project).bundles == ("other",)
    for harness_id in machine.global_harnesses:
        for name in ("backend", "data"):
            assert not harnesses.skill_path(harness_id, name, home, project).exists()
            assert not harnesses.agent_path(harness_id, name, home, project, machine).exists()
        _assert_link(harnesses.skill_path(harness_id, "shared", home, project), shared)
        _assert_link(harnesses.skill_path(harness_id, "other", home, project), sources["other"][0])
        _assert_link(harnesses.agent_path(harness_id, "other", home, project, machine), sources["other"][1])


def test_bundle_group_add_conflict_is_atomic_across_members_and_harnesses(home, project, monkeypatch):
    root = _catalog(home)
    for name in ("backend", "data"):
        _write_bundle(root, name)
        register(root, cat.BUNDLE, name, groups=["workflow"])
    machine = _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ()))
    before = state.path_for(project).read_bytes()
    collision = harnesses.agent_path("pi", "data", home, project, machine)
    collision.parent.mkdir(parents=True)
    collision.write_text("user-owned")
    monkeypatch.chdir(project)

    assert cli.main(["add", "--group", "workflow", "--type", "bundle"]) == errors.DRIFT

    assert state.path_for(project).read_bytes() == before
    assert collision.read_text() == "user-owned"
    assert not (project / ".claude").exists()
    assert not harnesses.skill_path("pi", "backend", home, project).exists()
    assert not harnesses.agent_path("pi", "backend", home, project, machine).exists()


@pytest.mark.parametrize("command", ["add", "remove"])
@pytest.mark.parametrize("names,group,expected", [
    ([], "unknown", errors.NOT_FOUND),
    (["backend"], "workflow", errors.USAGE),
])
def test_bundle_group_selection_refuses_without_writing(home, project, monkeypatch, command, names, group, expected):
    root = _catalog(home)
    _write_bundle(root, "backend")
    register(root, cat.BUNDLE, "backend", groups=["workflow"])
    _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ()))
    before = state.path_for(project).read_bytes()
    monkeypatch.chdir(project)

    assert cli.main([command, *names, "--group", group, "--type", "bundle"]) == expected

    assert state.path_for(project).read_bytes() == before
    assert not (project / ".claude").exists()
    assert not (project / ".pi").exists()


def test_bundle_group_remove_selects_only_configured_members(home, project, monkeypatch):
    root = _catalog(home)
    for name in ("backend", "data"):
        _write_bundle(root, name)
        register(root, cat.BUNDLE, name, groups=["workflow"])
    _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ()))
    monkeypatch.chdir(project)
    assert cli.main(["add", "backend", "--type", "bundle"]) == errors.OK

    assert cli.main(["remove", "--group", "workflow", "--type", "bundle"]) == errors.OK

    assert state.read_strict(project).bundles == ()
    before = state.path_for(project).read_bytes()
    assert cli.main(["remove", "--group", "workflow", "--type", "bundle"]) == errors.OK
    assert state.path_for(project).read_bytes() == before


@pytest.mark.parametrize("command", ["add", "remove", "list"])
def test_agent_group_cli_remains_unsupported(home, project, monkeypatch, command):
    _catalog(home)
    _configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ()))
    monkeypatch.chdir(project)

    assert cli.main([command, "--group", "workflow", "--type", "agent"]) == errors.USAGE


def test_list_plugin_type_refuses_instead_of_printing_skills(kit):
    result = kit("list", "--type", "plugin")

    assert result.returncode == errors.USAGE
    assert "Migrate" in result.stderr
    assert "--type bundle" in result.stderr


def test_help_does_not_advertise_the_legacy_plugin_type(kit):
    result = kit("list", "--help")

    assert "{skill,agent,bundle}" in result.stdout
    assert "plugin" not in result.stdout
