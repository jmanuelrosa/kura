"""`scout` at its two pure seams: what a directory earns, and what that ranks.

fingerprint.py is asked what a project is made of and scout.rank and scout.fold what
that earns, over literal artifacts so no case depends on how the catalogue is tagged.
The command's wiring across all three kinds is exercised in test_type_aware_commands.py.

Cases assert on tiers and order, never on wording.
"""

import json

import pytest

from kura import catalog as cat
from kura import fingerprint
from kura.commands import scout


def js_project(project, **dependencies):
    (project / "package.json").write_text(json.dumps({"dependencies": dependencies}))
    return project


def art(name, *groups, kind=cat.SKILL):
    """A stand-in artifact. rank() reads only the name, the type and the tags."""
    return cat.Artifact(name=name, type=kind, groups=tuple(groups))


def match(name, kind=cat.SKILL):
    return scout.Match(name=name, kind=kind, groups=(), description="", why="why", score=1.0)


def named(matches):
    return [match.name for match in matches]


def emittable_directly():
    return {
        tag
        for group in (
            *fingerprint.DEP_TAGS.values(),
            *fingerprint.DEP_PREFIX_TAGS.values(),
            *fingerprint.INTENT_KEYWORDS.values(),
            fingerprint.SWIFT_TAGS,
            fingerprint.TAURI_TAGS,
            fingerprint.MACOS_TAGS,
            fingerprint.GAP_TAGS,
        )
        for tag in group
    }


# --- fingerprint -------------------------------------------------------------


def test_g1_every_tech_tag_can_actually_be_detected():
    """A gate nothing can satisfy only ever subtracts, and nothing reports it."""
    assert sorted(fingerprint.TECH_TAGS - emittable_directly()) == []


def test_g2_no_implied_tag_is_a_gate_tag():
    """rank() satisfies the gate from direct evidence alone, so this would be inert."""
    inert = {
        tag: sorted(set(neighbours) & fingerprint.TECH_TAGS)
        for tag, neighbours in fingerprint.IMPLIED_TAGS.items()
        if set(neighbours) & fingerprint.TECH_TAGS
    }
    assert inert == {}


def test_every_tag_a_probe_emits_is_observable():
    assert emittable_directly() <= fingerprint.OBSERVABLE_TAGS


@pytest.mark.parametrize(
    ("dependency", "persona"),
    (("react-dom", "frontend"), ("astro", "frontend"), ("fastify", "backend"), ("hono", "backend"), ("@nestjs/core", "backend")),
)
def test_a_framework_names_the_seat_it_serves_directly(project, dependency, persona):
    assert persona in fingerprint.read(js_project(project, **{dependency: "1.0.0"}))


def test_bare_react_does_not_assert_the_frontend_seat(project):
    """React Native projects declare react too."""
    direct = fingerprint.read(js_project(project, react="19.0.0"))
    assert "react" in direct
    assert "frontend" not in direct


def test_an_intent_keyword_inside_another_word_is_not_evidence(project):
    (project / "docs").mkdir()
    (project / "CLAUDE.md").write_text("export function loadRoutes() {}\n")
    assert "documentation" not in fingerprint.read(project)


def test_an_intent_keyword_is_evidence_when_it_is_a_word(project):
    (project / "docs").mkdir()
    (project / "README.md").write_text("We practise TDD.\n")
    (project / "tests").mkdir()
    assert fingerprint.read(project)["testing"] == "README.md mentions 'tdd'"


# --- rank --------------------------------------------------------------------


def test_a_tech_hit_is_strong_however_many_other_tags_the_artifact_carries():
    strong, _ = scout.rank(
        [art("astro", "astro", "frontend", "backend", "testing", "review")],
        {"astro": "astro in package.json"},
        {},
        None,
    )
    assert named(strong) == ["astro"]


def test_a_lone_persona_hit_on_a_generic_artifact_is_only_a_guess():
    """A review skill tagged frontend and backend is not about a front-end project."""
    strong, consider = scout.rank(
        [art("review", "frontend", "backend", "refactoring"), art("frontend-seat", "frontend", kind=cat.BUNDLE)],
        {"frontend": "react-dom in package.json"},
        {},
        None,
    )
    assert named(strong) == ["frontend-seat"]
    assert named(consider) == ["review"]


