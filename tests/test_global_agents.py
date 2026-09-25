import json

from kura import catalog as cat
from kura import cli, config, errors, harnesses, state, views


def _catalog(home):
    root = config.catalog_path(home)
    if root.is_symlink():
        root.unlink()
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    return root


def _write_skill(root, name, *, groups=(), dependencies=()):
    directory = root / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"
    )
    return directory


def _write_agent(root, name, *, groups=("global",), dependencies=()):
    directory = root / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"
    )
    (root / "agent-registry.json").write_text(
        json.dumps(
            {
                "local": [
                    {
                        "name": name,
                        "groups": list(groups),
                        "dependencies": list(dependencies),
                    }
                ]
            }
        )
    )
    return path


def _write_skill_registry(root, entries):
    (root / "skill-registry.json").write_text(json.dumps({"local": entries}))


def _machine(harnesses=("claude", "pi"), *, pi_global="~/.pi/agent/agents"):
    extra = {"pi": {"agents": {"global": pi_global}}} if pi_global is not None else {}
    return config.Config(tuple(sorted(harnesses)), extra)


def test_pi_agent_conflict_prevents_claude_global_writes(home, capsys):
    root = _catalog(home)
    helper = _write_skill(root, "helper")
    agent = _write_agent(root, "architect", dependencies=("helper",))
    config.write(_machine(), home)
    machine = config.read(home)
    conflict = harnesses.agent_path("pi", "architect", home, machine_config=machine)
    conflict.mkdir(parents=True)

    assert cli.main(["sync"]) == errors.DRIFT

    assert "Pi" in capsys.readouterr().err
    assert not harnesses.agent_path("claude", "architect", home).exists()
    assert not harnesses.skill_path("claude", "helper", home).exists()
    assert conflict.is_dir()
    assert agent.is_file() and helper.is_dir()


def test_empty_global_agent_policy_refuses_to_prune_managed_agent_links(home, capsys):
    root = _catalog(home)
    agent = _write_agent(root, "architect")
    config.write(_machine(("claude",), pi_global=None), home)

    assert cli.main(["sync"]) == errors.OK
    link = harnesses.agent_path("claude", "architect", home)
    assert link.is_symlink() and link.resolve() == agent

    _write_agent(root, "architect", groups=())

    assert cli.main(["sync"]) == errors.DRIFT

    assert link.is_symlink()
    assert "global agent metadata resolved to an empty set" in capsys.readouterr().err


def test_missing_pi_global_agent_path_refuses_entire_sync(home, capsys):
    root = _catalog(home)
    _write_agent(root, "architect")
    config.write(_machine(pi_global=None), home)

    assert cli.main(["sync"]) == errors.DRIFT

    assert "Pi: global agent path is not configured" in capsys.readouterr().err
    assert not harnesses.agent_path("claude", "architect", home).exists()


def test_config_removing_harness_prunes_only_root_catalog_agent_links(home):
    root = _catalog(home)
    agent = _write_agent(root, "architect")
    config.write(_machine(), home)

    assert cli.main(["sync"]) == errors.OK

    machine = config.read(home)
    pi_link = harnesses.agent_path("pi", "architect", home, machine_config=machine)
    assert pi_link.is_symlink() and pi_link.resolve() == agent
    bundle_source = root / "bundles" / "other" / "agents" / "foreign.md"
    bundle_source.parent.mkdir(parents=True)
    bundle_source.write_text("---\nname: foreign\n---\n")
    foreign_link = pi_link.parent / "foreign.md"
    foreign_link.symlink_to(bundle_source)

    assert cli.main(["config", "--harness", "claude", "--yes"]) == errors.OK

    assert config.read(home).global_harnesses == ("claude",)
    assert not pi_link.exists()
    assert foreign_link.is_symlink() and foreign_link.resolve() == bundle_source
    assert harnesses.agent_path("claude", "architect", home).is_symlink()


def test_config_refuses_to_drop_pi_global_agent_view_when_old_path_is_unknown(home):
    root = _catalog(home)
    _write_agent(root, "architect")
    config.write(_machine(pi_global=None), home)

    assert cli.main(["config", "--harness", "claude", "--yes"]) == errors.DRIFT

    assert config.read(home).global_harnesses == ("claude", "pi")
    assert not harnesses.agent_path("claude", "architect", home).exists()


def test_skill_only_global_sync_does_not_require_pi_agent_configuration(home):
    root = _catalog(home)
    skill = _write_skill(root, "global-tool", groups=("global",))
    _write_skill_registry(root, [{"name": "global-tool", "groups": ["global"]}])
    config.write(_machine(pi_global=None), home)

    assert cli.main(["sync"]) == errors.OK

    for harness_id in ("claude", "pi"):
        link = harnesses.skill_path(harness_id, "global-tool", home)
        assert link.is_symlink() and link.resolve() == skill


def test_sync_type_skill_does_not_touch_global_agent_links(home):
    root = _catalog(home)
    _write_agent(root, "architect")
    config.write(_machine(("claude",), pi_global=None), home)

    assert cli.main(["sync", "--type", "skill"]) == errors.OK
    agent_link = harnesses.agent_path("claude", "architect", home)
    assert not agent_link.exists()

    assert cli.main(["sync"]) == errors.OK
    assert agent_link.is_symlink()
    assert cli.main(["sync", "--type", "skill"]) == errors.OK
    assert agent_link.is_symlink()


def test_project_bundle_uses_current_global_agent_without_duplicate_local_link(home, project):
    root = _catalog(home)
    agent = _write_agent(root, "architect")
    bundle = root / "bundles" / "backend"
    (bundle / "agents").mkdir(parents=True)
    (bundle / "agents" / "backend.md").write_text("---\nname: backend\ndescription: Backend.\n---\n")
    _write_skill(bundle, "backend-tool")
    (bundle / "bundle.json").write_text(json.dumps({"requires": {"agents": ["architect"]}}))
    global_link = harnesses.agent_path("claude", "architect", home)
    global_link.parent.mkdir(parents=True)
    global_link.symlink_to(agent)
    catalog = cat.build_catalog(root)
    declaration = state.Manifest(("claude",), (), schema_version=state.V2_SCHEMA_VERSION, bundles=("backend",))

    plan = views.project_plan(catalog, root, home, project, state.Manifest(("claude",), ()), declaration, global_harnesses=("claude",))

    assert not plan.refused
    assert not any(action.path == harnesses.agent_path("claude", "architect", home, project) for action in plan.actions)
    assert any(action.path == harnesses.agent_path("claude", "backend", home, project) for action in plan.actions)


def test_project_view_treats_global_agent_skill_dependencies_as_global(home, project):
    root = _catalog(home)
    helper = _write_skill(root, "helper")
    _write_skill_registry(root, [{"name": "helper"}])
    _write_agent(root, "architect", dependencies=("helper",))
    harnesses.skill_path("claude", "helper", home).parent.mkdir(parents=True)
    harnesses.skill_path("claude", "helper", home).symlink_to(helper)
    catalog = cat.build_catalog(root)

    plan = views.project_plan(
        catalog,
        root,
        home,
        project,
        state.Manifest(("claude",), ()),
        state.Manifest(("claude",), ("helper",)),
        global_harnesses=("claude",),
    )

    assert not plan.refused
    assert not any(action.path == harnesses.skill_path("claude", "helper", home, project) for action in plan.actions)
