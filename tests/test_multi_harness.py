import json
import os
from pathlib import Path

import pytest

from kura import catalog as cat
from kura import cli, config, errors, harnesses, pi_trust, projects, state
from kura.transaction import Action, Failed, Transaction


def skill(catalog, name):
    directory = catalog / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"
    )


def catalog_at(path, metadata=None):
    path.mkdir()
    (path / "skills").mkdir()
    for name in ("review", "helper", "global-tool"):
        skill(path, name)
    if metadata is not None:
        (path / "skill-registry.json").write_text(json.dumps(metadata))
    return path


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    catalog = catalog_at(
        tmp_path / "catalog",
        {
            "local_skills": [
                {"name": "review", "dependencies": ["helper"], "groups": ["review"]},
                {"name": "helper", "dependency_only": True},
                {"name": "global-tool", "groups": ["global"]},
            ]
        },
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("KURA_CATALOG", raising=False)
    monkeypatch.chdir(project)
    return home, project, catalog


def initialize(catalog):
    return cli.main(
        [
            "init",
            "--harness",
            "claude",
            "--harness",
            "pi",
            "--catalog",
            str(catalog),
            "--global-harness",
            "claude",
            "--global-harness",
            "pi",
            "--yes",
        ]
    )


def test_machine_config_round_trip_preserves_unknown_fields(tmp_path):
    parsed = config.parse(
        {
            "schemaVersion": 1,
            "catalog": str(tmp_path),
            "globalHarnesses": ["claude", "pi"],
            "future": {"value": 1},
        }
    )
    assert config.loads(config.dump(parsed)).extra == {"future": {"value": 1}}


@pytest.mark.parametrize(
    "field,value",
    [
        ("schemaVersion", 2),
        ("catalog", "relative"),
        ("globalHarnesses", ["pi", "claude"]),
        ("globalHarnesses", ["claude", "claude"]),
        ("globalHarnesses", ["other"]),
    ],
)
def test_machine_config_rejects_invalid_contracts(tmp_path, field, value):
    data = {
        "schemaVersion": 1,
        "catalog": str(tmp_path),
        "globalHarnesses": ["claude"],
    }
    data[field] = value
    with pytest.raises(config.Malformed):
        config.parse(data)


def test_registry_free_skill_is_discovered(tmp_path):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "skills").mkdir()
    skill(catalog, "local")
    artifact = cat.get(cat.build_catalog(catalog), cat.SKILL, "local")
    assert artifact is not None
    assert artifact.metadata is False
    assert artifact.dependencies == ()


def test_manifest_is_versioned_sorted_and_declarative():
    declaration = state.parse(
        {
            "schemaVersion": 1,
            "harnesses": ["claude", "pi"],
            "skills": ["helper", "review"],
        }
    )
    assert declaration.skills == ("helper", "review")
    assert "dep-of" not in state.dump(declaration)


def test_init_and_add_create_independent_native_links(setup):
    home, project, catalog = setup
    assert initialize(catalog) == errors.OK
    assert cli.main(["add", "review", "--type", "skill"]) == errors.OK
    declaration = state.read_strict(project)
    assert declaration.skills == ("review",)
    for harness_id in ("claude", "pi"):
        review = harnesses.skill_path(harness_id, "review", home, project)
        helper = harnesses.skill_path(harness_id, "helper", home, project)
        assert review.is_symlink() and review.resolve() == catalog / "skills" / "review"
        assert helper.is_symlink() and helper.resolve() == catalog / "skills" / "helper"
    assert os.readlink(project / ".agents" / "skills" / "review") != str(
        project / ".claude" / "skills" / "review"
    )


def test_collision_in_one_harness_prevents_every_write(setup):
    home, project, catalog = setup
    assert initialize(catalog) == errors.OK
    collision = harnesses.skill_path("pi", "review", home, project)
    collision.mkdir(parents=True)
    before = state.path_for(project).read_bytes()
    assert cli.main(["add", "review", "--type", "skill"]) == errors.DRIFT
    assert not harnesses.skill_path("claude", "review", home, project).exists()
    assert state.path_for(project).read_bytes() == before


def test_remove_rederives_dependencies(setup):
    home, project, catalog = setup
    initialize(catalog)
    cli.main(["add", "review", "--type", "skill"])
    assert cli.main(["remove", "review", "--type", "skill"]) == errors.OK
    assert state.read_strict(project).skills == ()
    for harness_id in ("claude", "pi"):
        assert not harnesses.skill_path(harness_id, "review", home, project).exists()
        assert not harnesses.skill_path(harness_id, "helper", home, project).exists()


def test_sync_projects_global_policy_to_both_harnesses(setup):
    home, _, catalog = setup
    initialize(catalog)
    for harness_id in ("claude", "pi"):
        assert harnesses.skill_path(harness_id, "global-tool", home).resolve() == (
            catalog / "skills" / "global-tool"
        )
    assert cli.main(["sync"]) == errors.OK


