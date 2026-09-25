import json

from kura import catalog as cat
from kura import config, harnesses, state, views
from kura.transaction import Transaction


def write_skill(root, name):
    directory = root / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    return directory


def write_agent(root, name):
    directory = root / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(f"---\nname: {name}\ndescription: Fixture agent {name}.\n---\n")
    return path


def write_bundle(root, name):
    directory = root / "bundles" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bundle.json").write_text(json.dumps({}))
    skill = write_skill(directory, name)
    agent = write_agent(directory, name)
    return directory, skill, agent


def machine():
    return config.Config(
        ("claude", "pi"),
        {"pi": {"agents": {"project": ".pi/agents"}}},
    )


def test_project_plan_links_direct_and_bundle_sources_to_every_selected_view(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    catalog_root = tmp_path / "catalog"
    home.mkdir()
    project.mkdir()
    direct_skill = write_skill(catalog_root, "review")
    _, bundle_skill, bundle_agent = write_bundle(catalog_root, "backend")
    catalog = cat.build_catalog(catalog_root)
    old = state.Manifest(("claude", "pi"), ())
    new = state.Manifest(
        ("claude", "pi"),
        ("review",),
        schema_version=state.V2_SCHEMA_VERSION,
        bundles=("backend",),
    )

    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        old,
        new,
        machine_config=machine(),
    )

    assert not plan.refused
    Transaction().run(plan.ordered_actions())
    for harness_id in ("claude", "pi"):
        assert harnesses.skill_path(harness_id, "review", home, project).resolve() == direct_skill
        assert harnesses.skill_path(harness_id, "backend", home, project).resolve() == bundle_skill
    assert harnesses.agent_path("claude", "backend", home, project).resolve() == bundle_agent
    assert harnesses.agent_path("pi", "backend", home, project, machine()).resolve() == bundle_agent


def test_skill_only_pi_project_does_not_require_agent_configuration(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    catalog_root = tmp_path / "catalog"
    home.mkdir()
    project.mkdir()
    write_skill(catalog_root, "review")
    catalog = cat.build_catalog(catalog_root)

    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        state.Manifest(("pi",), ()),
        state.Manifest(("pi",), ("review",)),
    )

    assert not plan.refused
    assert {action.path for action in plan.actions} == {
        harnesses.skill_path("pi", "review", home, project)
    }


def test_pi_agent_view_requires_a_project_agent_path(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    catalog_root = tmp_path / "catalog"
    home.mkdir()
    project.mkdir()
    write_agent(catalog_root, "architect")
    catalog = cat.build_catalog(catalog_root)
    new = state.Manifest(
        ("claude", "pi"),
        (),
        schema_version=state.V2_SCHEMA_VERSION,
        agents=("architect",),
    )

    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        state.Manifest(("claude", "pi"), ()),
        new,
    )

    assert plan.refused
    assert any("Pi: project agent path is not configured" == item for item in plan.blocked)


def test_project_delete_removes_declared_bundle_agent_link(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    catalog_root = tmp_path / "catalog"
    home.mkdir()
    project.mkdir()
    _, _, bundle_agent = write_bundle(catalog_root, "backend")
    catalog = cat.build_catalog(catalog_root)
    link = harnesses.agent_path("claude", "backend", home, project)
    link.parent.mkdir(parents=True)
    link.symlink_to(bundle_agent)
    old = state.Manifest(
        ("claude",),
        (),
        schema_version=state.V2_SCHEMA_VERSION,
        bundles=("backend",),
    )

    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        old,
        state.Manifest(("claude",), (), schema_version=state.V2_SCHEMA_VERSION),
    )

    assert any(action.operation == "delete" and action.path == link for action in plan.actions)


def test_project_delete_does_not_claim_arbitrary_bundle_agent_links(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    catalog_root = tmp_path / "catalog"
    home.mkdir()
    project.mkdir()
    write_bundle(catalog_root, "backend")
    other = catalog_root / "bundles" / "other" / "agents"
    other.mkdir(parents=True)
    foreign_source = other / "backend.md"
    foreign_source.write_text("---\nname: backend\n---\n")
    catalog = cat.build_catalog(catalog_root)
    link = harnesses.agent_path("claude", "backend", home, project)
    link.parent.mkdir(parents=True)
    link.symlink_to(foreign_source)
    old = state.Manifest(
        ("claude",),
        (),
        schema_version=state.V2_SCHEMA_VERSION,
        bundles=("backend",),
    )

    plan = views.project_plan(
        catalog,
        catalog_root,
        home,
        project,
        old,
        state.Manifest(("claude",), (), schema_version=state.V2_SCHEMA_VERSION),
    )

    assert not any(action.operation == "delete" and action.path == link for action in plan.actions)
