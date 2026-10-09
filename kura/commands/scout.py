"""`kura scout`: what this project is missing, ranked.

Every other command starts from a name you already know. This one starts from the
directory: it fingerprints the project (fingerprint.py), matches that fingerprint
against the catalogue's group tags, and prints a shortlist. Answering "what should
I install here" therefore needs no prior knowledge of what exists, which is the
gap between `list` (the whole catalogue, alphabetically, telling you nothing
about relevance) and `add`, which already assumes the answer.

Read-only unless `--add`, which installs the strong tier and only the strong tier.
The weaker tier is a prompt to go and look, not a recommendation to act on, so no
flag installs it.

`--type` is optional here for the same reason as on `doctor` and `adopt`: a
project's stack implies artifacts of all three kinds: a React repo wants react
skills and the frontend seat bundle, and a required `--type` would make a partial
answer the only one available. Given, it narrows the whole report.

Nothing already available here is ever offered, and "available" is wider than
"linked in this project": it covers ~/.claude too, and anything that *belongs* in
~/.claude whether or not `sync` has run yet. Offering a global artifact would be
offering to install what every project already loads.
"""

import json
import math
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass, replace
from types import SimpleNamespace

from .. import catalog as cat
from .. import errors, fingerprint, frontmatter, harnesses, paths, scope, views
from .. import colors, ui
from . import add, common

# How many artifacts a report may name before it stops being a shortlist. Strong
# matches take from this first; the weaker tier gets whatever is left, so a
# well-covered project sees no guesses at all.
SHORTLIST_CAP = 12

# Descriptions are written for a model and run long; several are a paragraph. The
# report wants the gist, not the contract.
DESCRIPTION_CAP = 220
WRAP = 100

# How much of an artifact's observable tags a project must match before a persona or
# topic hit alone makes it a strong match. `frontend` is carried by every front-end
# skill, generic review and refactoring ones included; a tag naming the technology
# itself is exempt, because the tech gate has already established relevance.
MIN_FIT = 0.5

STRONG = "Strong match"
CONSIDER = "Worth considering"
ALREADY = "Already in this project"

HEADER = "🔎 Scouting {}"

TYPE_ORDER = (cat.SKILL, cat.AGENT, cat.BUNDLE)


@dataclass(frozen=True)
class Match:
    """One recommendation, carrying the reason it was made."""

    name: str
    kind: str
    groups: tuple
    description: str
    # The evidence string for the tag that put it here, printed verbatim.
    why: str
    # How specifically it matches the project. Ranks within a tier, never across.
    score: float
    # What installing it also brings that this project does not have yet, as
    # (kind, name) pairs. For a bundle this is where its seat agent shows up.
    adds: tuple = ()


def describe(art):
    """A one-line gist of what an artifact does, or "".

    Four sources because the types keep their prose in different places: a skill's
    SKILL.md frontmatter, an agent's own frontmatter, a bundle's or a plugin's
    manifest.
    """
    if art.source is None:
        return ""
    if art.type in (cat.BUNDLE, cat.PLUGIN):
        marker = cat.BUNDLE_MARKER if art.type == cat.BUNDLE else cat.PLUGIN_MANIFEST
        manifest = art.source / marker
        try:
            data = json.loads(manifest.read_text(errors="replace"))
        except (OSError, ValueError):
            return ""
        if not isinstance(data, dict):
            return ""
        return " ".join(str(data.get("description") or "").split())
    path = art.source / "SKILL.md" if art.type == cat.SKILL else art.source
    try:
        return frontmatter.description(path.read_text(errors="replace"))
    except OSError:
        return ""


def configured_names(catalog, manifest):
    """{kind: names this project already has}, closure included.

    A skill or agent a declared bundle or agent brings in is already here, so it
    is never offered again.
    """
    resolution = cat.bundle_resolution(catalog, manifest.bundles, manifest.skills, manifest.agents)
    return {
        cat.SKILL: set(manifest.skills) | set(resolution.skills),
        cat.AGENT: set(manifest.agents) | set(resolution.agents),
        cat.BUNDLE: set(manifest.bundles),
    }


def _present(art):
    if art.source is None:
        return False
    return art.source.is_file() if art.type == cat.AGENT else art.source.is_dir()


