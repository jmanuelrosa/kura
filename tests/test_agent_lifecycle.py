import json
from types import SimpleNamespace

from kura import catalog as cat
from kura import cli, config, errors, harnesses, state
from kura.commands import adopt as adopt_command


def _catalog(home):
    root = config.catalog_path(home)
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


def _machine(*, pi_agents=True):
    extra = {"pi": {"agents": {"project": ".pi/agents"}}} if pi_agents else {}
    return config.Config(("claude", "pi"), extra)


def _assert_link(path, target):
    assert path.is_symlink()
    assert path.resolve() == target.resolve()


def test_reinit_preserves_v2_agents_and_bundles_with_pi_agent_paths(home, project, monkeypatch):
    root = _catalog(home)
    root_agent = _write_agent(root, "architect")
    bundle_skill, bundle_agent = _write_bundle(root, "backend")
    machine = _machine()
    config.write(machine, home)
    state.write(
        project,
        state.Manifest(
            ("claude", "pi"),
            (),
            schema_version=state.V2_SCHEMA_VERSION,
            agents=("architect",),
            bundles=("backend",),
        ),
    )
    monkeypatch.chdir(project)

    assert cli.main(["init", "--harness", "claude", "--harness", "pi", "--yes"]) == errors.OK

    manifest = state.read_strict(project)
    assert manifest.schema_version == state.V2_SCHEMA_VERSION
    assert manifest.agents == ("architect",)
    assert manifest.bundles == ("backend",)
    for harness_id in ("claude", "pi"):
        _assert_link(harnesses.skill_path(harness_id, "backend", home, project), bundle_skill)
    _assert_link(harnesses.agent_path("claude", "architect", home, project), root_agent)
    _assert_link(harnesses.agent_path("claude", "backend", home, project), bundle_agent)
    _assert_link(harnesses.agent_path("pi", "architect", home, project, machine), root_agent)
    _assert_link(harnesses.agent_path("pi", "backend", home, project, machine), bundle_agent)


def test_skill_only_project_does_not_require_pi_agent_path_for_lifecycle_commands(home, project, monkeypatch):
    root = _catalog(home)
    skill = _write_skill(root, "review")
    config.write(_machine(pi_agents=False), home)
    state.write(project, state.Manifest(("claude", "pi"), ("review",)))
    monkeypatch.chdir(project)

    assert cli.main(["init", "--harness", "claude", "--harness", "pi", "--yes"]) == errors.OK
    harnesses.skill_path("pi", "review", home, project).unlink()
    assert cli.main(["restore"]) == errors.OK
    _assert_link(harnesses.skill_path("pi", "review", home, project), skill)
    harnesses.skill_path("claude", "review", home, project).unlink()
    assert cli.main(["converge"]) == errors.OK
    _assert_link(harnesses.skill_path("claude", "review", home, project), skill)


def test_restore_adds_bundle_and_root_agent_views_and_refuses_conflicts(home, project, monkeypatch):
    root = _catalog(home)
    root_agent = _write_agent(root, "architect")
    bundle_skill, bundle_agent = _write_bundle(root, "backend")
    machine = _machine()
    config.write(machine, home)
    manifest = state.Manifest(
        ("claude", "pi"),
        (),
        schema_version=state.V2_SCHEMA_VERSION,
        agents=("architect",),
        bundles=("backend",),
    )
    state.write(project, manifest)
    conflict = harnesses.agent_path("claude", "architect", home, project)
    conflict.mkdir(parents=True)
    monkeypatch.chdir(project)

    assert cli.main(["restore"]) == errors.DRIFT
    assert conflict.is_dir()
    assert not harnesses.agent_path("pi", "architect", home, project, machine).exists()

    conflict.rmdir()
    assert cli.main(["restore"]) == errors.OK

    _assert_link(harnesses.agent_path("claude", "architect", home, project), root_agent)
    _assert_link(harnesses.agent_path("pi", "architect", home, project, machine), root_agent)
    _assert_link(harnesses.agent_path("claude", "backend", home, project), bundle_agent)
    _assert_link(harnesses.agent_path("pi", "backend", home, project, machine), bundle_agent)
    for harness_id in ("claude", "pi"):
        _assert_link(harnesses.skill_path(harness_id, "backend", home, project), bundle_skill)
    assert state.read_strict(project) == manifest


def test_converge_all_preflights_agent_conflicts_before_any_project_write(home, tmp_path, monkeypatch):
    root = _catalog(home)
    _write_agent(root, "architect")
    machine = _machine()
    config.write(machine, home)
    projects = tmp_path / "projects"
    first = projects / "first"
    second = projects / "second"
    first.mkdir(parents=True)
    second.mkdir()
    declaration = state.Manifest(
        ("claude", "pi"),
        (),
        schema_version=state.V2_SCHEMA_VERSION,
        agents=("architect",),
    )
    state.write(first, declaration)
    state.write(second, declaration)
    harnesses.agent_path("pi", "architect", home, second, machine).mkdir(parents=True)
    monkeypatch.chdir(projects)

    assert cli.main(["converge", "--all", "--root", str(projects)]) == errors.DRIFT

    assert not harnesses.agent_path("claude", "architect", home, first).exists()
    assert not harnesses.agent_path("pi", "architect", home, first, machine).exists()


def test_adopt_default_skill_flow_preserves_active_agent_and_bundle_intent(home, project, monkeypatch):
    root = _catalog(home)
    review = _write_skill(root, "review")
    _write_agent(root, "architect")
    _write_bundle(root, "backend")
    config.write(config.Config(("claude",)), home)
    state.write(
        project,
        state.Manifest(
            ("claude",),
            (),
            schema_version=state.V2_SCHEMA_VERSION,
            agents=("architect",),
            bundles=("backend",),
        ),
    )
    link = harnesses.skill_path("claude", "review", home, project)
    link.parent.mkdir(parents=True)
    link.symlink_to(review)
    monkeypatch.chdir(project)

    assert cli.main(["adopt"]) == errors.OK

    manifest = state.read_strict(project)
    assert manifest.skills == ("review",)
    assert manifest.agents == ("architect",)
    assert manifest.bundles == ("backend",)


def test_adopt_non_skill_type_refuses_with_agent_guidance(capsys):
    assert adopt_command.run(SimpleNamespace(type=cat.AGENT, dry_run=True)) == errors.USAGE

    message = capsys.readouterr().err
    assert "adopts project skills only" in message
    assert "--type agent" in message


def test_state_merge_keeps_active_v2_intent_and_legacy_rows_inert():
    root = state.Manifest(
        ("claude",),
        ("review",),
        {"plugins": {"old-plugin": "direct"}},
        state.V2_SCHEMA_VERSION,
        ("architect",),
        ("backend",),
    )
    migrated = state.migrated(
        {
            (cat.SKILL, "review"): "direct",
            (cat.AGENT, "legacy-agent"): "direct",
            (cat.PLUGIN, "legacy-plugin"): "direct",
        },
        ("claude",),
    )

    merged = state.merge(root, migrated)

    assert merged.schema_version == state.V2_SCHEMA_VERSION
    assert merged.agents == ("architect",)
    assert merged.bundles == ("backend",)
    assert merged.legacy == {
        "agents": {"legacy-agent": "direct"},
        "plugins": {"legacy-plugin": "direct", "old-plugin": "direct"},
    }
    assert migrated.agents == ()
    assert migrated.bundles == ()
    assert migrated.legacy["agents"] == {"legacy-agent": "direct"}
