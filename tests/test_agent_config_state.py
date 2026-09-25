import json

import pytest

from kura import config, state


def machine_config(pi_agents):
    data = {
        "schemaVersion": 1,
        "globalHarnesses": ["claude"],
        "pi": {"agents": pi_agents, "future": True},
        "futureTop": {"value": 1},
    }
    return data


def test_pi_agent_paths_validate_and_preserve_unknown_fields(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    parsed = config.parse(
        machine_config(
            {
                "global": "~/.pi/agent/agents",
                "project": ".pi/agents",
                "futureScope": {"path": "later"},
            }
        ),
        home,
    )

    assert parsed.pi_agent_global == "~/.pi/agent/agents"
    assert parsed.pi_agent_project == ".pi/agents"
    dumped = json.loads(config.dump(parsed))
    assert dumped["pi"]["agents"]["futureScope"] == {"path": "later"}
    assert dumped["pi"]["future"] is True
    assert dumped["futureTop"] == {"value": 1}


@pytest.mark.parametrize(
    "agents,match",
    [
        ({"global": "../agents"}, "global"),
        ({"global": "~/../outside"}, "under HOME"),
        ({"global": "/tmp/outside"}, "under HOME"),
        ({"global": 1}, "global"),
        ({"global": "~"}, "under HOME"),
        ({"global": "~/.claude/agents"}, "overlaps"),
        ({"global": "~/.agents/skills"}, "overlaps"),
        ({"project": "/absolute"}, "relative"),
        ({"project": "."}, "escape"),
        ({"project": ".claude/agents"}, "overlaps"),
        ({"project": ".agents/skills"}, "overlaps"),
        ({"project": "../agents"}, "escape"),
        ({"project": ".pi/../agents"}, "escape"),
        ({"project": ""}, "project"),
    ],
)
def test_pi_agent_path_validation_rejects_malformed_values(tmp_path, agents, match):
    home = tmp_path / "home"
    home.mkdir()

    with pytest.raises(config.Malformed, match=match):
        config.parse(machine_config(agents), home)


def v1_manifest():
    return {
        "schemaVersion": 1,
        "harnesses": ["claude"],
        "skills": ["review"],
        "legacy": {
            "agents": {"old-agent": "direct"},
            "plugins": {"old-plugin": "dep-of:review"},
        },
    }


def test_manifest_v1_parse_and_dump_preserve_legacy_without_activating_it():
    manifest = state.parse(v1_manifest())

    assert manifest.skills == ("review",)
    assert manifest.agents == ()
    assert manifest.bundles == ()
    assert manifest.legacy == {
        "agents": {"old-agent": "direct"},
        "plugins": {"old-plugin": "dep-of:review"},
    }
    dumped = json.loads(state.dump(manifest))
    assert dumped["schemaVersion"] == 1
    assert "agents" not in dumped
    assert "bundles" not in dumped


def test_manifest_v2_parse_and_dump_active_agents_and_bundles():
    manifest = state.parse(
        {
            "schemaVersion": 2,
            "harnesses": ["claude", "pi"],
            "skills": ["review"],
            "agents": ["architect"],
            "bundles": ["backend"],
            "legacy": {"plugins": {"backend": "direct"}},
        }
    )

    assert manifest.agents == ("architect",)
    assert manifest.bundles == ("backend",)
    assert manifest.legacy == {"plugins": {"backend": "direct"}}
    assert json.loads(state.dump(manifest)) == {
        "schemaVersion": 2,
        "harnesses": ["claude", "pi"],
        "skills": ["review"],
        "agents": ["architect"],
        "bundles": ["backend"],
        "legacy": {"plugins": {"backend": "direct"}},
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"agents": ["architect", "architect"], "bundles": []},
        {"agents": ["z", "a"], "bundles": []},
        {"agents": [], "bundles": [""]},
        {"agents": [], "bundles": ["z", "a"]},
        {"bundles": []},
        {"agents": []},
    ],
)
def test_manifest_v2_rejects_missing_or_malformed_active_collections(patch):
    data = {
        "schemaVersion": 2,
        "harnesses": ["claude"],
        "skills": [],
        "agents": [],
        "bundles": [],
    }
    for key, value in patch.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    if "agents" not in patch and patch == {"bundles": []}:
        data.pop("agents")
    if "bundles" not in patch and patch == {"agents": []}:
        data.pop("bundles")

    with pytest.raises(state.Malformed):
        state.parse(data)


def test_manifest_rejects_unsafe_agent_and_bundle_names():
    for field in ("agents", "bundles"):
        data = {"schemaVersion": 2, "harnesses": ["claude"], "skills": [], "agents": [], "bundles": []}
        data[field] = ["../outside"]
        with pytest.raises(state.Malformed, match="unsafe name"):
            state.parse(data)


def test_manifest_upgrade_writes_version_two_for_new_intent():
    original = state.parse(v1_manifest())
    upgraded = state.upgrade(original, agents=("z", "architect", "z"), bundles=("backend",))

    dumped = json.loads(state.dump(upgraded))
    assert dumped["schemaVersion"] == 2
    assert dumped["agents"] == ["architect", "z"]
    assert dumped["bundles"] == ["backend"]
    assert dumped["legacy"] == v1_manifest()["legacy"]
