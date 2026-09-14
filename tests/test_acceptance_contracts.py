import json
from pathlib import Path

import pytest

from kura import cli, config, errors, harnesses, projects, state, views
from kura.commands import common


def add_skill(catalog, name):
    directory = catalog / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"
    )


def make_catalog(path, global_skill=False):
    path.mkdir()
    (path / "skills").mkdir()
    add_skill(path, "review")
    groups = ["global"] if global_skill else []
    (path / "skill-registry.json").write_text(
        json.dumps({"local_skills": [{"name": "review", "groups": groups}]})
    )
    return path


@pytest.fixture
def environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    catalog = make_catalog(tmp_path / "catalog")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv(config.ENV_CATALOG, raising=False)
    monkeypatch.chdir(project)
    config.write(config.Config(catalog, ("claude", "pi")), home)
    return home, project, catalog


@pytest.mark.parametrize(
    "change",
    [
        {"schemaVersion": 2},
        {"harnesses": ["unknown"]},
        {"harnesses": ["claude", "claude"]},
        {"skills": ["review", "review"]},
        {"skills": "review"},
        {"legacy": []},
    ],
)
def test_manifest_rejects_every_invalid_contract(change):
    data = {
        "schemaVersion": 1,
        "harnesses": ["claude"],
        "skills": ["review"],
    }
    data.update(change)
    with pytest.raises(state.Malformed):
        state.parse(data)


def test_repeated_add_repairs_a_missing_selected_view(environment):
    home, project, catalog = environment
    state.write(project, state.Manifest(("claude", "pi"), ("review",)))
    claude = harnesses.skill_path("claude", "review", home, project)
    claude.parent.mkdir(parents=True)
    claude.symlink_to(catalog / "skills" / "review")

    assert cli.main(["add", "review", "--type", "skill"]) == errors.OK
    assert harnesses.skill_path("pi", "review", home, project).is_symlink()


def test_converge_repairs_a_missing_selected_view(environment):
    home, project, catalog = environment
    state.write(project, state.Manifest(("claude", "pi"), ("review",)))
    claude = harnesses.skill_path("claude", "review", home, project)
    claude.parent.mkdir(parents=True)
    claude.symlink_to(catalog / "skills" / "review")

    assert cli.main(["converge", "--type", "skill"]) == errors.OK
    assert harnesses.skill_path("pi", "review", home, project).is_symlink()


def test_converge_deletes_only_redundant_managed_project_links(environment):
    home, project, catalog = environment
    registry = json.loads((catalog / "skill-registry.json").read_text())
    registry["local_skills"][0]["groups"] = ["global"]
    (catalog / "skill-registry.json").write_text(json.dumps(registry))
    state.write(project, state.Manifest(("claude", "pi"), ("review",)))
    for harness_id in ("claude", "pi"):
        global_link = harnesses.skill_path(harness_id, "review", home)
        global_link.parent.mkdir(parents=True)
        global_link.symlink_to(catalog / "skills" / "review")
        project_link = harnesses.skill_path(harness_id, "review", home, project)
        project_link.parent.mkdir(parents=True)
        project_link.symlink_to(catalog / "skills" / "review")
    foreign = project / ".claude" / "skills" / "foreign"
    foreign_target = project / "foreign-target"
    foreign_target.mkdir()
    foreign.symlink_to(foreign_target)

    assert cli.main(["converge", "--type", "skill"]) == errors.OK
    for harness_id in ("claude", "pi"):
        assert not harnesses.skill_path(harness_id, "review", home, project).exists()
    assert foreign.is_symlink()


@pytest.mark.parametrize(
    "argv",
    [
        ["add", "review", "--type", "skill"],
        ["remove", "review", "--type", "skill"],
        ["scout"],
        ["adopt"],
        ["restore"],
        ["converge", "--type", "skill"],
        ["trust"],
    ],
)
def test_project_commands_refuse_an_uninitialized_exact_cwd(environment, argv):
    assert cli.main(argv) == errors.NO_PROJECT


def test_read_only_commands_remain_useful_without_a_manifest(environment):
    assert cli.main(["list", "--type", "skill", "--json"]) == errors.OK
    assert cli.main(["doctor"]) == errors.OK


def test_global_add_and_remove_need_no_project_manifest(environment):
    home, _, _ = environment
    assert cli.main(["add", "review", "--type", "skill", "--global"]) == errors.OK
    for harness_id in ("claude", "pi"):
        assert harnesses.skill_path(harness_id, "review", home).is_symlink()
    assert cli.main(["remove", "review", "--type", "skill", "--global"]) == errors.OK


def test_project_scan_prunes_hidden_vcs_dependency_cache_and_vendor_trees(tmp_path):
    visible = tmp_path / "work" / "deep" / "project"
    visible.mkdir(parents=True)
    state.write(visible, state.Manifest(("claude",), ()))
    for name in (".hidden", ".git", ".cache", "node_modules", "vendor", "build"):
        hidden = tmp_path / name / "project"
        hidden.mkdir(parents=True)
        state.write(hidden, state.Manifest(("claude",), ()))

    assert projects.scan(tmp_path) == [visible]


def test_rerun_init_preserves_direct_skills_without_a_legacy_manifest(environment):
    home, project, catalog = environment
    state.write(project, state.Manifest(("claude", "pi"), ("review",)))

    assert cli.main(["init", "--harness", "claude", "--yes"]) == errors.OK
    declaration = state.read_strict(project)
    assert declaration.harnesses == ("claude",)
    assert declaration.skills == ("review",)
    assert harnesses.skill_path("claude", "review", home, project).resolve() == (
        catalog / "skills" / "review"
    )