def test_restore_fills_one_missing_view_without_deleting_extras(setup):
    home, project, catalog = setup
    initialize(catalog)
    cli.main(["add", "review", "--type", "skill"])
    harnesses.skill_path("pi", "review", home, project).unlink()
    extra = project / ".agents" / "skills" / "foreign"
    extra.symlink_to(tmp_path := project / "foreign-source")
    tmp_path.mkdir()
    assert cli.main(["restore"]) == errors.OK
    assert harnesses.skill_path("pi", "review", home, project).is_symlink()
    assert extra.is_symlink()


def test_catalog_move_retargets_global_and_current_project(setup, tmp_path):
    home, project, old_catalog = setup
    initialize(old_catalog)
    cli.main(["add", "review", "--type", "skill"])
    new_catalog = tmp_path / "new-catalog"
    new_catalog.mkdir()
    (new_catalog / "skills").mkdir()
    for name in ("review", "helper", "global-tool"):
        skill(new_catalog, name)
    (new_catalog / "skill-registry.json").write_bytes(
        (old_catalog / "skill-registry.json").read_bytes()
    )
    assert cli.main(["config", "--catalog", str(new_catalog), "--yes"]) == errors.OK
    assert harnesses.skill_path("pi", "review", home, project).resolve() == (
        new_catalog / "skills" / "review"
    )
    assert config.read(home).catalog == new_catalog


def test_harness_reconfiguration_uses_but_does_not_persist_catalog_override(setup, tmp_path, monkeypatch):
    home, project, saved_catalog = setup
    initialize(saved_catalog)
    override = tmp_path / "override"
    override.mkdir()
    (override / "skills").mkdir()
    for name in ("review", "helper", "global-tool"):
        skill(override, name)
    (override / "skill-registry.json").write_bytes(
        (saved_catalog / "skill-registry.json").read_bytes()
    )
    for harness_id in ("claude", "pi"):
        harnesses.skill_path(harness_id, "global-tool", home).unlink()
    monkeypatch.setenv("KURA_CATALOG", str(override))
    assert cli.main(["config", "--harness", "claude", "--yes"]) == errors.OK
    assert config.read(home).catalog == saved_catalog
    assert harnesses.skill_path("claude", "global-tool", home).resolve() == (
        override / "skills" / "global-tool"
    )


def test_project_scan_uses_root_manifests_without_depth_cap(tmp_path):
    project = tmp_path.joinpath(*[f"level-{index}" for index in range(8)])
    project.mkdir(parents=True)
    state.write(project, state.Manifest(("claude",), ()))
    assert projects.scan(tmp_path) == [project]
    hidden = tmp_path / ".hidden" / "project"
    hidden.mkdir(parents=True)
    state.write(hidden, state.Manifest(("claude",), ()))
    assert projects.scan(tmp_path) == [project]


def test_pi_lock_uses_the_native_lock_path(tmp_path):
    store = tmp_path / "trust.json"
    with pi_trust.lock(store):
        assert Path(str(store) + ".lock").is_dir()
        with pytest.raises(OSError):
            with pi_trust.lock(store):
                pass
    assert not Path(str(store) + ".lock").exists()


