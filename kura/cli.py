"""Argument parsing and dispatch.

`--type` is required on every command except `scout`, `sync`, `doctor` and `adopt`, where
it narrows an otherwise cross-type result, and `trust`, which does not take it at all:
workspace trust is a property of a directory, not of an artifact. Nothing is inferred
from a name: because the type is always explicit, the three namespaces are allowed to
overlap.

The help is painted in the same palette the commands use (colors.py): bold for a
section heading, as in `list`'s "Available skills:"; cyan for a name, as in its group
tags and `outdated`'s repo rules; dim for a de-emphasised aside; magenta for a refusal.
"""

import argparse
import importlib
import sys

from . import errors
from . import colors, ui

# Python 3.14 paints argparse's own output in its own theme (blue usage, magenta prog,
# green flags), which would show through ours as nested escapes and a palette the
# commands never use. Switched off rather than layered under. The kwarg does not exist
# before 3.14, and this package runs on whatever python3 the machine has.
#
# Read off the code object rather than with inspect.signature: importing inspect costs
# more than half of this module's import time, and every run pays it, including the
# usage errors the lazy command imports below exist to keep cheap.
NO_ARGPARSE_COLOR = (
    {"color": False} if "color" in argparse.ArgumentParser.__init__.__code__.co_varnames else {}
)

TYPES = ("skill",)

COMMANDS = {
    "init": "Initialize this exact directory for selected harnesses",
    "config": "Show or change machine configuration",
    "list": "Show skills and their native harness views",
    "scout": "Recommend metadata-backed skills for the current project",
    "add": "Add direct project intent or temporary global skill links",
    "remove": "Remove direct project intent or temporary global skill links",
    "sync": "Converge registry-global skills across enabled harnesses",
    "update": "Fetch skills from their upstream repos",
    "outdated": "Report which skills are behind upstream",
    "doctor": "Report configuration and native-view drift",
    "adopt": "Adopt catalog-backed native links as direct intent",
    "restore": "Recreate missing links without deleting anything",
    "trust": "Show or change selected harness trust",
    "converge": "Reconcile selected native skill views",
}

# Which module runs each command. `update` and `outdated` share one: they are the same
# traversal with writes switched off. Adding a command means adding it here, to
# COMMANDS, to FAMILIES and to SCOPE, and test_help.py fails on three of the four.
#
# Two names do not match their module. `list` shadows a builtin, and `sync` would have
# collided with the module that had the name first: commands/pull.py serves `update` and
# `outdated`, and fetching from upstream is a different act from converging ~/.claude.
MODULE = {
    "init": "init",
    "config": "config",
    "list": "listing",
    "scout": "scout",
    "add": "add",
    "remove": "remove",
    "sync": "provision",
    "update": "pull",
    "outdated": "pull",
    "doctor": "doctor",
    "adopt": "adopt",
    "restore": "restore",
    "trust": "trust",
    "converge": "converge",
}

# The families differ in what they touch, which is what decides whether --global is
# even a question. Grouping them is the whole point of the epilog.
#
# **Each title is a claim about every command beneath it**, so a family must be uniform
# on the flag or its title cannot be right about it. That is why `add` and `remove` sit
# apart from the other four scope-aware commands rather than with them: those two write
# somewhere you choose, the rest have exactly one right answer given the cwd and take no
# flag at all. One combined "scope-aware ... with --global" family read as a promise to
# all six, which sent readers looking for `list --global` and got them a refusal about
# --type instead. Splitting says it where the commands are, so no footnote has to.
#
# `sync` stays out of both: it acts on ~/.claude and only ever on ~/.claude, so --global
# is not an option there but the implied and only scope, and listing it as scope-chosen
# would suggest a project run that does not exist.
FAMILIES = (
    (
        "Scope-chosen (project intent, or temporary global views with --global)",
        ("add", "remove"),
    ),
    (
        "Setup and machine configuration (--global does not apply)",
        ("init", "config"),
    ),
    (
        "Project and catalog operations (--global does not apply)",
        ("list", "scout", "doctor", "adopt", "restore"),
    ),
    (
        "Global native views (--global does not apply)",
        ("sync",),
    ),
    (
        "Registry-backed upstream sources (--global does not apply)",
        ("update", "outdated"),
    ),
    # A fifth family rather than a sixth scope-fixed command, because the other four
    # titles are all claims about which .claude/ a command touches and this one touches
    # none of them: it reads and writes Claude Code's own ~/.claude.json. Filing it under
    # "the cwd decides" would have been true and would still have put it in a list of
    # artifact commands, where a reader looking for why a plugin does not load has no
    # reason to look.
    (
        "Harness trust for the current project (--global does not apply)",
        ("trust",),
    ),
    # A sixth family for one command, because none of the five titles is true about it.
    # It writes into a project's `.agents/`, never its `.claude/`, so "the cwd decides"
    # would file it beside commands that install artifacts, where a reader wondering why
    # pi loads none of their skills has no reason to look. It is also the one project
    # command that can act on every project at once, which no scope-fixed title can claim.
    (
        "Project native-view convergence (--global does not apply)",
        ("converge",),
    ),
)

