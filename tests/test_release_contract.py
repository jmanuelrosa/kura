"""Contracts shared by release automation, documentation, and packaging checks."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kit_helpers import CATALOG, SHIM, TOOL
from kura import config, errors, upstream
from kura.__version__ import __version__
from kura.commands import pull

RELEASE_WORKFLOW = yaml.load((TOOL / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader)

_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$"
)


def test_package_version_is_semver():
    assert _SEMVER.match(__version__), f"__version__ is not semver: {__version__!r}"


def test_version_flag_prints_the_package_version():
    result = subprocess.run(
        [sys.executable, str(SHIM), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == errors.OK
    assert result.stdout.strip() == f"kura {__version__}"
    assert result.stderr == ""


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


def test_the_release_job_runs_no_third_party_python():
    for step in RELEASE_WORKFLOW["jobs"]["release"]["steps"]:
        assert "pip install" not in step.get("run", "")
        assert "pytest" not in step.get("run", "")


@pytest.mark.parametrize(
    "relative",
    [".github/workflows/release.yml", ".github/workflows/test.yml"],
)
def test_workflows_install_test_dependencies_by_hash(relative):
    workflow = (TOOL / relative).read_text()
    assert "pip install --require-hashes -r requirements-test.txt" in workflow
    assert "pip install pytest" not in workflow


def _release_step(name, job="release"):
    return next(step for step in RELEASE_WORKFLOW["jobs"][job]["steps"] if step.get("name") == name)


def _stub_release_cli(tmp_path, name, source):
    executable = tmp_path / "bin" / name
    executable.parent.mkdir(exist_ok=True)
    executable.write_text(f"#!{sys.executable}\nimport json\nimport os\nimport sys\n{source}\n")
    executable.chmod(0o755)
    return executable


def _run_release_step(tmp_path, name, job="release", **env):
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", _release_step(name, job)["run"]],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}", **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_manual_release_authorization_precedes_write_permissions():
    version = RELEASE_WORKFLOW["on"]["workflow_dispatch"]["inputs"]["version"]
    assert version["required"] == "true"
    assert version["type"] == "string"
    assert RELEASE_WORKFLOW["on"]["push"]["tags"] == ["v*"]
    assert RELEASE_WORKFLOW["permissions"] == {"contents": "read"}
    assert RELEASE_WORKFLOW["jobs"]["test"]["needs"] == "authorize"
    assert "permissions" not in RELEASE_WORKFLOW["jobs"]["test"]
    assert RELEASE_WORKFLOW["jobs"]["release"]["needs"] == ["authorize", "test"]
    assert RELEASE_WORKFLOW["jobs"]["release"]["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert RELEASE_WORKFLOW["concurrency"]["cancel-in-progress"] == "false"
    assert "if" not in _release_step("Authorize release", "authorize")
    assert _release_step("Prepare release version")["if"] == "github.event_name == 'workflow_dispatch'"
    assert _release_step("Create release commit and tag")["if"] == "github.event_name == 'workflow_dispatch'"
    steps = RELEASE_WORKFLOW["jobs"]["release"]["steps"]
    names = [step.get("name") for step in steps]
    for check in ("Build release assets", "Smoke-test release asset", "Attest build provenance"):
        assert names.index("Create release commit and tag") > names.index(check)
    assert names.index("Publish GitHub release") > names.index("Create release commit and tag")
    assert "version" in (TOOL / "RELEASING.md").read_text()


@pytest.mark.parametrize("event,ref", [("workflow_dispatch", "refs/heads/default"), ("push", "refs/tags/v0.8.0")])
@pytest.mark.parametrize(
    "permission,rerun_permission,allowed",
    [
        ("admin", "admin", True),
        ("write", "admin", False),
        ("maintain", "admin", False),
        ("admin", "write", False),
        ("admin", "read", False),
        ("api-error", "admin", False),
    ],
)
def test_release_requires_both_actors_to_be_admins(tmp_path, event, ref, permission, rerun_permission, allowed):
    _stub_release_cli(
        tmp_path,
        "gh",
        """user = sys.argv[2].split('/')[-2]
permission = json.loads(os.environ['PERMISSIONS'])[user]
if permission == 'api-error':
    sys.exit(1)