def test_transaction_restores_an_earlier_link_when_a_later_write_fails(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    first = tmp_path / "views" / "first"
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    transaction = Transaction()
    with pytest.raises(Failed):
        transaction.run(
            [
                Action("create", first, source),
                Action("write", blocked, data=b"new"),
            ]
        )
    assert not first.exists()
    assert blocked.is_dir()


def test_sync_refuses_to_prune_to_an_empty_global_set(setup):
    home, _, catalog = setup
    initialize(catalog)
    registry = catalog / "skill-registry.json"
    data = json.loads(registry.read_text())
    for entry in data["local_skills"]:
        entry["groups"] = [group for group in entry.get("groups", []) if group != "global"]
    registry.write_text(json.dumps(data))
    managed = harnesses.skill_path("claude", "global-tool", home)
    assert managed.is_symlink()
    assert cli.main(["sync"]) == errors.DRIFT
    assert managed.is_symlink()


def test_list_marks_partial_selected_views_as_drift(setup, capsys):
    home, project, catalog = setup
    initialize(catalog)
    cli.main(["add", "review", "--type", "skill"])
    harnesses.skill_path("pi", "review", home, project).unlink()
    capsys.readouterr()
    assert cli.main(["list", "--type", "skill", "--json"]) == errors.OK
    rows = {row["name"]: row for row in json.loads(capsys.readouterr().out)}
    assert rows["review"]["state"] == "drift"
    assert rows["review"]["views"] == {
        "claude": {"state": "linked"},
        "pi": {"state": "missing"},
    }


def test_project_add_requires_every_global_dependency_view(setup):
    home, project, catalog = setup
    initialize(catalog)
    data = json.loads((catalog / "skill-registry.json").read_text())
    review = next(entry for entry in data["local_skills"] if entry["name"] == "review")
    review["dependencies"].append("global-tool")
    (catalog / "skill-registry.json").write_text(json.dumps(data))
    harnesses.skill_path("pi", "global-tool", home).unlink()
    assert cli.main(["add", "review", "--type", "skill"]) == errors.DRIFT
    assert not harnesses.skill_path("claude", "review", home, project).exists()
    assert state.read_strict(project).skills == ()


def test_trust_updates_both_selected_native_stores(setup, monkeypatch):
    home, project, catalog = setup
    initialize(catalog)
    (home / ".claude.json").write_text('{"projects":{}}')
    monkeypatch.setattr(harnesses, "executable_available", lambda harness_id: True)
    assert cli.main(["trust", "--on"]) == errors.OK
    assert pi_trust.read_strict(pi_trust.store_path(home))[str(project.resolve())] is True
    claude = json.loads((home / ".claude.json").read_text())
    assert any(entry["hasTrustDialogAccepted"] for entry in claude["projects"].values())


def test_missing_dependency_content_blocks_every_project_write(setup):
    home, project, catalog = setup
    initialize(catalog)
    (catalog / "skills" / "helper").rename(catalog / "skills" / "helper-missing")
    before = state.path_for(project).read_bytes()
    assert cli.main(["add", "review", "--type", "skill"]) == errors.DRIFT
    assert state.path_for(project).read_bytes() == before
    assert not harnesses.skill_path("claude", "review", home, project).exists()


def test_converge_all_preflights_every_project_before_writing(setup, tmp_path):
    home, _, catalog = setup
    initialize(catalog)
    root = tmp_path / "projects"
    first = root / "a"
    second = root / "b"
    first.mkdir(parents=True)
    second.mkdir()
    declaration = state.Manifest(("claude", "pi"), ("review",))
    state.write(first, declaration)
    state.write(second, declaration)
    harnesses.skill_path("pi", "review", home, second).mkdir(parents=True)
    assert cli.main(["converge", "--all", "--root", str(root)]) == errors.DRIFT
    assert not harnesses.skill_path("claude", "review", home, first).exists()


def test_catalog_rejects_a_skill_source_escaping_its_root(setup, tmp_path):
    _, _, catalog = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: escape\n---\n")
    (catalog / "skills" / "escape").symlink_to(outside)
    artifact = cat.get(cat.build_catalog(catalog), cat.SKILL, "escape")
    assert "resolves outside" in artifact.catalog_error
    initialize(catalog)
    assert cli.main(["add", "escape", "--type", "skill"]) == errors.DRIFT


def test_config_can_repair_a_catalog_that_was_physically_moved(setup, tmp_path):
    home, project, old_catalog = setup
    initialize(old_catalog)
    cli.main(["add", "review", "--type", "skill"])
    new_catalog = tmp_path / "renamed-catalog"
    old_catalog.rename(new_catalog)
    assert cli.main(["config", "--catalog", str(new_catalog), "--yes"]) == errors.OK
    assert harnesses.skill_path("pi", "review", home, project).resolve() == (
        new_catalog / "skills" / "review"
    )


def test_catalog_move_never_treats_home_as_a_project(setup, tmp_path, monkeypatch):
    home, _, old_catalog = setup
    initialize(old_catalog)
    state.write(home, state.Manifest(("claude",), ("review",)))
    new_catalog = tmp_path / "new-home-catalog"
    new_catalog.mkdir()
    (new_catalog / "skills").mkdir()
    for name in ("review", "helper", "global-tool"):
        skill(new_catalog, name)
    (new_catalog / "skill-registry.json").write_bytes(
        (old_catalog / "skill-registry.json").read_bytes()
    )
    monkeypatch.chdir(home)
    assert cli.main(["config", "--catalog", str(new_catalog), "--yes"]) == errors.OK
    assert not (home / ".claude" / "skills" / "review").exists()


def test_state_merge_refuses_legacy_direct_intent_missing_from_root():
    root = state.Manifest(("claude",), ())
    legacy = state.Manifest(("claude",), ("review",))
    with pytest.raises(state.Malformed, match="disagree"):
        state.merge(root, legacy)


def test_pi_null_entry_does_not_shadow_parent_trust(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    store = {str(tmp_path.resolve()): True, str(child.resolve()): None}
    assert pi_trust.decided_by(store, child) == (str(tmp_path.resolve()), True)


def test_listing_keeps_missing_direct_intent_visible(setup, capsys):
    _, project, catalog = setup
    initialize(catalog)
    state.write(project, state.Manifest(("claude", "pi"), ("vanished",)))
    capsys.readouterr()
    assert cli.main(["list", "--type", "skill", "--json"]) == errors.OK
    rows = {row["name"]: row for row in json.loads(capsys.readouterr().out)}
    assert rows["vanished"]["state"] == "missing"


def test_doctor_reports_malformed_registry_without_a_traceback(setup):
    _, _, catalog = setup
    initialize(catalog)
    (catalog / "skill-registry.json").write_text("[]")
    assert cli.main(["doctor"]) == errors.DRIFT


def test_listing_includes_a_foreign_symlink_target(setup, tmp_path, capsys):
    home, project, catalog = setup
    initialize(catalog)
    cli.main(["add", "review", "--type", "skill"])
    link = harnesses.skill_path("pi", "review", home, project)
    link.unlink()
    foreign = tmp_path / "foreign-review"
    foreign.mkdir()
    link.symlink_to(foreign)
    capsys.readouterr()
    cli.main(["list", "--type", "skill", "--json"])
    rows = {row["name"]: row for row in json.loads(capsys.readouterr().out)}
    assert rows["review"]["views"]["pi"] == {
        "state": "foreign",
        "target": str(foreign),
    }


def test_trust_off_reports_inherited_claude_trust(setup, monkeypatch, capsys):
    home, project, catalog = setup
    initialize(catalog)
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "projects": {
                    str(project.parent): {"hasTrustDialogAccepted": True}
                }
            }
        )
    )
    monkeypatch.setattr(harnesses, "executable_available", lambda harness_id: True)
    capsys.readouterr()
    assert cli.main(["trust", "--off"]) == errors.OK
    assert "trust remains inherited" in capsys.readouterr().out


