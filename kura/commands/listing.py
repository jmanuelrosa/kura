"""List catalog skills, declared intent, and every selected native view."""

import json
import sys
from pathlib import Path

from .. import catalog as cat
from .. import colors, config, errors, harnesses, paths, scope, state, ui, views
from . import common

LINKED = "linked"
AVAILABLE = "available"
MISSING = "missing"
DRIFT = "drift"
GROUPS_HEADER = "📚 Available groups:"


def _parents(catalog, manifest):
    if manifest is None:
        return {}
    parents = {}
    for direct in manifest.skills:
        for name in cat.resolve(catalog, [direct]).names:
            if name != direct:
                parents.setdefault(name, []).append(direct)
    return {name: tuple(sorted(values)) for name, values in parents.items()}


def _physical_scope(catalog_root, art, home, project, global_harnesses, selected):
    roots = views.accepted_skill_roots(catalog_root)
    global_ids = tuple(sorted(set(global_harnesses) | set(selected)))
    for installed, view_project, harness_ids in (
        (scope.GLOBAL, None, global_ids),
        (scope.PROJECT, project, selected if project is not None else ()),
    ):
        for harness_id in harness_ids:
            path = harnesses.skill_path(harness_id, art.name, home, view_project)
            if views.classify(path, art.source, roots).state in (views.CURRENT, views.STALE):
                return installed
    return None


def _unknown_views(home, project, selected, name):
    output = {}
    for harness_id in selected:
        path = harnesses.skill_path(harness_id, name, home, project)
        if path.is_symlink():
            output[harness_id] = {
                "state": views.FOREIGN,
                "target": str(scope.link_target(path)),
            }
        elif path.exists():
            output[harness_id] = {"state": views.REAL}
        else:
            output[harness_id] = {"state": views.MISSING}
    return output


def _view_state(path, source, roots, exact_sources=()):
    if path is None:
        return {"state": views.MISSING}
    detail = views.classify(path, source, roots, exact_sources)
    output = {"state": detail.state}
    if detail.target is not None:
        output["target"] = str(detail.target)
    return output


def _agent_views(catalog_root, home, project, machine, harness_ids, name, art, exact_sources=()):
    roots = views.accepted_agent_roots(catalog_root)
    return {
        harness_id: _view_state(
            harnesses.agent_path(harness_id, name, home, project, machine),
            art.source,
            roots,
            exact_sources,
        )
        for harness_id in harness_ids
    }


def _member_views(catalog_root, home, project, machine, harness_ids, skill_map, agent_map):
    output = {}
    skill_sources = tuple(art.source for art in skill_map.values())
    agent_sources = tuple(art.source for art in agent_map.values())
    for name, art in sorted(skill_map.items()):
        for harness_id in harness_ids:
            output[f"{harness_id} skill {name}"] = _view_state(
                harnesses.skill_path(harness_id, name, home, project),
                art.source,
                views.accepted_skill_roots(catalog_root),
                skill_sources,
            )
    for name, art in sorted(agent_map.items()):
        for harness_id, detail in _agent_views(catalog_root, home, project, machine, harness_ids, name, art, agent_sources).items():
            output[f"{harness_id} agent {name}"] = detail
    return output


