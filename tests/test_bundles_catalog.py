import json

import pytest

from kura import catalog as cat


def write_skill(root, name, *, frontmatter=None):
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    block = frontmatter or f"name: {name}"
    (directory / "SKILL.md").write_text(f"---\n{block}\n---\n")
    return directory


def write_agent(root, name, *, frontmatter=None):
    directory = root / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    block = frontmatter or f"name: {name}\ndescription: Fixture agent {name}."
    path = directory / f"{name}.md"
    path.write_text(f"---\n{block}\n---\n")
    return path


def write_bundle(root, name, *, marker=None, agent=None, skill=None):
    directory = root / "bundles" / name
    directory.mkdir(parents=True)
    (directory / "bundle.json").write_text(json.dumps(marker if marker is not None else {}))
    write_agent(directory, agent or name)
    write_skill(directory, skill or name)
    return directory


def test_bundle_discovery_is_selectable_only_through_bundle_resolution(tmp_path):
    root = tmp_path / "catalog"
    write_bundle(root, "backend")

    catalog = cat.build_catalog(root)

    assert cat.bundles(catalog)["backend"].name == "backend"
    assert cat.get(catalog, cat.SKILL, "backend") is None
    assert cat.get(catalog, cat.AGENT, "backend") is None
    assert cat.resolve(catalog, ["backend"]).missing_direct == ("backend",)

    resolution = cat.bundle_resolution(catalog, ["backend"])

    assert resolution.complete
    assert resolution.bundles == ("backend",)
    assert resolution.skills == ("backend",)
    assert resolution.agents == ("backend",)


def test_bundle_requires_root_artifacts_and_derives_skill_closure(tmp_path):
    root = tmp_path / "catalog"
    write_skill(root, "leaf")
    write_skill(root, "shared")
    write_agent(root, "architect")
    (root / "skill-registry.json").write_text(
        json.dumps({"local": [{"name": "shared", "dependencies": ["leaf"]}]})
    )
    (root / "agent-registry.json").write_text(
        json.dumps({"local": [{"name": "architect", "dependencies": ["shared"]}]})
    )
    write_bundle(
        root,
        "backend",
        marker={"requires": {"skills": ["shared"], "agents": ["architect"]}},
    )

    resolution = cat.bundle_resolution(cat.build_catalog(root), ["backend"])

    assert resolution.complete
    assert resolution.skills == ("backend", "leaf", "shared")
    assert resolution.agents == ("architect", "backend")


def test_bundle_resolution_reports_missing_bundle_requirements(tmp_path):
    root = tmp_path / "catalog"
    write_bundle(
        root,
        "backend",
        marker={"requires": {"skills": ["missing-skill"], "agents": ["missing-agent"]}},
    )

    resolution = cat.bundle_resolution(cat.build_catalog(root), ["backend"])

    assert not resolution.complete
    assert resolution.missing_skills == ("missing-skill",)
    assert resolution.missing_agents == ("missing-agent",)


def test_bundle_requires_at_least_one_agent_and_one_skill(tmp_path):
    root = tmp_path / "catalog"
    directory = root / "bundles" / "backend"
    directory.mkdir(parents=True)
    (directory / "bundle.json").write_text("{}")
    write_agent(directory, "backend")

    bundle = cat.bundles(cat.build_catalog(root))["backend"]
    resolution = cat.bundle_resolution(cat.build_catalog(root), ["backend"])

    assert "at least one agent and one skill" in bundle.catalog_error
    assert not resolution.complete
    assert resolution.invalid == (("backend", bundle.catalog_error),)


def test_bundle_rejects_unsafe_external_reference(tmp_path):
    root = tmp_path / "catalog"
    write_bundle(root, "backend", marker={"requires": {"skills": ["../escape"]}})

    with pytest.raises(ValueError, match="one safe path component"):
        cat.build_catalog(root)


def test_bundle_rejects_frontmatter_name_mismatch(tmp_path):
    root = tmp_path / "catalog"
    directory = write_bundle(root, "backend")
    (directory / "agents" / "backend.md").write_text("---\nname: other\ndescription: Other.\n---\n")

    resolution = cat.bundle_resolution(cat.build_catalog(root), ["backend"])

    assert not resolution.complete
    assert "source name 'other'" in resolution.invalid[0][1]


