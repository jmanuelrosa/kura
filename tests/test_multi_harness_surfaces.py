import json

import pytest

from kura import cli, config, errors, harnesses, state
from kura.commands import doctor


ROW_FIELDS = {
    "name",
    "state",
    "installed",
    "global",
    "groups",
    "dependencies",
    "reason",
    "parent",
    "global_for",
    "views",
}


def catalog_at(path, entries, present=None, declared=None):
    path.mkdir(parents=True)
    skills = path / "skills"
    skills.mkdir()
    present = {entry["name"] for entry in entries} if present is None else set(present)
    declared = declared or {}
    for name in sorted(present):
        source = skills / name
        source.mkdir()
        frontmatter_name = declared.get(name, name)
        (source / "SKILL.md").write_text(
            f"---\nname: {frontmatter_name}\ndescription: {name}\n---\n\n# {name}\n"
        )
    (path / "skill-registry.json").write_text(json.dumps({"local": entries}))
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(project)
    return home, project


def configure(home, harness_ids=("claude", "pi")):
    config.write(config.Config(harness_ids), home)


@pytest.mark.parametrize(
    "arguments",
    (
        ["add", "unknown", "--type", "skill"],
        ["add", "--type", "skill", "--group", "unknown"],
    ),
)
def test_project_add_checks_the_exact_manifest_before_requests(workspace, tmp_path, capsys, arguments):
    home, project = workspace
    catalog_at(
        config.catalog_path(home),
        [{"name": "known", "groups": ["known"]}],
    )
    configure(home)
    state.write(project.parent, state.Manifest(("claude",), ()))

    assert cli.main(arguments) == errors.NO_PROJECT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not initialized for project operations" in captured.err
    assert "exact directory" in captured.err


def test_project_add_distinguishes_missing_declared_intent_from_a_new_request(
    workspace, tmp_path, capsys
):
    home, project = workspace
    catalog = catalog_at(config.catalog_path(home), [{"name": "vanished"}])
    configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ("vanished",)))
    source = catalog / "skills" / "vanished"
    (source / "SKILL.md").unlink()
    source.rmdir()

    assert cli.main(["add", "vanished", "--type", "skill"]) == errors.DRIFT
    captured = capsys.readouterr()
    assert "remains declared but is missing from the catalog" in captured.out
    assert captured.err == ""
    assert state.read_strict(project).skills == ("vanished",)

    state.write(project, state.Manifest(("claude", "pi"), ()))
    assert cli.main(["add", "vanished", "--type", "skill"]) == errors.NOT_FOUND
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing from the catalog" in captured.err


def test_adopt_does_not_promote_an_existing_dependency_to_direct(workspace, tmp_path, capsys):
    home, project = workspace
    catalog = catalog_at(
        config.catalog_path(home),
        [
            {"name": "root", "dependencies": ["helper"]},
            {"name": "helper"},
        ],
    )
    configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ("root",)))
    claude = harnesses.project_skill_root(project, "claude")
    claude.mkdir(parents=True)
    (claude / "helper").symlink_to(catalog / "skills" / "helper")

    assert cli.main(["adopt"]) == errors.OK
    captured = capsys.readouterr()
    assert "0 direct skills adopted" in captured.out
    assert captured.err == ""
    assert state.read_strict(project).skills == ("root",)


def test_scout_counts_invalid_direct_intent_without_recommending_catalog_errors(
    workspace, tmp_path, capsys
):
    home, project = workspace
    catalog = catalog_at(
        config.catalog_path(home),
        [
            {"name": "declared-broken", "groups": ["review"]},
            {"name": "candidate-broken", "groups": ["review"]},
        ],
        declared={
            "declared-broken": "wrong-declared-name",
            "candidate-broken": "wrong-candidate-name",
        },
    )
    configure(home)
    state.write(project, state.Manifest(("claude",), ("declared-broken",)))

    assert cli.main(["scout", "--focus", "review"]) == errors.OK
    captured = capsys.readouterr()
    assert "declared-broken" in captured.out
    assert "1 already here" in captured.out
    assert "candidate-broken" not in captured.out
    assert "kura add declared-broken" not in captured.out
    assert captured.err == ""


def test_doctor_checks_project_views_against_the_fixed_catalog(workspace, capsys):
    home, project = workspace
    catalog = catalog_at(
        config.catalog_path(home),
        [{"name": "review"}],
    )
    state.write(project, state.Manifest(("claude",), ("review",)))

    assert cli.main(["doctor"]) == errors.DRIFT
    captured = capsys.readouterr()
    assert "Claude Code view: review: create required" in captured.out
    assert "Machine configuration: not created" in captured.out
    assert str(catalog) not in captured.err
    assert captured.err == ""