print(permission)""",
    )
    result = _run_release_step(
        tmp_path,
        "Authorize release",
        "authorize",
        EVENT_NAME=event,
        GH_REPO="owner/repo",
        ACTOR="starter",
        TRIGGERING_ACTOR="rerunner",
        REF=ref,
        DEFAULT_REF="refs/heads/default",
        PERMISSIONS=json.dumps({"starter": permission, "rerunner": rerun_permission}),
    )
    assert (result.returncode == 0) == allowed, result.stderr


def test_manual_release_refuses_a_non_default_branch_before_calling_the_api(tmp_path):
    _stub_release_cli(tmp_path, "gh", "raise AssertionError('the API must not be called')")
    result = _run_release_step(
        tmp_path,
        "Authorize release",
        "authorize",
        EVENT_NAME="workflow_dispatch",
        REF="refs/heads/feature/release",
        DEFAULT_REF="refs/heads/default",
    )
    assert result.returncode != 0
    assert "Manual releases must run from" in result.stderr
    assert "AssertionError" not in result.stderr


@pytest.mark.parametrize(
    "event,version,ref,tag",
    [
        ("workflow_dispatch", "0.8.0", "main", "v0.8.0"),
        ("workflow_dispatch", "1.0.0-rc.1", "main", "v1.0.0-rc.1"),
        ("push", "", "v0.8.0", "v0.8.0"),
        ("workflow_dispatch", "v0.8.0", "main", None),
        ("workflow_dispatch", "01.8.0", "main", None),
        ("workflow_dispatch", "$(touch injected)", "main", None),
        ("push", "", "not-a-version", None),
    ],
)
def test_release_resolves_and_validates_versions(tmp_path, event, version, ref, tag):
    output = tmp_path / "output"
    result = _run_release_step(
        tmp_path,
        "Validate release tag",
        "authorize",
        EVENT_NAME=event,
        VERSION=version,
        REF_NAME=ref,
        GITHUB_OUTPUT=str(output),
    )
    if tag is None:
        assert result.returncode != 0
        assert not output.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert output.read_text() == f"tag={tag}\n"
    assert not (tmp_path / "injected").exists()


@pytest.mark.parametrize("exists", ["false", "true", "api-error"])
def test_release_prepares_only_a_new_tag_version(tmp_path, exists):
    _stub_release_cli(
        tmp_path,
        "gh",
        """exists = os.environ['TAG_EXISTS']
if exists == 'api-error':
    sys.exit(1)
print(exists)""",
    )
    (tmp_path / "bin" / "python").symlink_to(sys.executable)
    path = tmp_path / "kura" / "__version__.py"
    path.parent.mkdir()
    source = '"""Release version."""\n\n__version__ = "0.7.0"\n'
    path.write_text(source)
    result = _run_release_step(
        tmp_path,
        "Prepare release version",
        GH_REPO="owner/repo",
        TAG="v0.8.0",
        TAG_EXISTS=exists,
    )
    if exists == "false":
        assert result.returncode == 0, result.stderr
        assert path.read_text() == source.replace("0.7.0", "0.8.0")
    else:
        assert result.returncode != 0
        assert path.read_text() == source


@pytest.mark.parametrize("changed", [True, False])
def test_release_pushes_only_its_tag_without_changing_the_default_branch(tmp_path, changed):
    log = tmp_path / "commands"
    _stub_release_cli(
        tmp_path,
        "git",
        """with open(os.environ['COMMAND_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1:4] == ['diff', '--cached', '--quiet']:
    sys.exit(1 if os.environ['VERSION_CHANGED'] == 'true' else 0)""",
    )
    _stub_release_cli(tmp_path, "gh", "if sys.argv[1] == 'api':\n    print(123)")
    result = _run_release_step(
        tmp_path,
        "Create release commit and tag",
        TAG="v0.8.0",
        GITHUB_SERVER_URL="https://github.com",
        COMMAND_LOG=str(log),
        VERSION_CHANGED=str(changed).lower(),
    )
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert commands[0] == ["add", "--", "kura/__version__.py"]
    assert [command for command in commands if command[0] == "push"] == [
        ["push", "origin", "refs/tags/v0.8.0"]
    ]
    assert ["tag", "v0.8.0"] in commands
    commits = [command for command in commands if "commit" in command]
    assert len(commits) == int(changed)
    if changed:
        assert commits[0][-3:] == ["commit", "-m", "chore: release 0.8.0"]


@pytest.mark.parametrize("exists,ready,action", [(False, False, "create"), (True, False, "upload"), (True, True, None)])
def test_release_publication_preserves_existing_assets(tmp_path, exists, ready, action):
    log = tmp_path / "commands"
    _stub_release_cli(
        tmp_path,
        "gh",
        """args = sys.argv[1:]
with open(os.environ['COMMAND_LOG'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:2] == ['release', 'view']:
    if os.environ['RELEASE_EXISTS'] == 'false':
        sys.exit(1)
    if '--json' in args:
        print(os.environ['ASSETS_READY'])""",
    )
    result = _run_release_step(
        tmp_path,
        "Publish GitHub release",
        TAG="v0.8.0",
        COMMAND_LOG=str(log),
        RELEASE_EXISTS=str(exists).lower(),
        ASSETS_READY=str(ready).lower(),
    )
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    mutations = [command for command in commands if command[1] in ("create", "upload")]
    if action is None:
        assert mutations == []
    else:
        assert len(mutations) == 1
        assert mutations[0][:3] == ["release", action, "v0.8.0"]
        assert "dist/kura" in mutations[0]
        assert "dist/kura.sha256" in mutations[0]
        assert "--clobber" not in mutations[0]


def test_release_shell_steps_have_valid_bash_syntax():
    for job in RELEASE_WORKFLOW["jobs"].values():
        for step in job["steps"]:
            if "run" in step:
                result = subprocess.run(
                    ["bash", "-n"],
                    input=step["run"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert result.returncode == 0, f"{step['name']}: {result.stderr}"


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