def test_tags_no_probe_can_observe_do_not_count_against_the_fit():
    """qa and quality are invisible to scout, so the qa seat fits a project with no tests."""
    strong, _ = scout.rank(
        [art("qa", "quality", "qa", "testing", kind=cat.BUNDLE)],
        {"testing": "no test directory and no test files"},
        {},
        None,
    )
    assert named(strong) == ["qa"]


def test_the_focus_is_strong_even_on_a_poor_fit():
    strong, _ = scout.rank(
        [art("seat", "review", "frontend", "backend", "testing")],
        {"review": "requested focus 'review'"},
        {},
        "review",
    )
    assert named(strong) == ["seat"]


def test_a_rarer_tag_outranks_a_common_one():
    catalog = [art(f"f{i}", "frontend") for i in range(9)] + [art("a", "astro")]
    weights = scout.specificity(catalog)
    assert weights["astro"] > weights["frontend"]
    strong, _ = scout.rank(
        [art("generic", "frontend"), art("specific", "astro")],
        {"frontend": "e", "astro": "e"},
        {},
        None,
        weights,
    )
    assert named(strong) == ["specific", "generic"]


def test_a_closer_fit_outranks_a_looser_one_on_the_same_tag():
    _, consider = scout.rank(
        [art("loose", "frontend", "backend", "refactoring"), art("close", "frontend")],
        {},
        {"frontend": "implied"},
        None,
    )
    assert named(consider) == ["close", "loose"]


def test_the_reason_prefers_the_rarest_tag_after_tech():
    weights = {"workflow": 0.5, "documentation": 2.0}
    evidence = {"workflow": "common", "documentation": "rare"}
    assert scout.reason(["documentation", "workflow"], evidence, None, weights) == "rare"


# --- fold --------------------------------------------------------------------


def test_a_member_of_an_offered_bundle_is_folded_into_it():
    bundle, member = match("seat", cat.BUNDLE), match("checklist")
    brings = {(cat.BUNDLE, "seat"): {(cat.SKILL, "checklist"), (cat.AGENT, "seat-engineer")}}
    strong, consider = scout.fold([bundle], [member], brings)
    assert named(strong) == ["seat"]
    assert consider == []
    assert strong[0].adds == ((cat.AGENT, "seat-engineer"), (cat.SKILL, "checklist"))


def test_a_strong_member_is_never_folded_into_a_guessed_bundle():
    """--add takes the strong tier, so folding upwards would lose the member."""
    brings = {(cat.BUNDLE, "seat"): {(cat.SKILL, "checklist")}}
    strong, consider = scout.fold([match("checklist")], [match("seat", cat.BUNDLE)], brings)
    assert named(strong) == ["checklist"]
    assert named(consider) == ["seat"]


def test_an_agents_dependencies_are_folded_into_it():
    brings = {(cat.AGENT, "architect"): {(cat.SKILL, "adrs")}}
    strong, _ = scout.fold([match("adrs"), match("architect", cat.AGENT)], [], brings)
    assert named(strong) == ["architect"]


def test_matches_that_bring_each_other_both_stay():
    brings = {(cat.SKILL, "a"): {(cat.SKILL, "b")}, (cat.SKILL, "b"): {(cat.SKILL, "a")}}
    strong, _ = scout.fold([match("a"), match("b")], [], brings)
    assert named(strong) == ["a", "b"]


# --- describe ----------------------------------------------------------------


def test_a_bundle_is_described_by_its_manifest(tmp_path):
    (tmp_path / cat.BUNDLE_MARKER).write_text(json.dumps({"description": "The  frontend\nseat."}))
    bundle = cat.Bundle(name="frontend", source=tmp_path)
    assert scout.describe(bundle) == "The frontend seat."


def test_a_bundle_without_a_description_has_no_gist(tmp_path):
    (tmp_path / cat.BUNDLE_MARKER).write_text("[]")
    assert scout.describe(cat.Bundle(name="frontend", source=tmp_path)) == ""