SCOPE = {
    "init": (
        "Uses cwd as the exact project, creates root kura.json, and projects skills into each selected harness."
    ),
    "config": (
        "Reads or changes the XDG machine configuration and converges global native views."
    ),
    "list": (
        "Reads the catalog, global native views, and root kura.json in cwd when present."
    ),
    "scout": (
        "Requires root kura.json in cwd and recommends only metadata-backed project skills."
    ),
    "add": (
        "Changes direct intent in root kura.json, or temporary global views with --global."
    ),
    "remove": (
        "Changes direct intent in root kura.json, or temporary global views with --global."
    ),
    "sync": (
        "Converges registry-global skills across every globally enabled harness."
    ),
    "update": (
        "Acts on this repo's skill sources against upstream. Tied to neither a "
        "project nor ~/.claude, and covers skills only."
    ),
    "outdated": (
        "Acts on this repo's skill sources against upstream. Tied to neither a "
        "project nor ~/.claude, and covers skills only."
    ),
    "doctor": (
        "Inspects machine, catalog, project, native-view, instruction, executable, and trust state."
    ),
    "adopt": (
        "Requires root kura.json and adopts agreeing catalog-backed selected harness views."
    ),
    "restore": (
        "Requires root kura.json and recreates missing selected harness links without deleting."
    ),
    "converge": (
        "Reconciles the cwd project, or recursively scans cwd or explicit roots with --all."
    ),
    "trust": (
        "Uses root kura.json in cwd and reads or changes each selected available harness store."
    ),
}


class _Painted:
    """Colour argparse's own sections and flags, without disturbing its layout.

    Both hooks paint text argparse has already measured. Painting earlier would feed
    escape codes into the widths it aligns on: `_format_action` pads the invocation
    column with `%-*s`, so a cyan `--global` would count ten invisible characters and
    under-pad every row in the block.
    """

    def _format_usage(self, usage, actions, groups, prefix):
        text = super()._format_usage(usage, actions, groups, prefix)
        if prefix is not None:
            # add_subparsers derives each subcommand's prog by formatting the parent's
            # usage with prefix='', so painting that call would bake escape codes into
            # every `kura COMMAND` prog and skew the width its usage wraps on.
            # A default prefix means this is the copy a human reads.
            return text
        for plain, colour in (("usage:", "bold"), (self._prog, "cyan")):
            painted = colors.paint(plain, colour)
            if painted != plain:
                text = text.replace(plain, painted, 1)
        return text

    def start_section(self, heading):
        # argparse appends the colon itself, so only the word is painted. Bold on a
        # colon is invisible anyway.
        super().start_section(colors.paint(heading, "bold") if heading else heading)

    def _format_action(self, action):
        text = super()._format_action(action)
        invocation = self._format_action_invocation(action)
        painted = colors.paint(invocation, "cyan")
        if invocation and painted != invocation:
            # The header is the first thing in the block, so the first occurrence is it.
            text = text.replace(invocation, painted, 1)
        return text


class Help(_Painted, argparse.HelpFormatter):
    """For subparsers, whose descriptions wrap to the terminal."""


class RawHelp(_Painted, argparse.RawDescriptionHelpFormatter):
    """For the root parser, whose epilog is a table and must not be rewrapped."""


class Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which is NOT_FOUND in our table.

    It also reports one usage problem at a time, and two of its orderings made a
    mistyped flag read as an unrelated complaint:

    - A missing required argument is raised inside `parse_known_args`, before extras
      are ever looked at, so `list --global` only ever said `--type` was missing. Fix
      one and a second, different refusal appears.
    - Extras are caught by the *root* parser, after the subparser has parsed cleanly,
      so `list --type skill --global` printed `usage: kura [-h] COMMAND ...` and
      named none of `list`'s own flags.

    Both are addressed by keeping the argv each parser was handed and choosing the
    parser that reports, rather than by touching argparse's parsing itself.
    """

    #: The argv this parser was handed, recorded by parse_known_args. Subparsers are
    #: Parser instances too (add_subparsers takes parser_class from type(self)), and
    #: _SubParsersAction calls parse_known_args on them with their own slice, so each
    #: ends up holding exactly its own tokens.
    _argv = ()

    #: Set on the root parser only, to route an extras refusal at the subcommand.
    subcommands = {}

    def parse_known_args(self, args=None, namespace=None):
        self._argv = tuple(sys.argv[1:] if args is None else args)
        return super().parse_known_args(args, namespace)

    def parse_args(self, args=None, namespace=None):
        # argparse's own two lines, with the error target swapped: the subcommand knows
        # its flags and the root does not, so its usage is the one worth printing.
        parsed, extras = self.parse_known_args(args, namespace)
        if extras:
            target = self.subcommands.get(getattr(parsed, "command", None), self)
            target.error("unrecognized arguments: %s" % " ".join(extras))
        return parsed

    def _unknown_flags(self):
        """The `--`-prefixed tokens in our argv that this parser does not define.

        Matched by prefix rather than equality because allow_abbrev is on, so `--typ`
        is a legal spelling of `--type` and must not be reported. Only `--` tokens are
        considered: a single dash can begin a value, and a false accusation about one
        would be worse than the silence.
        """
        unknown = []
        for token in self._argv:
            if token == "--":
                break
            if not token.startswith("--"):
                continue
            name = token.split("=", 1)[0]
            if not any(known.startswith(name) for known in self._option_string_actions):
                unknown.append(name)
        return unknown

    def error(self, message):
        # Fold in what argparse would only have told us on the next run. Skipped when
        # the message is already the extras refusal, or `--global` would be named twice.
        unknown = [] if message.startswith("unrecognized") else self._unknown_flags()
        if unknown:
            message = f"unrecognized arguments: {' '.join(unknown)}; {message}"
        # The usage line is painted by a formatter that cannot know where it will be
        # printed, and a refusal goes to stderr rather than stdout. for_stream is what
        # decides against the right one, so a redirected stderr gets clean text.
        usage = colors.for_stream(self.format_usage(), sys.stderr)
        self.exit(errors.USAGE, f"{usage}{colors.cross(sys.stderr)} {message}\n")


def _add_type(parser, required=True):
    parser.add_argument(
        "--type",
        dest="type",
        choices=TYPES,
        required=required,
        metavar="skill",
        help="artifact type (skills are the only managed type in this phase)",
    )


def _add_group(parser, verb):
    """`--group TAG` for add and remove, which take a tag instead of names.

    Deliberately not `list`'s shape: there a bare --group means "bucket the listing",
    which has no analogue when the flag has to name a set to act on.
    """
    parser.add_argument(
        "--group",
        default=None,
        metavar="TAG",
        help=f"{verb} every artifact tagged TAG instead of naming them. Members split "
        f"by scope: --global picks the global half, its absence the project half.",
    )


def _epilog():
    """Render FAMILIES as the command listing argparse cannot produce itself.

    A parser takes exactly one subparsers action, so the families cannot be argument
    groups. Generating this from COMMANDS rather than writing it out keeps the
    one-liners in a single place.
    """
    width = max(len(name) for name in COMMANDS)
    blocks = [
        "\n".join(
            [
                colors.paint(f"{title}:", "bold"),
                # Padded before painting, so the description column lines up whether
                # colour is on or off.
                *(
                    f"  {colors.paint(name, 'cyan')}{' ' * (width - len(name))}  "
                    f"{COMMANDS[name]}"
                    for name in names
                ),
            ]
        )
        for title, names in FAMILIES
    ]
    # No --global footnote: each title now carries that for its own members, which is
    # the point of the split. A dim line at the foot of the page was the worst place
    # for the one fact the listing had been missing.
    blocks.append(
        colors.paint("Run `kura COMMAND --help` for a command's flags and its scope.", "dim")
    )
    return "\n\n".join(blocks)


def _command(sub, name):
    """Add a subparser that contributes no line to argparse's own listing.

    Omitting `help` is what suppresses that listing, leaving the epilog as the
    only one. The description carries the same one-liner into `COMMAND --help`,
    where until now there was no prose at all.

    formatter_class has to be passed per subparser: they inherit parser_class from the
    root parser but not the formatter, so without this only `kura -h` is painted.
    """
    return sub.add_parser(
        name,
        description=f"{COMMANDS[name]}. {SCOPE[name]}",
        formatter_class=Help,
        **NO_ARGPARSE_COLOR,
    )


def build_parser():
    parser = Parser(
        prog="kura",
        description="Manage one declared skill set across Claude Code and Pi native views.",
        epilog=_epilog(),
        # Raw only here, to hold the epilog's columns. Subparsers get Help instead, so
        # their descriptions keep wrapping to the terminal.
        formatter_class=RawHelp,
        **NO_ARGPARSE_COLOR,
    )
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        help="one of the commands below",
    )
    # So parse_args can hand an extras refusal to the subcommand whose flags it is
    # actually about. Only the root gets one; a subparser keeps the empty class default.
    parser.subcommands = sub.choices

    init = _command(sub, "init")
    init.add_argument(
        "--harness",
        dest="harnesses",
        action="append",
        default=[],
        choices=("claude", "pi"),
        help="select a project harness (repeatable)",
    )
    init.add_argument(
        "--global-harness",
        dest="global_harnesses",
        action="append",
        default=[],
        choices=("claude", "pi"),
        help="enable a global harness during first machine setup (repeatable)",
    )
    init.add_argument("--yes", action="store_true", help="apply the complete safe plan")
    init.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    init.add_argument("--verbose", action="store_true", help="show every conversion and native link")

    config_command = _command(sub, "config")
    config_command.add_argument(
        "--harness",
        dest="harnesses",
        action="append",
        default=None,
        choices=("claude", "pi"),
        help="replace global harnesses with this repeatable selection",
    )
    config_command.add_argument("--yes", action="store_true", help="apply the complete safe plan")
    config_command.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    config_command.add_argument("--verbose", action="store_true", help="show every native link decision")

    listing = _command(sub, "list")
    _add_type(listing)
    # Bare `--group` is the grouped view;
    # `--group <tag>` narrows to one tag. const distinguishes "flag given without a
    # value" from "flag absent", which a plain optional argument cannot express.
    listing.add_argument(
        "--group",
        nargs="?",
        const=True,
        default=None,
        metavar="TAG",
        help="With no value, group the listing by tag. With a tag, show only that tag.",
    )
    # The machine-readable half of the same answer, and the reason the Television cables
    # need no catalogue logic of their own. Only on `list`: it is the one command whose
    # whole output is a set of facts rather than a report of what it did.
    listing.add_argument(
        "--json",
        action="store_true",
        help="Print the rows as JSON on stdout instead of rendering them.",
    )

    # --type is optional for the same reason as doctor's and adopt's: a project's stack
    # implies artifacts of all three kinds, and requiring it would make a partial answer
    # the only one available. Given, it narrows the whole report.
    scout = _command(sub, "scout")
    _add_type(scout, required=False)
    scout.add_argument(
        "--focus",
        metavar="TAG",
        help="Promote this metadata group to a strong match (e.g. testing)",
    )
    scout.add_argument(
        "--add",
        dest="add",
        action="store_true",
        help="Install the strong matches instead of only listing them",
    )

    # `names` is nargs="*" on add and remove only because --group is the other way to
    # say what to act on. Neither of them, and both of them, are refused in run():
    # argparse can express "at least one positional" but not "exactly one of these two".
    add = _command(sub, "add")
    _add_type(add)
    add.add_argument("names", nargs="*", metavar="NAME")
    add.add_argument(
        "--global",
        dest="want_global",
        action="store_true",
        help="Create temporary links in every globally enabled harness",
    )
    _add_group(add, "Install")

    remove = _command(sub, "remove")
    _add_type(remove)
    remove.add_argument("names", nargs="*", metavar="NAME")
    remove.add_argument(
        "--global",
        dest="want_global",
        action="store_true",
        help="Remove temporary links from every globally enabled harness",
    )
    remove.add_argument(
        "--no-cascade",
        dest="no_cascade",
        action="store_true",
        help="Deprecated compatibility flag; dependencies are derived from kura.json",
    )
    _add_group(remove, "Act on")

    # --type is optional for the same reason as doctor's: converging ~/.claude is
    # cross-type by nature, and a required --type would make a partial run the only
    # thing the `ai` role could ask for. Given, it narrows both halves of the run.
    sync = _command(sub, "sync")
    _add_type(sync, required=False)
    sync.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show what would be linked and unlinked without touching anything",
    )

    update = _command(sub, "update")
    _add_type(update)
    update.add_argument("names", nargs="*", metavar="NAME")

    outdated = _command(sub, "outdated")
    _add_type(outdated)
    outdated.add_argument("names", nargs="*", metavar="NAME")

    doctor = _command(sub, "doctor")
    _add_type(doctor, required=False)

    # --type is optional here for the same reason as doctor: one kura.json
    # holds all three types, so requiring it could only ever write a partial file.
    adopt = _command(sub, "adopt")
    _add_type(adopt, required=False)
    adopt.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show what would be recorded without writing anything",
    )

    # Optional for adopt's reason, from the other side: one manifest holds all three
    # collections, so a required --type could only ever restore part of a project.
    restore = _command(sub, "restore")
    _add_type(restore, required=False)
    restore.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show what would be linked without touching anything",
    )

    # No _add_type, for trust's reason from the other side: what reaches pi is decided
    # by what is on disk, and a --type could only ever converge half of a view whose
    # whole purpose is to mirror the other directory exactly.
    converge = _command(sub, "converge")
    _add_type(converge, required=False)
    converge.add_argument(
        "--all",
        dest="all",
        action="store_true",
        help="Sweep every discovered project instead of only the cwd",
    )
    converge.add_argument(
        "--root",
        dest="roots",
        action="append",
        default=[],
        metavar="PATH",
        help="With --all, a tree to scan recursively (repeatable; default: cwd)",
    )
    converge.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show what would be linked without touching anything",
    )
    output = converge.add_mutually_exclusive_group()
    output.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        help="With --all, show steady-state details for every project",
    )
    output.add_argument(
        "--quiet",
        dest="quiet",
        action="store_true",
        help="Print nothing on stdout, for running from a hook. Warnings go to stderr.",
    )

    # No _add_type: this is the one command that acts on a directory rather than on an
    # artifact, so there is no kind to narrow and run() reads no args.type.
    trust = _command(sub, "trust")
    # Mutually exclusive rather than one --trust=BOOL: the two are opposite intentions
    # and argparse can refuse both at once only when they are separate flags.
    switch = trust.add_mutually_exclusive_group()
    switch.add_argument(
        "--on",
        dest="turn_on",
        action="store_true",
        help="Trust this project in every selected available harness",
    )
    switch.add_argument(
        "--off",
        dest="turn_off",
        action="store_true",
        help="Record a native refusal for this project",
    )
    trust.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show trust targets without changing either harness store",
    )

    return parser


def _dispatch(args):
    # Imported lazily so a usage error costs no registry read: A1 requires that a
    # missing --type touch nothing at all. import_module defers exactly as a `from`
    # statement does, so the table costs that guarantee nothing.
    module = importlib.import_module(f".commands.{MODULE[args.command]}", __package__)
    return module.run(args)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except OSError as exc:
        detail = exc.strerror or str(exc)
        return fail(errors.USAGE, f"{detail}: {exc.filename}")


def fail(code, message):
    ui.err(message)
    return code
