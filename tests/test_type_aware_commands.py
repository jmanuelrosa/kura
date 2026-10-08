"""`--type agent|bundle` on the commands that narrow a cross-type result.

`narrow` is pure, so its selection rules are pinned on literal plans. The commands are
then checked for the effects that make narrowing safe: a narrowed run neither creates
nor deletes outside its selection, and a harness-wide refusal still stops it.
"""

import json
from pathlib import Path

import pytest
from kit_helpers import register

from kura import catalog as cat
from kura import cli, config, errors, harnesses, state, views
from kura.transaction import Action, Snapshot


def _catalog(home):
    root = config.catalog_path(home)
    if root.is_symlink():
        root.unlink()
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    return root


def _skill(root, name, *, registered=True, **fields):
    directory = root / "skills" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n")
    if registered:
        register(root, cat.SKILL, name, **fields)
    return directory


def _agent(root, name, *, registered=True, **fields):
    directory = root / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(f"---\nname: {name}\ndescription: {name}\n---\n")
    if registered:
        register(root, cat.AGENT, name, **fields)
    return path


def _bundle(root, name, *, skill, agent, requires=None, **fields):
    directory = root / "bundles" / name
    directory.mkdir(parents=True)
    (directory / "bundle.json").write_text(json.dumps({"requires": requires or {}}))
    register(root, cat.BUNDLE, name, **fields)
    return _skill(directory, skill, registered=False), _agent(directory, agent, registered=False)