def _agent_rows(catalog, catalog_root, machine, home, project, manifest):
    selected = manifest.harnesses if manifest else ()
    _, project_agents = views.project_sources(catalog, manifest) if manifest else ({}, {})
    exact_sources = tuple(art.source for art in project_agents.values())
    output = []
    global_ids = machine.global_harnesses if machine else ()
    for art in cat.visible(catalog, cat.AGENT):
        selected_source = project_agents.get(art.name)
        is_configured = selected_source is not None and selected_source.source == art.source
        is_global = art.metadata and art.tagged_global
        project_views = _agent_views(catalog_root, home, project, machine, selected if is_configured else (), art.name, art, exact_sources)
        global_views = _agent_views(catalog_root, home, None, machine, global_ids if is_global else (), art.name, art)
        if is_configured and is_global:
            view_map = {
                harness_id: (
                    global_views[harness_id]
                    if harness_id in global_ids and global_views[harness_id]["state"] != views.MISSING
                    else project_views[harness_id]
                )
                for harness_id in selected
            }
        else:
            view_map = project_views if is_configured else global_views
        healthy = bool(view_map) and all(value["state"] == views.CURRENT for value in view_map.values())
        on_disk = art.source is not None and art.source.is_file()
        row_state = MISSING if not on_disk else LINKED if healthy else DRIFT if is_configured or is_global else AVAILABLE
        installed = scope.GLOBAL if healthy and is_global else scope.PROJECT if healthy else None
        output.append({"name": art.name, "state": row_state, "installed": installed, "global": is_global, "groups": tuple(sorted(set(art.groups))), "dependencies": tuple(sorted(set(art.dependencies))), "reason": state.DIRECT if manifest and art.name in manifest.agents else None, "parent": None, "global_for": (), "views": view_map})
    bundle_map = cat.bundles(catalog)
    for bundle_name in sorted(bundle_map):
        bundle = bundle_map[bundle_name]
        for art in bundle.agents:
            name = art.name
            is_configured = name in project_agents and bundle_name in (manifest.bundles if manifest else ())
            view_map = _agent_views(catalog_root, home, project, machine, selected if is_configured else (), name, art, exact_sources)
            healthy = bool(view_map) and all(value["state"] == views.CURRENT for value in view_map.values())
            row_state = LINKED if is_configured and healthy else DRIFT if is_configured else AVAILABLE
            output.append({"name": name, "state": row_state, "installed": scope.PROJECT if healthy else None, "global": False, "groups": (), "dependencies": (), "reason": state.dep_of(bundle_name) if is_configured else None, "parent": bundle_name if is_configured else None, "global_for": (), "views": view_map})
    missing = set(manifest.agents if manifest else ()) - set(project_agents)
    for name in sorted(missing):
        output.append({"name": name, "state": MISSING, "installed": None, "global": False, "groups": (), "dependencies": (), "reason": state.DIRECT, "parent": None, "global_for": (), "views": _unknown_agent_views(home, project, machine, selected, name)})
    return sorted(output, key=lambda row: (row["name"], row["parent"] or ""))


def _unknown_agent_views(home, project, machine, selected, name):
    output = {}
    for harness_id in selected:
        path = harnesses.agent_path(harness_id, name, home, project, machine)
        if path is None:
            output[harness_id] = {"state": views.MISSING}
        elif path.is_symlink():
            output[harness_id] = {"state": views.FOREIGN, "target": str(scope.link_target(path))}
        elif path.exists():
            output[harness_id] = {"state": views.REAL}
        else:
            output[harness_id] = {"state": views.MISSING}
    return output


def _bundle_rows(catalog, catalog_root, machine, home, project, manifest):
    selected = manifest.harnesses if manifest else ()
    output = []
    bundle_map = cat.bundles(catalog)
    for name, bundle in sorted(bundle_map.items()):
        selected_bundle = manifest and name in manifest.bundles
        bundle_intent = state.Manifest(selected, schema_version=state.V2_SCHEMA_VERSION, bundles=(name,))
        skill_map, agent_map = views.project_sources(catalog, bundle_intent) if selected_bundle else ({}, {})
        view_map = _member_views(catalog_root, home, project, machine, selected, skill_map, agent_map) if selected_bundle else {}
        healthy = bool(view_map) and all(value["state"] == views.CURRENT for value in view_map.values())
        output.append({"name": name, "state": LINKED if healthy else DRIFT if selected_bundle else AVAILABLE, "installed": scope.PROJECT if healthy else None, "global": False, "groups": (), "dependencies": tuple(sorted((*bundle.requires_skills, *bundle.requires_agents))), "reason": state.DIRECT if selected_bundle else None, "parent": None, "global_for": (), "views": view_map})
    for name in sorted(set(manifest.bundles if manifest else ()) - set(bundle_map)):
        output.append({"name": name, "state": MISSING, "installed": None, "global": False, "groups": (), "dependencies": (), "reason": state.DIRECT, "parent": None, "global_for": (), "views": {}})
    return output