def test_scout_focus_add_uses_the_project_transaction(setup):
    home, project, catalog = setup
    initialize(catalog)
    assert cli.main(["scout", "--focus", "review", "--add"]) == errors.OK
    assert state.read_strict(project).skills == ("review",)
    assert harnesses.skill_path("pi", "review", home, project).is_symlink()


def test_adopt_infers_roots_and_fills_the_missing_harness(setup):
    home, project, catalog = setup
    initialize(catalog)
    claude_root = harnesses.project_skill_root(project, "claude")
    claude_root.mkdir(parents=True)
    for name in ("review", "helper"):
        (claude_root / name).symlink_to(catalog / "skills" / name)
    assert cli.main(["adopt"]) == errors.OK
    assert state.read_strict(project).skills == ("review",)
    assert harnesses.skill_path("pi", "review", home, project).is_symlink()
    assert harnesses.skill_path("pi", "helper", home, project).is_symlink()


def test_scratch_global_add_and_remove_touch_every_enabled_harness(setup):
    home, _, catalog = setup
    initialize(catalog)
    assert cli.main(["add", "review", "--type", "skill", "--global"]) == errors.OK
    for harness_id in ("claude", "pi"):
        assert harnesses.skill_path(harness_id, "review", home).is_symlink()
    assert cli.main(["remove", "review", "--type", "skill", "--global"]) == errors.OK
    for harness_id in ("claude", "pi"):
        assert not harnesses.skill_path(harness_id, "review", home).exists()


def test_init_and_config_dry_runs_write_nothing(setup, tmp_path):
    home, project, catalog = setup
    assert cli.main(
        [
            "init",
            "--harness",
            "claude",
            "--catalog",
            str(catalog),
            "--global-harness",
            "claude",
            "--dry-run",
        ]
    ) == errors.OK
    assert not state.path_for(project).exists()
    assert not config.path_for(home).exists()


def test_init_migrates_the_old_manifest_and_pi_directory_bridge(setup):
    home, project, catalog = setup
    claude_root = harnesses.project_skill_root(project, "claude")
    claude_root.mkdir(parents=True)
    (claude_root / "review").symlink_to(catalog / "skills" / "review")
    (claude_root / "helper").symlink_to(catalog / "skills" / "helper")
    pi_parent = project / ".agents"
    pi_parent.mkdir()
    (pi_parent / "skills").symlink_to(Path("..") / ".claude" / "skills")
    legacy = state.legacy_path_for(project)
    legacy.write_text(
        json.dumps(
            {
                "installed": {
                    "skills": {"review": "direct", "helper": "dep-of:review"},
                    "agents": {"reviewer": "direct"},
                }
            }
        )
    )
    assert initialize(catalog) == errors.OK
    declaration = state.read_strict(project)
    assert declaration.skills == ("review",)
    assert declaration.legacy == {"agents": {"reviewer": "direct"}}
    assert not legacy.exists()
    assert harnesses.project_skill_root(project, "pi").is_dir()
    assert (harnesses.project_skill_root(project, "pi") / "review").is_symlink()
