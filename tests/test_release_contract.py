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


def test_release_workflow_leaves_a_published_tag_unchanged():
    workflow = (TOOL / ".github/workflows/release.yml").read_text()
    assert 'gh release view "$TAG"' in workflow
    assert "leaving published assets unchanged" in workflow
    assert "--clobber" not in workflow


def test_releasing_smoke_test_and_wording_use_skills():
    releasing = (TOOL / "RELEASING.md").read_text()
    assert "list --type plugin" not in releasing
    assert 'ln -s "$PWD/tests/fixtures/catalog" "$tmp/home/.config/kura/catalog"' in releasing
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
    catalog = _catalog_at(config.catalog_path(home))
    skill = catalog / "skills" / "brainstorming" / "SKILL.md"
    registry = catalog / "skill-registry.json"
    before = skill.read_bytes(), registry.read_bytes()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(upstream, "fetch", _unexpected_fetch)

    code = pull.run(_args("update"))

    assert code == errors.NO_PROJECT
    assert (skill.read_bytes(), registry.read_bytes()) == before
    assert "machine configuration does not exist" in capsys.readouterr().err.lower()


def test_outdated_uses_the_fixed_catalog_without_machine_configuration(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    _catalog_at(config.catalog_path(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(upstream, "fetch", _unexpected_fetch)

    code = pull.run(_args("outdated", ["coderabbit"]))

    assert code == errors.OK
    assert "coderabbit" in capsys.readouterr().out


def test_missing_fixed_catalog_names_the_path_and_config_remedy(tmp_path):
    with pytest.raises(config.Malformed) as raised:
        config.effective_catalog(tmp_path)
    assert str(config.catalog_path(tmp_path)) in str(raised.value)
    assert "kura config" in str(raised.value)


def test_packaging_check_uses_the_interpreter_stdlib_inventory():
    packaging_test = (TOOL / "tests" / "test_packaging.py").read_text()
    assert "sys.stdlib_module_names" in packaging_test
    assert "ast.parse" in packaging_test
    assert "third_party =" not in packaging_test