def _globally_linked(art, machine, catalog_root, home):
    if machine is None or art.type == cat.BUNDLE:
        return False
    roots = views.accepted_artifact_roots(catalog_root, art.type) if catalog_root is not None else ()
    for harness_id in machine.global_harnesses:
        if art.type == cat.AGENT:
            path = harnesses.agent_path(harness_id, art.name, home, machine_config=machine)
        else:
            path = harnesses.skill_path(harness_id, art.name, home)
        if path is None or views.classify(path, art.source, roots).state != views.CURRENT:
            return False
    return True


def available(catalog, effective, configured, machine=None, catalog_root=None, home=None, kinds=TYPE_ORDER):
    """Project-scoped candidates and configured artifacts of `kinds`."""
    candidates, already = [], []
    for kind in kinds:
        for art in cat.visible(catalog, kind):
            if kind != cat.BUNDLE and scope.belongs_global(art, effective):
                continue
            if art.name in configured[kind]:
                already.append(art)
                continue
            if not _present(art) or art.catalog_error:
                continue
            if not _globally_linked(art, machine, catalog_root, home):
                candidates.append(art)
    return candidates, already


def specificity(arts):
    """{tag: weight}, higher the fewer artifacts carry the tag. Pure.

    Inverse document frequency over the catalogue: sharing `astro` with a project
    says far more than sharing `frontend`, which a fifth of the catalogue carries.
    Derived from the registries on every run, so retagging needs no edit here.
    """
    counts = Counter(tag for art in arts for tag in set(art.groups))
    return {tag: math.log(1 + len(arts) / count) for tag, count in counts.items()}


def reason(matched, evidence, focus, weights=None):
    """Which of the matched tags explains the recommendation.

    The focus first, when it is one of them, because it is what the reader asked
    about. Then a tag naming a technology, then the rarest, then alphabetical, so
    the fallback is at least stable.

    Without this the reason is whichever tag happens to sort first, and the `qa`
    seat matching a project with no tests on `testing` justified itself with
    `observability` instead.
    """
    if focus and focus in matched:
        return evidence[focus]
    weights = weights or {}
    best = min(
        matched,
        key=lambda tag: (tag not in fingerprint.TECH_TAGS, -weights.get(tag, 0), tag),
    )
    return evidence[best]


def rank(candidates, direct, indirect, focus, weights=None):
    """Split candidates into the two tiers. Pure.

    A tag with direct project evidence makes a strong match; a merely implied one
    makes it worth considering. A direct hit on a persona or topic tag alone is
    strong only when the artifact fits the project (MIN_FIT); otherwise a generic
    review skill tagged `frontend` would be installed by `--add` in every React app.

    Within a tier, `weights` (see specificity) orders the matches: the sum of the
    matched tags' weights, scaled by how much of the artifact's observable tags the
    project matched, so an artifact about exactly this project beats one that merely
    touches it.

    `focus` only orders here: it sorts its own matches to the front of whichever
    tier they earned. The promotion happens one level up, in run(), which enters the
    focus tag into `direct` before calling this: asking for a tag is itself the
    evidence for it, so an artifact carrying it is a strong match and `--add` takes
    it. Keeping the two apart is what lets this function be tested for ordering
    alone.
    """
    # Naming a broad tag is the one way to mean it, so the focus is exempt from the
    # subtraction below. Otherwise `--focus observability` would quietly match
    # nothing at all, which reads as "no such tag" rather than "not by default".
    broad = fingerprint.BROAD_TAGS - {focus} if focus else fingerprint.BROAD_TAGS
    observable = fingerprint.OBSERVABLE_TAGS | ({focus} if focus else set())
    weights = weights or {}
    strong, consider = [], []
    for art in candidates:
        tags = set(art.groups) - broad
        # An artifact built for a framework the project does not use is noise,
        # however well its broader tags match.
        wanted_tech = tags & fingerprint.TECH_TAGS
        if wanted_tech and not wanted_tech & set(direct):
            continue
        hits = sorted(tags & set(direct))
        soft = sorted(tags & set(indirect))
        if not hits and not soft:
            continue
        matched = hits + soft
        fit = len(matched) / len(tags & (observable | set(matched)))
        specific = focus in hits or bool(set(hits) & fingerprint.TECH_TAGS) or fit >= MIN_FIT
        if hits and specific:
            bucket, why = strong, reason(hits, direct, focus, weights)
        else:
            bucket, why = consider, reason(matched, {**indirect, **direct}, focus, weights)
        bucket.append(
            Match(
                name=art.name,
                kind=art.type,
                groups=tuple(art.groups),
                description=describe(art),
                why=why,
                score=fit * sum(weights.get(tag, 1.0) for tag in matched),
            )
        )

    def order(match):
        return (0 if focus and focus in match.groups else 1, -match.score, match.kind, match.name)

    return sorted(strong, key=order), sorted(consider, key=order)