def rows(catalog, catalog_root, machine, home, project, manifest, group=None):
    effective = set(scope.global_set(catalog))
    resolution = cat.resolve(catalog, manifest.skills) if manifest else cat.Resolution(())
    configured = set(manifest.skills if manifest else ()) | set(resolution.names)
    selected = manifest.harnesses if manifest else ()
    global_harnesses = machine.global_harnesses if machine else ()
    parents = _parents(catalog, manifest)
    global_parents = scope.global_parents(catalog)
    output = []
    for art in cat.visible(catalog, cat.SKILL):
        if isinstance(group, str) and group not in art.groups:
            continue
        is_global = art.name in effective
        is_configured = art.name in configured
        view_map = {}
        if is_configured:
            project_views = views.view_states(
                catalog_root, catalog, home, project, selected, [art.name]
            )[art.name]
            if is_global:
                global_views = views.view_states(
                    catalog_root, catalog, home, None, selected, [art.name]
                )[art.name]
                view_map = {
                    harness_id: (
                        project_views[harness_id]
                        if harness_id not in global_harnesses
                        or global_views[harness_id]["state"] == views.MISSING
                        else global_views[harness_id]
                    )
                    for harness_id in selected
                }
            else:
                view_map = project_views
        elif is_global:
            view_map = views.view_states(
                catalog_root,
                catalog,
                home,
                None,
                global_harnesses,
                [art.name],
            )[art.name]
        else:
            scratch = views.view_states(
                catalog_root,
                catalog,
                home,
                None,
                global_harnesses,
                [art.name],
            )[art.name]
            if any(detail["state"] != views.MISSING for detail in scratch.values()):
                view_map = scratch

        healthy = bool(view_map) and all(value["state"] == views.CURRENT for value in view_map.values())
        on_disk = art.source.is_dir()
        if not on_disk:
            row_state = MISSING
        elif is_configured or is_global:
            row_state = LINKED if healthy else DRIFT
        elif view_map:
            row_state = LINKED if healthy else DRIFT
        else:
            row_state = AVAILABLE
        installed = _physical_scope(
            catalog_root,
            art,
            home,
            project,
            global_harnesses,
            selected,
        )
        reason = None
        parent = None
        if manifest and art.name in manifest.skills:
            reason = state.DIRECT
        elif art.name in parents:
            parent = parents[art.name][0]
            reason = state.dep_of(parent)
        output.append(
            {
                "name": art.name,
                "state": row_state,
                "installed": installed,
                "global": is_global or installed == scope.GLOBAL,
                "groups": tuple(sorted(set(art.groups))),
                "dependencies": tuple(sorted(set(art.dependencies))),
                "reason": reason,
                "parent": parent,
                "global_for": global_parents.get(art.name, ()),
                "views": view_map,
            }
        )

    missing_parents = {name: parent for parent, name in resolution.missing_dependencies}
    missing_names = set(resolution.missing_direct) | set(missing_parents)
    for name in sorted(missing_names - {row["name"] for row in output}):
        direct = name in set(manifest.skills)
        parent = None if direct else missing_parents.get(name)
        output.append(
            {
                "name": name,
                "state": MISSING,
                "installed": None,
                "global": False,
                "groups": (),
                "dependencies": (),
                "reason": state.DIRECT if direct else state.dep_of(parent),
                "parent": parent,
                "global_for": (),
                "views": _unknown_views(home, project, selected, name),
            }
        )
    return sorted(output, key=lambda row: row["name"])