@pytest.mark.parametrize("manifest_kind", ("symlink", "directory"))
def test_doctor_rejects_non_regular_root_manifests(
    workspace, capsys, manifest_kind
):
    home, project = workspace
    catalog_at(config.catalog_path(home), [])
    manifest = state.path_for(project)
    if manifest_kind == "symlink":
        target = project.parent / "other.json"
        target.write_text(state.dump(state.Manifest(("claude",), ())))
        manifest.symlink_to(target)
    else:
        manifest.mkdir()

    assert cli.main(["doctor"]) == errors.DRIFT
    captured = capsys.readouterr()
    assert "Project manifest" in captured.out
    assert "not a regular project manifest" in captured.out
    assert "Project: not initialized" not in captured.out
    assert captured.err == ""


def test_listing_keeps_physical_installation_and_foreign_targets_in_json(
    workspace, tmp_path, capsys
):
    home, project = workspace
    catalog = catalog_at(
        config.catalog_path(home),
        [
            {"name": "parent", "dependencies": ["child"]},
            {"name": "child"},
        ],
    )
    configure(home)
    state.write(project, state.Manifest(("claude", "pi"), ("parent",)))
    claude = harnesses.project_skill_root(project, "claude")
    pi = harnesses.project_skill_root(project, "pi")
    claude.mkdir(parents=True)
    pi.mkdir(parents=True)
    (claude / "parent").symlink_to(catalog / "skills" / "parent")
    foreign = tmp_path / "foreign-parent"
    foreign.mkdir()
    (pi / "parent").symlink_to(foreign)

    assert cli.main(["list", "--type", "skill", "--json"]) == errors.OK
    captured = capsys.readouterr()
    rows = {row["name"]: row for row in json.loads(captured.out)}
    assert captured.err == ""
    assert set(rows["parent"]) == ROW_FIELDS
    assert rows["parent"]["installed"] == "project"
    assert rows["parent"]["state"] == "drift"
    assert rows["parent"]["views"]["pi"] == {
        "state": "foreign",
        "target": str(foreign),
    }
    assert rows["child"]["installed"] is None
    assert rows["child"]["reason"] == "dep-of:parent"

    assert cli.main(["list", "--type", "skill"]) == errors.OK
    captured = capsys.readouterr()
    assert "2 configured" in captured.out
    assert str(foreign) in captured.out
    assert captured.err == ""


def test_listing_retains_missing_global_policy_and_harness_views(
    workspace, tmp_path, capsys
):
    home, project = workspace
    catalog = catalog_at(
        config.catalog_path(home),
        [{"name": "missing-global", "groups": ["global"]}],
        present=(),
    )
    configure(home)
    state.write(project, state.Manifest(("claude",), ()))

    assert cli.main(["list", "--type", "skill", "--json"]) == errors.OK
    captured = capsys.readouterr()
    rows = {row["name"]: row for row in json.loads(captured.out)}
    row = rows["missing-global"]
    assert captured.err == ""
    assert set(row) == ROW_FIELDS
    assert row["state"] == "missing"
    assert row["installed"] is None
    assert row["global"] is True
    assert row["views"] == {
        "claude": {"state": "missing"},
        "pi": {"state": "missing"},
    }


def test_doctor_reports_findings_in_the_accepted_order(capsys):
    findings = [
        doctor.Finding("legacy-state", doctor.NOTE, "legacy", "legacy"),
        doctor.Finding("split-instructions", doctor.NOTE, "instructions", "instructions"),
        doctor.Finding("executable-absent", doctor.NOTE, "executable", "executable"),
        doctor.Finding("trust", doctor.NOTE, "trust", "trust"),
        doctor.Finding("native-view-drift", doctor.PROBLEM, "native", "native"),
        doctor.Finding("unsafe-view", doctor.PROBLEM, "collision", "collision"),
        doctor.Finding("missing-dependency", doctor.PROBLEM, "catalog", "catalog"),
        doctor.Finding("project-manifest", doctor.PROBLEM, "manifest", "manifest"),
        doctor.Finding("machine-config", doctor.PROBLEM, "machine", "machine"),
    ]

    assert doctor.report(findings) == errors.DRIFT
    captured = capsys.readouterr()
    positions = [
        captured.out.index(f"  {subject}:")
        for subject in (
            "machine",
            "manifest",
            "catalog",
            "collision",
            "native",
            "trust",
            "executable",
            "instructions",
            "legacy",
        )
    ]
    assert positions == sorted(positions)
    assert captured.err == ""


def test_doctor_notes_do_not_fail_health(capsys):
    findings = [
        doctor.Finding("trust", doctor.NOTE, "trust", "not granted"),
        doctor.Finding("executable-absent", doctor.NOTE, "executable", "not found"),
        doctor.Finding("split-instructions", doctor.NOTE, "instructions", "split"),
        doctor.Finding("legacy-state", doctor.NOTE, "legacy", "preserved"),
    ]

    assert doctor.report(findings) == errors.OK
    captured = capsys.readouterr()
    assert "0 problem(s), 4 note(s)" in captured.out
    assert captured.err == ""