def _link(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(source)
    return path


def _v2(skills=(), agents=(), bundles=()):
    return state.Manifest(
        ("claude",),
        tuple(skills),
        schema_version=state.V2_SCHEMA_VERSION,
        agents=tuple(agents),
        bundles=tuple(bundles),
    )


@pytest.fixture
def env(home, project, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.chdir(project)
    root = _catalog(home)
    config.write(config.Config(("claude",)), home)
    return home, project, root


# --- narrow, over literal plans ----------------------------------------------


def _literal_plan():
    root = Path("/project")

    def action(operation, name, kind, leaf):
        before = Snapshot("symlink", target="elsewhere") if operation == "delete" else Snapshot("absent")
        return Action(operation, root / leaf, harness="claude", skill=name, kind=kind, expected=before)

    return views.Plan(
        actions=[
            action("create", "review", cat.SKILL, "skills/review"),
            action("create", "architect", cat.AGENT, "agents/architect.md"),
            action("create", "worker", cat.SKILL, "skills/worker"),
            action("delete", "worker", cat.AGENT, "agents/worker.md"),
        ],
        current=[views.Linked("claude", "helper", root / "skills/helper", cat.SKILL)],
        blocked=[
            views.Blocked("Claude Code: /project/.claude/skills is a directory symlink"),
            views.Blocked("bundle 'ghost' is missing from the catalog", cat.BUNDLE, "ghost"),
            views.Blocked("Claude Code: architect is a real path", cat.AGENT, "architect"),
        ],
        missing=[views.Missing("vanished", None, None, cat.SKILL)],
        logical_skills={"review", "worker", "helper"},
        logical_agents={"architect", "worker"},
        global_fallbacks={"claude": {"review", "worker"}},
        bundled_by={
            (cat.BUNDLE, "backend"): ("backend",),
            (cat.BUNDLE, "ghost"): ("ghost",),
            (cat.SKILL, "worker"): ("backend",),
            (cat.SKILL, "helper"): ("backend",),
            (cat.AGENT, "worker"): ("backend",),
        },
    )


def _keys(plan):
    return {
        "actions": sorted((action.kind, action.skill, action.operation) for action in plan.actions),
        "current": sorted(row.name for row in plan.current),
        "blocked": sorted(row.detail for row in plan.blocked),
        "missing": sorted(row.name for row in plan.missing),
        "skills": sorted(plan.logical_skills),
        "agents": sorted(plan.logical_agents),
        "fallbacks": {harness: sorted(names) for harness, names in plan.global_fallbacks.items()},
    }


def test_narrow_without_a_selection_is_the_plan_itself():
    plan = _literal_plan()

    assert views.narrow(plan, None) is plan


def test_narrow_to_skill_keeps_every_skill_including_bundle_members():
    assert _keys(views.narrow(_literal_plan(), cat.SKILL)) == {
        "actions": [(cat.SKILL, "review", "create"), (cat.SKILL, "worker", "create")],
        "current": ["helper"],
        "blocked": ["Claude Code: /project/.claude/skills is a directory symlink"],
        "missing": ["vanished"],
        "skills": ["helper", "review", "worker"],
        "agents": [],
        "fallbacks": {"claude": ["review", "worker"]},
    }


def test_narrow_to_agent_keeps_every_agent_including_bundle_members():
    assert _keys(views.narrow(_literal_plan(), cat.AGENT)) == {
        "actions": [(cat.AGENT, "architect", "create"), (cat.AGENT, "worker", "delete")],
        "current": [],
        "blocked": [
            "Claude Code: /project/.claude/skills is a directory symlink",
            "Claude Code: architect is a real path",
        ],
        "missing": [],
        "skills": [],
        "agents": ["architect", "worker"],
        "fallbacks": {},
    }


def test_narrow_to_bundle_keeps_only_what_a_bundle_contributes_and_bundle_findings():
    assert _keys(views.narrow(_literal_plan(), cat.BUNDLE)) == {
        "actions": [(cat.AGENT, "worker", "delete"), (cat.SKILL, "worker", "create")],
        "current": ["helper"],
        "blocked": [
            "Claude Code: /project/.claude/skills is a directory symlink",
            "bundle 'ghost' is missing from the catalog",
        ],
        "missing": [],
        "skills": ["helper", "worker"],
        "agents": ["worker"],
        "fallbacks": {"claude": ["worker"]},
    }


@pytest.mark.parametrize("selection", (cat.SKILL, cat.AGENT, cat.BUNDLE))
def test_an_untyped_block_survives_every_selection(selection):
    plan = views.Plan(blocked=[views.Blocked("Pi: project agent path is not configured")])

    narrowed = views.narrow(plan, selection)

    assert narrowed.refused
    assert [row.detail for row in narrowed.blocked] == ["Pi: project agent path is not configured"]


def test_project_plan_records_kind_and_bundle_provenance(env):
    home, project, root = env
    _skill(root, "review")
    _bundle(root, "backend", skill="worker", agent="worker", requires={"skills": ["review"]})
    catalog = cat.build_catalog(root)
    manifest = _v2(skills=("review",), bundles=("backend",))

    plan = views.project_plan(catalog, root, home, project, manifest, manifest, ("claude",))

    assert sorted((action.kind, action.skill) for action in plan.actions) == [
        (cat.AGENT, "worker"),
        (cat.SKILL, "review"),
        (cat.SKILL, "worker"),
    ]
    assert plan.bundled_by[(cat.SKILL, "review")] == ("backend",)
    assert plan.bundled_by[(cat.AGENT, "worker")] == ("backend",)
    assert plan.bundled_by[(cat.BUNDLE, "backend")] == ("backend",)


# --- converge and restore ----------------------------------------------------


def _redundant_global_skill(home, project, root):
    """A direct global skill whose global link is current, so its project link is prunable."""
    source = _skill(root, "commit", **{"global": True})
    _link(harnesses.skill_path("claude", "commit", home), source)
    return _link(harnesses.skill_path("claude", "commit", home, project), source)


def test_converge_type_agent_leaves_every_skill_link_alone(env):
    home, project, root = env
    _skill(root, "review")
    architect = _agent(root, "architect")
    redundant = _redundant_global_skill(home, project, root)
    state.write(project, _v2(skills=("commit", "review"), agents=("architect",)))

    assert cli.main(["converge", "--type", "agent"]) == errors.OK

    assert harnesses.agent_path("claude", "architect", home, project).resolve() == architect
    assert not harnesses.skill_path("claude", "review", home, project).exists()
    assert redundant.is_symlink()

    assert cli.main(["converge"]) == errors.OK
    assert harnesses.skill_path("claude", "review", home, project).is_symlink()
    assert not redundant.is_symlink()


def test_converge_type_bundle_links_members_and_never_prunes_an_orphan(env):
    home, project, root = env
    worker_skill, worker_agent = _bundle(root, "backend", skill="worker", agent="worker")
    redundant = _redundant_global_skill(home, project, root)
    state.write(project, _v2(skills=("commit",), bundles=("backend",)))

    assert cli.main(["converge", "--type", "bundle"]) == errors.OK

    assert harnesses.skill_path("claude", "worker", home, project).resolve() == worker_skill
    assert harnesses.agent_path("claude", "worker", home, project).resolve() == worker_agent
    assert redundant.is_symlink()


def test_a_narrowed_converge_still_refuses_on_a_harness_wide_collision(env, tmp_path):
    home, project, root = env
    _skill(root, "review")
    _agent(root, "architect")
    state.write(project, _v2(skills=("review",), agents=("architect",)))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _link(harnesses.project_skill_root(project, "claude"), elsewhere)

    assert cli.main(["converge", "--type", "agent"]) == errors.DRIFT
    assert not harnesses.agent_path("claude", "architect", home, project).exists()


def test_restore_type_skill_creates_only_skill_links(env):
    home, project, root = env
    review = _skill(root, "review")
    _agent(root, "architect")
    state.write(project, _v2(skills=("review",), agents=("architect",)))

    assert cli.main(["restore", "--type", "skill"]) == errors.OK

    assert harnesses.skill_path("claude", "review", home, project).resolve() == review
    assert not harnesses.agent_path("claude", "architect", home, project).exists()


# --- sync --------------------------------------------------------------------


def test_sync_type_agent_converges_global_agents_and_touches_no_skill_link(env, capsys):
    home, _, root = env
    _skill(root, "commit", **{"global": True})
    stale = _link(harnesses.skill_path("claude", "review", home), _skill(root, "review"))
    architect = _agent(root, "architect", **{"global": True})

    assert cli.main(["sync", "--type", "agent"]) == errors.OK

    assert harnesses.agent_path("claude", "architect", home).resolve() == architect
    assert not harnesses.skill_path("claude", "commit", home).exists()
    assert stale.is_symlink()
    capsys.readouterr()

    assert cli.main(["sync", "--type", "agent"]) == errors.OK
    assert ", 0 changes" in capsys.readouterr().out


def test_sync_type_skill_keeps_the_skill_dependencies_of_global_agents(env, capsys):
    home, _, root = env
    _skill(root, "commit", **{"global": True})
    lint = _skill(root, "lint")
    _agent(root, "architect", dependencies=["lint"], **{"global": True})

    assert cli.main(["sync"]) == errors.OK
    assert harnesses.skill_path("claude", "lint", home).resolve() == lint
    capsys.readouterr()

    assert cli.main(["sync", "--type", "skill"]) == errors.OK

    assert harnesses.skill_path("claude", "lint", home).resolve() == lint
    assert ", 0 changes" in capsys.readouterr().out


def test_sync_type_bundle_refuses_as_bundles_are_project_only(env):
    home, _, root = env
    _bundle(root, "backend", skill="worker", agent="worker")
    _skill(root, "commit", **{"global": True})

    assert cli.main(["sync", "--type", "bundle"]) == errors.USAGE
    assert not harnesses.skill_path("claude", "commit", home).exists()


# --- adopt -------------------------------------------------------------------


def test_adopt_type_bundle_reports_a_partial_bundle_and_adopts_a_complete_one(env, capsys):
    home, project, root = env
    worker_skill, worker_agent = _bundle(root, "backend", skill="worker", agent="worker")
    tool_skill, _ = _bundle(root, "frontend", skill="tool", agent="designer")
    state.write(project, _v2())
    _link(harnesses.skill_path("claude", "worker", home, project), worker_skill)
    _link(harnesses.agent_path("claude", "worker", home, project), worker_agent)
    _link(harnesses.skill_path("claude", "tool", home, project), tool_skill)

    assert cli.main(["adopt", "--type", "bundle"]) == errors.DRIFT

    assert state.read_strict(project).bundles == ("backend",)
    assert not harnesses.agent_path("claude", "designer", home, project).exists()
    assert "frontend" in capsys.readouterr().out


def test_adopt_type_bundle_refuses_a_partial_bundle_without_writing(env):
    home, project, root = env
    worker_skill, _ = _bundle(root, "backend", skill="worker", agent="worker")
    manifest = _v2()
    state.write(project, manifest)
    _link(harnesses.skill_path("claude", "worker", home, project), worker_skill)

    assert cli.main(["adopt", "--type", "bundle"]) == errors.DRIFT

    assert state.read_strict(project) == manifest
    assert not harnesses.agent_path("claude", "worker", home, project).exists()


def test_untyped_adopt_records_bundle_members_as_bundle_intent_not_direct(env):
    home, project, root = env
    review = _skill(root, "review")
    extra = _skill(root, "extra")
    architect = _agent(root, "architect")
    worker_skill, worker_agent = _bundle(
        root,
        "backend",
        skill="worker",
        agent="worker",
        requires={"skills": ["review"], "agents": ["architect"]},
    )
    state.write(project, _v2())
    for link, source in (
        (harnesses.skill_path("claude", "worker", home, project), worker_skill),
        (harnesses.agent_path("claude", "worker", home, project), worker_agent),
        (harnesses.skill_path("claude", "review", home, project), review),
        (harnesses.agent_path("claude", "architect", home, project), architect),
        (harnesses.skill_path("claude", "extra", home, project), extra),
    ):
        _link(link, source)

    assert cli.main(["adopt"]) == errors.OK

    manifest = state.read_strict(project)
    assert manifest.bundles == ("backend",)
    assert manifest.agents == ()
    assert manifest.skills == ("extra",)


# --- doctor ------------------------------------------------------------------


def _doctor_project(home, project, root):
    _skill(root, "review")
    _bundle(root, "backend", skill="worker", agent="worker")
    state.write(project, _v2(skills=("review",), bundles=("backend", "ghost")))


def test_doctor_type_bundle_shows_bundle_findings_and_untyped_ones_only(env, capsys):
    home, project, root = env
    _doctor_project(home, project, root)
    (project / "AGENTS.md").write_text("# agents\n")
    (project / "CLAUDE.md").write_text("# claude\n")

    assert cli.main(["doctor", "--type", "bundle"]) == errors.DRIFT

    out = capsys.readouterr().out
    assert "bundle 'ghost' is missing from the catalog" in out
    assert f"{Path('skills') / 'worker'}" in out
    assert f"{Path('agents') / 'worker.md'}" in out
    assert "AGENTS.md and CLAUDE.md are split" in out
    assert "review" not in out


def test_doctor_type_skill_shows_bundle_member_skills_but_no_agent_or_bundle_finding(env, capsys):
    home, project, root = env
    _doctor_project(home, project, root)

    assert cli.main(["doctor", "--type", "skill"]) == errors.DRIFT

    out = capsys.readouterr().out
    assert "review: create required" in out
    assert f"{Path('skills') / 'worker'}" in out
    assert "worker.md" not in out
    assert "ghost" not in out


# --- scout -------------------------------------------------------------------


def _scout_catalog(root):
    _skill(root, "review-skill", groups=["review"])
    _agent(root, "reviewer", groups=["review"])
    _bundle(root, "review-kit", skill="kit-skill", agent="kit-agent", groups=["review"])


@pytest.mark.parametrize(
    ("selection", "offered", "withheld"),
    (
        (cat.AGENT, "reviewer", ("review-skill", "review-kit")),
        (cat.BUNDLE, "review-kit", ("review-skill", "reviewer")),
    ),
)
def test_scout_type_recommends_only_that_kind_by_its_tags(env, capsys, selection, offered, withheld):
    _, project, root = env
    _scout_catalog(root)
    state.write(project, _v2())

    assert cli.main(["scout", "--type", selection, "--focus", "review"]) == errors.OK

    out = capsys.readouterr().out
    assert f"kura add {offered} --type {selection}" in out
    for name in withheld:
        assert name not in out


def test_untyped_scout_covers_every_kind_with_a_kind_suffix(env, capsys):
    _, project, root = env
    _scout_catalog(root)
    state.write(project, _v2())

    assert cli.main(["scout", "--focus", "review"]) == errors.OK

    out = capsys.readouterr().out
    for command in (
        "kura add review-skill --type skill",
        "kura add reviewer --type agent",
        "kura add review-kit --type bundle",
    ):
        assert command in out
    assert "(bundle)" in out


def test_scout_type_bundle_add_installs_the_bundle_and_then_counts_it_as_here(env, capsys):
    home, project, root = env
    _scout_catalog(root)
    state.write(project, _v2())

    assert cli.main(["scout", "--type", "bundle", "--focus", "review", "--add"]) == errors.OK

    assert state.read_strict(project).bundles == ("review-kit",)
    assert harnesses.agent_path("claude", "kit-agent", home, project).is_symlink()
    capsys.readouterr()
    assert cli.main(["scout", "--type", "bundle", "--focus", "review"]) == errors.OK
    assert "1 already here" in capsys.readouterr().out


# --- dispatch ----------------------------------------------------------------


@pytest.mark.parametrize("command", ("scout", "sync", "doctor", "adopt", "restore", "converge"))
def test_the_legacy_plugin_type_is_still_refused(env, command):
    assert cli.main([command, "--type", "plugin"]) == errors.USAGE