def _view_detail(row):
    if not row["views"]:
        return ""
    values = []
    for harness_id, detail in row["views"].items():
        suffix = f" -> {detail['target']}" if "target" in detail else ""
        values.append(f"{harness_id} {detail['state']}{suffix}")
    prefix = "linked" if row["state"] == LINKED else "drift"
    return f" ({prefix}: {'; '.join(values)})"


def format_row(row, indent="  ", show_groups=True):
    if row["state"] == LINKED:
        marker = f"{colors.paint('✓', 'green')} {row['name']}"
    elif row["state"] == DRIFT:
        marker = f"{colors.paint('!', 'magenta')} {row['name']}"
    elif row["state"] == MISSING:
        marker = colors.paint(f"↓ {row['name']} (missing)", "dim")
    else:
        marker = f"{colors.paint('·', 'dim')} {row['name']}"
    parts = [indent + marker + _view_detail(row)]
    if show_groups and row["groups"]:
        parts.append(colors.paint("[" + ", ".join(row["groups"]) + "]", "cyan"))
    if row["dependencies"]:
        parts.append(colors.paint("(needs: " + ", ".join(row["dependencies"]) + ")", "dim"))
    if row["parent"]:
        parts.append(colors.paint(f"(derived from {row['parent']})", "dim"))
    return " ".join(parts)


def grouped(listed):
    buckets = {}
    for row in listed:
        for tag in row["groups"]:
            buckets.setdefault(tag, []).append(row)
    return sorted(buckets.items())


def run(args):
    def operation():
        if args.json and args.group is True:
            raise common.Refusal(
                errors.USAGE,
                "--json emits rows; every row already carries its groups.",
            )
        home = paths.home()
        try:
            machine = config.read(home)
            catalog_root = config.effective_catalog(home)
        except config.Malformed as exc:
            raise common.Refusal(errors.DRIFT, f"Invalid machine configuration: {exc}") from exc
        catalog = common.loaded_catalog(catalog_root)
        project = scope.project_root(Path.cwd(), home)
        manifest = None
        if project is not None and state.path_for(project).is_file():
            manifest = common.manifest(project)
        elif project is not None:
            ui.note(
                "This directory is not initialized. Project state is omitted; run `kura init` here.",
                stream=sys.stderr,
                indent=0,
            )
        listing_type = getattr(args, "type", None) or cat.SKILL
        if listing_type == cat.AGENT:
            if args.group is not None:
                raise common.Refusal(errors.USAGE, "agent listing does not support --group")
            listed = _agent_rows(catalog, catalog_root, machine, home, project, manifest)
        elif listing_type == cat.BUNDLE:
            if args.group is not None:
                raise common.Refusal(errors.USAGE, "bundle listing does not support --group")
            listed = _bundle_rows(catalog, catalog_root, machine, home, project, manifest)
        else:
            listed = rows(catalog, catalog_root, machine, home, project, manifest, args.group)
        if args.json:
            print(json.dumps(listed))
            return errors.OK
        if args.group is True:
            ui.title(GROUPS_HEADER)
            for tag, members in grouped(listed):
                print(f"  {colors.paint(tag + ':', 'cyan')}")
                for row in members:
                    print(format_row(row, indent="    ", show_groups=False))
            return errors.OK
        noun = {cat.SKILL: "skills", cat.AGENT: "agents", cat.BUNDLE: "bundles"}[listing_type]
        ui.title(f"🧩 Available {noun}:")
        for row in listed:
            print(format_row(row))
        configured = len([row for row in listed if row["reason"] is not None])
        links = sum(
            1
            for row in listed
            for detail in row["views"].values()
            if detail["state"] == views.CURRENT
        )
        ui.blank()
        ui.done(f"{len(listed)} {noun}, {configured} configured, {links} links current")
        return errors.OK

    return common.run_guarded(operation)