def closure(catalog, art):
    """(kind, name) of everything installing `art` brings with it, itself excluded.

    The same resolution `add` performs, so what the report says comes along is what
    actually would.
    """
    if art.type == cat.BUNDLE:
        resolution = cat.bundle_resolution(catalog, [art.name])
    elif art.type == cat.AGENT:
        resolution = cat.bundle_resolution(catalog, (), (), [art.name])
    else:
        resolution = cat.bundle_resolution(catalog, (), [art.name])
    brought = {(cat.SKILL, name) for name in resolution.skills}
    brought |= {(cat.AGENT, name) for name in resolution.agents}
    brought.discard((art.type, art.name))
    return brought


def fold(strong, consider, brings):
    """Drop matches another offered match already brings, and record what each adds.

    `brings` is {(kind, name): set of (kind, name)}. A match is folded into one in
    its own or a stronger tier, never a weaker one: a strong skill stays even when
    only a guessed bundle would bring it, or `--add` would lose it. Two matches that
    bring each other both stay, since folding either way would be arbitrary. Pure.
    """
    def key(match):
        return (match.kind, match.name)

    def adds(match):
        return tuple(sorted(brings.get(key(match), ()), key=lambda pair: (pair[0] != cat.AGENT, pair)))

    folded, above = [], []
    for tier in (strong, consider):
        bringers = above + tier
        kept = [
            replace(match, adds=adds(match))
            for match in tier
            if not any(
                key(match) in brings.get(key(other), ())
                and key(other) not in brings.get(key(match), ())
                for other in bringers
            )
        ]
        folded.append(kept)
        above = bringers
    return folded[0], folded[1]


def shortlist(strong, consider, cap=SHORTLIST_CAP):
    """Both tiers trimmed to a combined `cap`, strong matches served first."""
    strong = strong[:cap]
    return strong, consider[: max(cap - len(strong), 0)]


def install_commands(matches):
    """The commands that install `matches`, one per type.

    Not one command: `--type` applies to every name in a call, so a mixed
    shortlist cannot honestly be written as a single line.
    """
    by_kind = {}
    for match in matches:
        by_kind.setdefault(match.kind, []).append(match.name)
    return [
        f"kura add {' '.join(by_kind[kind])} --type {kind}"
        for kind in TYPE_ORDER
        if kind in by_kind
    ]


def _add_differs(strong, offered):
    """How the install command above differs from what `--add` would do.

    An empty strong tier gets its own sentence rather than "the 0 strong matches",
    which is arithmetic where the reader wants an answer: the honest reading is
    that `--add` has nothing to install here.
    """
    if not strong:
        return "`--add` would install nothing: every match here is a guess."
    plural = "" if len(strong) == 1 else "es"
    return (
        f"That installs all {len(offered)}. `--add` takes only the "
        f"{len(strong)} strong match{plural}."
    )


def _row(match, show_kind):
    """One recommendation's headline: the name, its type when ambiguous, its tags.

    Composed by hand rather than through ui.item because only the glyph and the
    suffixes are painted, exactly as `list` composes its rows.
    """
    parts = [f"  {colors.paint('·', 'dim')} {match.name}"]
    if show_kind:
        parts.append(colors.paint(f"({match.kind})", "dim"))
    if match.groups:
        parts.append(colors.paint("[" + ", ".join(match.groups) + "]", "cyan"))
    return " ".join(parts)


def _aside(label, text):
    gist = textwrap.shorten(text, DESCRIPTION_CAP, placeholder=" …")
    filled = textwrap.fill(gist, width=WRAP, initial_indent=label, subsequent_indent=" " * len(label))
    return ui.render("note", filled.replace("\n", "\n    "), indent=4)