@pytest.mark.parametrize("command", ["update", "outdated"])
def test_upstream_commands_refuse_malformed_registry_without_a_traceback(
    environment, command
):
    _, _, catalog = environment
    (catalog / "skill-registry.json").write_text("{bad")

    assert cli.main([command, "--type", "skill"]) == errors.DRIFT


def test_trust_dry_run_preflights_missing_claude_state(
    environment, monkeypatch
):
    _, project, _ = environment
    state.write(project, state.Manifest(("claude", "pi"), ()))
    monkeypatch.setattr(harnesses, "executable_available", lambda harness_id: True)

    assert cli.main(["trust", "--on", "--dry-run"]) == errors.USAGE


def test_converge_all_sends_manifest_failures_to_stderr(
    environment, tmp_path, capsys
):
    root = tmp_path / "projects"
    broken = root / "broken"
    broken.mkdir(parents=True)
    state.path_for(broken).write_text("{bad")

    assert cli.main(["converge", "--all", "--root", str(root)]) == errors.DRIFT
    captured = capsys.readouterr()
    assert "invalid" in captured.err
    assert "invalid" not in captured.out


def test_init_refuses_malformed_legacy_rows_without_deleting_them(environment):
    _, project, _ = environment
    legacy = state.legacy_path_for(project)
    legacy.parent.mkdir()
    legacy.write_text('{"installed":{"agents":{"reviewer":null}}}')
    before = legacy.read_bytes()

    assert cli.main(["init", "--harness", "claude", "--yes"]) == errors.DRIFT
    assert legacy.read_bytes() == before
    assert not state.path_for(project).exists()


def test_malformed_dependency_metadata_is_a_controlled_catalog_refusal(environment):
    _, project, catalog = environment
    state.write(project, state.Manifest(("claude",), ()))
    (catalog / "skill-registry.json").write_text(
        json.dumps(
            {
                "local_skills": [
                    {"name": "review", "dependencies": ["helper", 1]}
                ]
            }
        )
    )

    assert cli.main(["add", "review", "--type", "skill"]) == errors.DRIFT


def test_first_bootstrap_supports_a_symlinked_config_directory(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    config_target = tmp_path / "config-target"
    home.mkdir()
    project.mkdir()
    config_target.mkdir()
    (home / ".config").symlink_to(config_target)
    catalog = make_catalog(tmp_path / "bootstrap-catalog")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv(config.ENV_CATALOG, raising=False)
    monkeypatch.chdir(project)

    assert cli.main(
        [
            "init",
            "--harness",
            "claude",
            "--catalog",
            str(catalog),
            "--global-harness",
            "claude",
            "--yes",
        ]
    ) == errors.OK
    assert config.path_for(home).is_file()
    assert config.path_for(home).resolve().is_relative_to(config_target)


def test_config_can_recover_from_malformed_old_catalog_metadata(
    environment, tmp_path
):
    home, _, old_catalog = environment
    (old_catalog / "skill-registry.json").write_text("{bad")
    new_catalog = make_catalog(tmp_path / "replacement")

    assert cli.main(["config", "--catalog", str(new_catalog), "--yes"]) == errors.OK
    assert config.read(home).catalog == new_catalog


def test_state_actions_refuse_concurrent_configuration_and_manifest_changes(environment):
    home, project, catalog = environment
    original_config = config.read(home)
    config.write(
        config.Config(
            catalog,
            ("claude", "pi"),
            extra={"concurrent": True},
        ),
        home,
    )
    with pytest.raises(common.Refusal, match="changed after machine configuration"):
        common.config_action(home, original_config, original_config)

    original_manifest = state.Manifest(("claude",), ())
    state.write(project, original_manifest)
    state.write(project, state.Manifest(("pi",), ()))
    with pytest.raises(common.Refusal, match="changed after project state"):
        common.manifest_action(project, original_manifest, original_manifest)


def test_global_remove_preserves_a_foreign_link_swapped_after_preflight(
    environment, tmp_path, monkeypatch
):
    home, _, catalog = environment
    links = {}
    for harness_id in ("claude", "pi"):
        link = harnesses.skill_path(harness_id, "review", home)
        link.parent.mkdir(parents=True)
        link.symlink_to(catalog / "skills" / "review")
        links[harness_id] = link
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    original_classify = views.classify
    swapped = False

    def classify_with_swap(path, expected, roots):
        nonlocal swapped
        result = original_classify(path, expected, roots)
        if Path(path) == links["pi"] and not swapped:
            swapped = True
            Path(path).unlink()
            Path(path).symlink_to(foreign)
        return result

    monkeypatch.setattr(views, "classify", classify_with_swap)

    assert cli.main(["remove", "review", "--type", "skill", "--global"]) == errors.DRIFT
    assert links["claude"].resolve() == catalog / "skills" / "review"
    assert links["pi"].is_symlink() and links["pi"].resolve() == foreign


def test_sync_and_converge_keep_the_zero_changes_automation_marker(environment, capsys):
    _, project, _ = environment
    state.write(project, state.Manifest(("claude", "pi"), ()))

    assert cli.main(["sync"]) == errors.OK
    sync_output = capsys.readouterr().out
    assert ", 0 changes" in sync_output

    assert cli.main(["converge", "--type", "skill"]) == errors.OK
    converge_output = capsys.readouterr().out
    assert ", 0 changes" in converge_output
