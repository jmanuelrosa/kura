"""Contracts shared by release automation, documentation, and packaging checks."""

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from kit_helpers import CATALOG, TOOL
from kura import config, errors, upstream
from kura.commands import pull


@pytest.mark.parametrize(
    "relative",
    [".github/workflows/release.yml", ".github/workflows/test.yml"],
)
def test_workflow_smoke_tests_use_the_supported_artifact_type(relative):
    workflow = (TOOL / relative).read_text()
    assert "list --type plugin" not in workflow
    assert "list --type skill" in workflow


@pytest.mark.parametrize(
    "relative",
    [".github/workflows/test.yml", ".github/workflows/security.yml"],
)
def test_default_branch_workflows_watch_main(relative):
    workflow = (TOOL / relative).read_text()
    assert "branches: [main]" in workflow
    assert "branches: [master]" not in workflow


def test_releasing_smoke_test_and_wording_use_skills():
    releasing = (TOOL / "RELEASING.md").read_text()
    assert "list --type plugin" not in releasing
    assert 'KURA_CATALOG="$PWD/tests/fixtures/catalog" "$tmp/kura" list --type skill' in releasing
    assert "The first must print the skill listing." in releasing


def _catalog_at(destination):
    return Path(shutil.copytree(CATALOG, destination))


def _args(command, names=()):
    return SimpleNamespace(command=command, type="skill", names=list(names))


def _unexpected_fetch(repo, branch, destination):
    raise AssertionError(f"unexpected fetch of {repo}")


def test_update_without_machine_configuration_refuses_before_mutating_fallback(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    fallback = _catalog_at(home / config.DEFAULT_CATALOG)
    skill = fallback / "skills" / "brainstorming" / "SKILL.md"
    registry = fallback / "skill-registry.json"
    before = skill.read_bytes(), registry.read_bytes()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv(config.ENV_CATALOG, raising=False)
    monkeypatch.setattr(upstream, "fetch", _unexpected_fetch)

    code = pull.run(_args("update"))

    assert code == errors.NO_PROJECT
    assert (skill.read_bytes(), registry.read_bytes()) == before
    assert "machine configuration does not exist" in capsys.readouterr().err.lower()


def test_outdated_uses_the_fallback_without_machine_configuration(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    _catalog_at(home / config.DEFAULT_CATALOG)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv(config.ENV_CATALOG, raising=False)
    monkeypatch.setattr(upstream, "fetch", _unexpected_fetch)

    code = pull.run(_args("outdated", ["coderabbit"]))

    assert code == errors.OK
    assert "coderabbit" in capsys.readouterr().out


def test_update_uses_catalog_override_after_validating_saved_configuration(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    saved = _catalog_at(tmp_path / "saved")
    override = _catalog_at(tmp_path / "override")
    skill = override / "skills" / "override-only" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: override-only\ndescription: Override fixture.\n---\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    config.write(config.Config(saved, ("claude",)), home)
    monkeypatch.setenv(config.ENV_CATALOG, str(override))
    monkeypatch.setattr(upstream, "fetch", _unexpected_fetch)

    code = pull.run(_args("update", ["override-only"]))

    assert code == errors.OK
    assert "override-only" in capsys.readouterr().out


def test_missing_fallback_catalog_names_the_environment_remedy(tmp_path, monkeypatch):
    monkeypatch.delenv(config.ENV_CATALOG, raising=False)
    with pytest.raises(config.Malformed) as raised:
        config.effective_catalog(None, tmp_path)
    assert config.ENV_CATALOG in str(raised.value)
    assert "kura init" in str(raised.value)


def test_packaging_check_uses_the_interpreter_stdlib_inventory():
    packaging_test = (TOOL / "tests" / "test_packaging.py").read_text()
    assert "sys.stdlib_module_names" in packaging_test
    assert "ast.parse" in packaging_test
    assert "third_party =" not in packaging_test