def render(strong, consider, already, focus, project, emit=print):
    """Print the report and return the artifacts it offered, in display order.

    `emit` is the seam the tests capture on, as in doctor.report: a helper that
    could only print would put the palette out of their reach.
    """
    offered = [*strong, *consider]
    # More than one type in play, so a bare name no longer says which command
    # installs it. With one type the suffix would be on every row and inform nobody.
    show_kind = len({match.kind for match in offered}) > 1

    emit(ui.render("title", HEADER.format(ui.path(project))))
    emit("")

    # Without this a focus that matched nothing installable reads as an ordinary
    # report, and the unrelated fallback picks look like the answer to it.
    if focus and offered and not any(focus in match.groups for match in offered):
        emit(ui.render("warn", f"Nothing available carries the '{focus}' tag. Showing the rest."))
        emit("")

    for title, matches in ((STRONG, strong), (CONSIDER, consider)):
        if not matches:
            continue
        emit(ui.render("title", title))
        for match in matches:
            emit(_row(match, show_kind))
            emit(ui.render("note", f"Why:  {match.why}", indent=4))
            if match.description:
                emit(_aside("What: ", match.description))
            if match.adds:
                names = [name if kind != cat.AGENT else f"{name} (agent)" for kind, name in match.adds]
                emit(_aside("Adds: ", ", ".join(names)))
        emit("")

    if already:
        emit(ui.render("title", ALREADY))
        for art in already:
            tick = colors.paint("✓", "green")
            suffix = f" {colors.paint('(' + art.type + ')', 'dim')}" if show_kind else ""
            emit(f"  {tick} {art.name}{suffix}")
        emit("")

    if offered:
        for command in install_commands(offered):
            emit(ui.render("step", command))
        # The command above takes everything shown; --add takes the strong tier
        # alone. Reading one as shorthand for the other is the obvious mistake to
        # make, and it is only visible from the counts, so say it outright. Silent
        # when the tiers agree, since then there is no difference to warn about.
        if consider:
            emit(ui.render("note", _add_differs(strong, offered)))
    else:
        reason = (
            f"No artifact matches the focus '{focus}'."
            if focus
            else "Nothing left to add: everything relevant is already installed."
        )
        emit(ui.render("ok", reason))

    emit(
        ui.render(
            "done",
            f"{len(strong)} strong, {len(consider)} worth considering, "
            f"{len(already)} already here",
        )
    )
    return offered


def install(matches):
    """Add `matches` one type at a time, stopping at the first refusal.

    `add` takes one --type per call, so a mixed shortlist is several transactions;
    stopping keeps a refusal for one type from being followed by a second change.
    """
    by_kind = {}
    for match in matches:
        by_kind.setdefault(match.kind, []).append(match.name)
    for kind in TYPE_ORDER:
        if kind not in by_kind:
            continue
        code = add.run(SimpleNamespace(names=by_kind[kind], group=None, want_global=False, type=kind))
        if code != errors.OK:
            return code
    return errors.OK


def run(args):
    def operation():
        machine, catalog_root = common.machine()
        project = common.project_root()
        manifest = common.manifest(project)
        catalog = common.loaded_catalog(catalog_root)
        effective = scope.global_set(catalog)
        kinds = TYPE_ORDER if args.type is None else (args.type,)
        configured = configured_names(catalog, manifest)
        candidates, already = available(
            catalog,
            effective,
            configured,
            machine,
            catalog_root,
            paths.home(),
            kinds,
        )

        direct = fingerprint.read(project)
        indirect = fingerprint.implied(direct)
        if not fingerprint.covered(direct):
            for tag, evidence in fingerprint.fallback(direct).items():
                indirect.setdefault(tag, evidence)
        if args.focus:
            if not any(cat.in_group(catalog, kind, args.focus) for kind in kinds):
                ui.warn(
                    f"nothing in the catalogue carries '{args.focus}', so --focus did nothing",
                    stream=sys.stderr,
                )
                ui.note("`kura list --type TYPE` prints each artifact with its tags.", stream=sys.stderr)
            direct.setdefault(args.focus, f"requested focus '{args.focus}'")
            indirect.pop(args.focus, None)

        def wanted(pair):
            art = cat.get(catalog, *pair)
            if pair[1] in configured.get(pair[0], ()):
                return False
            return art is None or not scope.belongs_global(art, effective)

        weights = specificity([art for kind in TYPE_ORDER for art in cat.visible(catalog, kind)])
        brings = {
            (art.type, art.name): {pair for pair in closure(catalog, art) if wanted(pair)}
            for art in candidates
        }
        strong, consider = shortlist(
            *fold(*rank(candidates, direct, indirect, args.focus, weights), brings)
        )
        render(strong, consider, already, args.focus, project)
        if not args.add or not strong:
            return errors.OK
        ui.blank()
        return install(strong)

    return common.run_guarded(operation)