def test_bundle_rejects_source_escape(tmp_path):
    root = tmp_path / "catalog"
    outside = tmp_path / "outside.md"
    outside.write_text("---\nname: backend\ndescription: Backend.\n---\n")
    directory = write_bundle(root, "backend")
    agent = directory / "agents" / "backend.md"
    agent.unlink()
    agent.symlink_to(outside)

    resolution = cat.bundle_resolution(cat.build_catalog(root), ["backend"])

    assert not resolution.complete
    assert "resolves outside" in resolution.invalid[0][1]


def test_bundle_rejects_marker_escape(tmp_path):
    root = tmp_path / "catalog"
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    directory = write_bundle(root, "backend")
    marker = directory / "bundle.json"
    marker.unlink()
    marker.symlink_to(outside)

    resolution = cat.bundle_resolution(cat.build_catalog(root), ["backend"])

    assert not resolution.complete
    assert "resolves outside" in resolution.invalid[0][1]


def test_registered_root_agent_rejects_missing_name_or_description_when_selected(tmp_path):
    root = tmp_path / "catalog"
    agent = write_agent(root, "architect")
    write_bundle(root, "backend", marker={"requires": {"agents": ["architect"]}})
    (root / "agent-registry.json").write_text(json.dumps({"local": [{"name": "architect"}]}))

    agent.write_text("---\ndescription: Architect.\n---\n")
    missing_name = cat.bundle_resolution(cat.build_catalog(root), ["backend"])

    agent.write_text("---\nname: architect\n---\n")
    missing_description = cat.bundle_resolution(cat.build_catalog(root), ["backend"])

    assert not missing_name.complete
    assert "missing frontmatter name" in missing_name.invalid[0][1]
    assert not missing_description.complete
    assert "missing frontmatter description" in missing_description.invalid[0][1]


def test_bundle_rejects_malformed_requires(tmp_path):
    root = tmp_path / "catalog"
    write_bundle(root, "a-null-requires", marker={"requires": None})
    write_bundle(root, "b-null-skill", marker={"requires": {"skills": None}})
    write_bundle(root, "c-duplicate", marker={"requires": {"agents": ["architect", "architect"]}})

    with pytest.raises(ValueError, match="requires must be an object"):
        cat.build_catalog(root)

    (root / "bundles" / "a-null-requires" / "bundle.json").write_text("{}")
    with pytest.raises(ValueError, match="requires.skills must be a list"):
        cat.build_catalog(root)

    (root / "bundles" / "b-null-skill" / "bundle.json").write_text("{}")
    with pytest.raises(ValueError, match="duplicate names"):
        cat.build_catalog(root)


def test_bundle_rejects_unsafe_source_names(tmp_path):
    root = tmp_path / "catalog"
    directory = write_bundle(root, "backend")
    write_agent(directory, "bad\\name")

    with pytest.raises(ValueError, match="one safe path component"):
        cat.build_catalog(root)


def test_agent_registry_rejects_retired_keys(tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "agent-registry.json").write_text(json.dumps({"local_agents": []}))

    with pytest.raises(ValueError, match="retired"):
        cat.build_catalog(root)


def test_bundle_and_direct_root_name_collision_is_invalid(tmp_path):
    root = tmp_path / "catalog"
    write_skill(root, "backend")
    write_bundle(root, "backend")

    resolution = cat.bundle_resolution(
        cat.build_catalog(root), ["backend"], direct_skills=["backend"]
    )

    assert not resolution.complete
    assert resolution.invalid[0][0] == "backend"
    assert "conflicts" in resolution.invalid[0][1]


def test_root_agent_can_share_name_with_bundle_without_key_collision(tmp_path):
    root = tmp_path / "catalog"
    write_agent(root, "backend")
    write_bundle(root, "backend", agent="worker", skill="worker")

    catalog = cat.build_catalog(root)
    resolution = cat.bundle_resolution(catalog, ["backend"], direct_agents=["backend"])

    assert cat.get(catalog, cat.AGENT, "backend").source == root / "agents" / "backend.md"
    assert cat.bundles(catalog)["backend"].agents[0].name == "worker"
    assert resolution.complete
    assert resolution.agents == ("backend", "worker")


def test_claude_plugin_manifest_is_not_a_bundle(tmp_path):
    root = tmp_path / "catalog"
    manifest = root / "plugins" / "backend" / ".claude-plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(
        json.dumps({"name": "backend", "description": "Backend", "version": "1.0.0"})
    )

    catalog = cat.build_catalog(root)

    assert cat.bundles(catalog) == {}
    assert cat.bundle_resolution(catalog, ["backend"]).missing_bundles == ("backend",)
