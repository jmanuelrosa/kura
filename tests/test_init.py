import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kura import config, errors, harnesses, state
from kura.commands import init as init_command


class TTY:
    def isatty(self):
        return True


def skill(root, name):
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"
    )


def catalog_at(path, dependencies=False):
    path.mkdir(parents=True)
    (path / "skills").mkdir()
    if dependencies:
        skill(path, "review")
        skill(path, "helper")
        (path / "skill-registry.json").write_text(
            json.dumps(
                {
                    "local_skills": [
                        {"name": "review", "dependencies": ["helper"]},
                        {"name": "helper", "dependency_only": True},
                    ]
                }
            )
        )
    return path


def arguments(
    harnesses=("claude",),
    yes=True,
    dry_run=False,
    verbose=False,
):
    return SimpleNamespace(
        harnesses=list(harnesses),
        yes=yes,
        dry_run=dry_run,
        verbose=verbose,
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    catalog = catalog_at(config.catalog_path(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(project)
    config.write(config.Config(("claude",)), home)
    return home, project, catalog


@pytest.mark.parametrize(
    "agents_before,claude_before,agents_after,claude_after",
    [
        (None, None, b"# Project instructions\n", b"@AGENTS.md\n"),
        (b"agents\xffbytes\n", None, b"agents\xffbytes\n", b"@AGENTS.md\n"),
        (None, b"claude\xffbytes\n", None, b"claude\xffbytes\n"),
        (
            b"agents\xffbytes\n",
            b"claude\xffbytes\n",
            b"agents\xffbytes\n",
            b"claude\xffbytes\n",
        ),
    ],
)
def test_instruction_file_topologies_preserve_exact_bytes(
    setup,
    agents_before,
    claude_before,
    agents_after,
    claude_after,
):
    _, project, catalog = setup
    agents = project / "AGENTS.md"
    claude = project / "CLAUDE.md"
    if agents_before is not None:
        agents.write_bytes(agents_before)
    if claude_before is not None:
        claude.write_bytes(claude_before)

    assert init_command.run(arguments()) == errors.OK

    if agents_after is None:
        assert not agents.exists()
    else:
        assert agents.read_bytes() == agents_after
    if claude_after is None:
        assert not claude.exists()
    else:
        assert claude.read_bytes() == claude_after


@pytest.mark.parametrize("target_exists", [False, True], ids=("dangling", "foreign"))
def test_instruction_symlinks_that_would_be_written_refuse(setup, tmp_path, target_exists, capsys):
    _, project, catalog = setup
    (project / "AGENTS.md").write_bytes(b"keep agents\n")
    target = tmp_path / "instruction-target.md"
    if target_exists:
        target.write_bytes(b"foreign\n")
    (project / "CLAUDE.md").symlink_to(target)

    assert init_command.run(arguments()) == errors.DRIFT

    captured = capsys.readouterr()
    assert "Cannot write instruction file" in captured.err
    assert "symlink ->" in captured.err
    assert "Replace it with a regular file or remove it" in captured.err
    assert (project / "AGENTS.md").read_bytes() == b"keep agents\n"
    assert (project / "CLAUDE.md").is_symlink()
    assert not state.path_for(project).exists()


def test_non_regular_instruction_destination_refuses(setup, capsys):
    _, project, catalog = setup
    (project / "AGENTS.md").write_bytes(b"keep agents\n")
    (project / "CLAUDE.md").mkdir()

    assert init_command.run(arguments()) == errors.DRIFT

    captured = capsys.readouterr()
    assert "is not a regular file" in captured.err
    assert (project / "AGENTS.md").read_bytes() == b"keep agents\n"
    assert (project / "CLAUDE.md").is_dir()
    assert not state.path_for(project).exists()


def test_existing_catalog_without_skills_refuses(setup, capsys):
    home, project, catalog = setup
    (catalog / "skills").rmdir()
    config_before = config.path_for(home).read_bytes()

    assert init_command.run(arguments()) == errors.DRIFT

    assert "has no skills/ directory" in capsys.readouterr().err
    assert config.path_for(home).read_bytes() == config_before
    assert not state.path_for(project).exists()


def test_missing_fixed_catalog_is_actionable_drift(setup, capsys):
    home, project, catalog = setup
    (catalog / "skills").rmdir()
    catalog.rmdir()
    config.write(config.Config(("claude",)), home)

    assert init_command.run(arguments()) == errors.DRIFT

    captured = capsys.readouterr()
    assert "Cannot resolve the catalog" in captured.err
    assert str(config.catalog_path(home)) in captured.err
    assert not state.path_for(project).exists()


def test_conflicting_root_and_legacy_manifests_refuse_with_semantic_difference(setup, capsys):
    home, project, catalog = setup
    skill(catalog, "review")
    skill(catalog, "helper")
    config.write(config.Config(("claude",)), home)
    state.write(project, state.Manifest(("claude",), ("review",)))
    root_before = state.path_for(project).read_bytes()
    legacy = state.legacy_path_for(project)
    legacy.parent.mkdir(exist_ok=True)
    legacy.write_text(json.dumps({"installed": {"skills": {"helper": "direct"}}}))
    legacy_before = legacy.read_bytes()

    assert init_command.run(arguments()) == errors.DRIFT

    captured = capsys.readouterr()
    assert "Cannot migrate project state" in captured.err
    assert "only in root: review" in captured.err
    assert "only in legacy: helper" in captured.err
    assert "so their declarations agree" in captured.err
    assert state.path_for(project).read_bytes() == root_before
    assert legacy.read_bytes() == legacy_before


@pytest.mark.parametrize(
    "entry_kind,expected",
    [
        ("real", "rogue: real path"),
        ("foreign", "rogue: foreign symlink ->"),
        ("undeclared", "rogue: catalog-backed but not declared or derived"),
    ],
)
def test_unsafe_legacy_pi_bridge_entries_refuse(setup, tmp_path, entry_kind, expected, capsys):
    home, project, catalog = setup
    skill(catalog, "review")
    skill(catalog, "rogue")
    config.write(config.Config(("claude", "pi")), home)
    claude_root = harnesses.project_skill_root(project, "claude")
    claude_root.mkdir(parents=True)
    (claude_root / "review").symlink_to(catalog / "skills" / "review")
    rogue = claude_root / "rogue"
    if entry_kind == "real":
        rogue.mkdir()
    elif entry_kind == "foreign":
        foreign = tmp_path / "foreign-skill"
        foreign.mkdir()
        rogue.symlink_to(foreign)
    else:
        rogue.symlink_to(catalog / "skills" / "rogue")
    pi_root = harnesses.project_skill_root(project, "pi")
    pi_root.parent.mkdir()
    pi_root.symlink_to(Path("..") / ".claude" / "skills")
    legacy = state.legacy_path_for(project)
    legacy.write_text(json.dumps({"installed": {"skills": {"review": "direct"}}}))
    legacy_before = legacy.read_bytes()

    assert init_command.run(
        arguments(harnesses=("claude", "pi"))
    ) == errors.DRIFT

    captured = capsys.readouterr()
    assert "Cannot replace .agents/skills" in captured.err
    assert expected in captured.err
    assert "No files were changed" in captured.err
    assert pi_root.is_symlink()
    assert legacy.read_bytes() == legacy_before
    assert not state.path_for(project).exists()


def write_legacy_plan(project):
    legacy = state.legacy_path_for(project)
    legacy.parent.mkdir(exist_ok=True)
    legacy.write_text(
        json.dumps(
            {
                "installed": {
                    "skills": {"review": "direct", "helper": "dep-of:review"},
                    "agents": {"reviewer": "direct"},
                    "plugins": {"backend": "dep-of:review"},
                }
            }
        )
    )
    return legacy


def test_dry_run_renders_the_complete_summary_and_writes_nothing(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    catalog = catalog_at(config.catalog_path(home), dependencies=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(project)
    config.write(config.Config(("claude", "pi")), home)
    config_before = config.path_for(home).read_bytes()
    legacy = write_legacy_plan(project)
    legacy_before = legacy.read_bytes()

    assert init_command.run(
        arguments(harnesses=("claude", "pi"), dry_run=True)
    ) == errors.OK

    output = capsys.readouterr().out
    assert "Instructions\n  + AGENTS.md\n  + CLAUDE.md importing AGENTS.md" in output
    assert "Migration\n  1 direct skill retained" in output
    assert "1 dependency row will be re-derived" in output
    assert "2 legacy agent/plugin records preserved" in output
    assert "Native views\n  4 links to create" in output
    assert "State\n  Catalog: use" in output
    assert "Project manifest: create" in output
    assert "Skill details" not in output
    assert "Nothing written (--dry-run)" in output
    assert legacy.read_bytes() == legacy_before
    assert config.path_for(home).read_bytes() == config_before
    assert not state.path_for(project).exists()
    assert not (project / "AGENTS.md").exists()
    assert not (project / "CLAUDE.md").exists()
    assert not harnesses.project_skill_root(project, "pi").exists()


def test_verbose_plan_enumerates_every_conversion_and_destination(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    catalog = catalog_at(config.catalog_path(home), dependencies=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(project)
    config.write(config.Config(("claude", "pi")), home)
    legacy = write_legacy_plan(project)

    assert init_command.run(
        arguments(
            harnesses=("claude", "pi"),
            dry_run=True,
            verbose=True,
        )
    ) == errors.OK

    output = capsys.readouterr().out
    assert "Skill details" in output
    assert "+ review: direct, persisted in kura.json" in output
    assert "· helper: derived, not persisted" in output
    assert "Migration details" in output
    assert "skill helper: dep-of:review, dropped and re-derived" in output
    assert "agent reviewer: direct, preserved as legacy state" in output
    assert "plugin backend: dep-of:review, preserved as legacy state" in output
    assert "Native view details" in output
    for harness_name in ("Claude Code", "Pi"):
        for name in ("helper", "review"):
            assert f"create: {harness_name} '{name}'" in output
    assert "State details" in output
    assert f"write: project manifest at {state.path_for(project)}" in output
    assert f"delete after success: legacy manifest at {legacy}" in output


def test_interactive_negative_cancels_without_writes(setup, monkeypatch, capsys):
    home, project, catalog = setup
    monkeypatch.setattr(init_command.sys, "stdin", TTY())
    monkeypatch.setattr("builtins.input", lambda prompt: "no")

    config_before = config.path_for(home).read_bytes()

    assert init_command.run(arguments(yes=False)) == errors.OK

    assert "Initialization cancelled; nothing was changed" in capsys.readouterr().out
    assert config.path_for(home).read_bytes() == config_before
    assert not state.path_for(project).exists()
    assert not (project / "AGENTS.md").exists()
    assert not (project / "CLAUDE.md").exists()


def test_interactive_eof_refuses_without_writes(setup, monkeypatch, capsys):
    home, project, catalog = setup
    monkeypatch.setattr(init_command.sys, "stdin", TTY())

    def end_input(prompt):
        raise EOFError

    config_before = config.path_for(home).read_bytes()
    monkeypatch.setattr("builtins.input", end_input)

    assert init_command.run(arguments(yes=False)) == errors.USAGE

    assert "Input ended before confirmation; nothing was changed" in capsys.readouterr().err
    assert config.path_for(home).read_bytes() == config_before
    assert not state.path_for(project).exists()
    assert not (project / "AGENTS.md").exists()
    assert not (project / "CLAUDE.md").exists()
