import json

import pytest

from kura import catalog as cat
from kura import config, state


def catalog_with_registry(tmp_path, registry):
    root = tmp_path / "catalog"
    (root / "skills").mkdir(parents=True)
    (root / "skill-registry.json").write_text(json.dumps(registry))
    return root


def write_skill(root, name, frontmatter=None):
    directory = root / "skills" / name
    directory.mkdir()
    block = frontmatter or f"name: {name}"
    (directory / "SKILL.md").write_text(f"---\n{block}\n---\n")


@pytest.mark.parametrize("location", ["local", "repo"])
@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "nested/name", "../escape", "nested\\name"],
)
def test_registry_names_must_be_one_safe_path_component(tmp_path, location, name):
    if location == "local":
        registry = {"local": [{"name": name}]}
    else:
        registry = {
            "upstream": {
                "owner/repo": {
                    "skills": [{"name": name, "upstream_path": "skills/review"}]
                }
            }
        }
    root = catalog_with_registry(tmp_path, registry)

    with pytest.raises(ValueError, match="one safe path component"):
        cat.build_catalog(root)


@pytest.mark.parametrize("retired_key", ["repos", "local_skills"])
def test_registry_rejects_retired_keys_even_with_new_sections(tmp_path, retired_key):
    root = catalog_with_registry(tmp_path, {"upstream": {}, "local": [], retired_key: {}})

    with pytest.raises(ValueError, match="retired"):
        cat.build_catalog(root)


def test_registry_rejects_an_unsafe_name_derived_from_upstream_path(tmp_path):
    root = catalog_with_registry(
        tmp_path,
        {
            "upstream": {
                "owner/repo": {
                    "skills": [{"upstream_path": "skills/.."}]
                }
            }
        },
    )

    with pytest.raises(ValueError, match="one safe path component"):
        cat.build_catalog(root)


def test_metadata_only_source_is_checked_for_catalog_containment(tmp_path):
    root = catalog_with_registry(
        tmp_path,
        {"local": [{"name": "review"}]},
    )
    source = root / "skills" / "review"
    source.symlink_to(tmp_path / "outside" / "review", target_is_directory=True)

    artifact = cat.get(cat.build_catalog(root), cat.SKILL, "review")

    assert artifact.source == source
    assert "resolves outside" in artifact.catalog_error


def test_catalog_path_is_fixed_under_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    catalog = config.catalog_path(home)
    catalog.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("KURA_CATALOG", str(tmp_path / "override"))

    assert config.effective_catalog() == home / ".config" / "kura" / "catalog"


def test_missing_metadata_sources_do_not_hide_deeper_dependency_drift(tmp_path):
    root = catalog_with_registry(
        tmp_path,
        {
            "local": [
                {"name": "root", "dependencies": ["middle"]},
                {"name": "middle", "dependencies": ["leaf"]},
                {"name": "leaf", "dependencies": ["root"]},
            ]
        },
    )
    write_skill(root, "leaf")

    resolution = cat.resolve(cat.build_catalog(root), ["root"])

    assert resolution.names == ("leaf",)
    assert resolution.missing_direct == ("root",)
    assert ("root", "middle") in resolution.missing_dependencies
    assert not resolution.complete


def test_catalog_name_reader_accepts_a_quoted_yaml_key(tmp_path):
    root = catalog_with_registry(
        tmp_path,
        {"local": [{"name": "review"}]},
    )
    write_skill(root, "review", '"name": review')

    artifact = cat.get(cat.build_catalog(root), cat.SKILL, "review")

    assert artifact.catalog_error is None


def manifest_data():
    return {
        "schemaVersion": 1,
        "harnesses": ["claude"],
        "skills": [],
    }


def test_manifest_defaults_legacy_only_when_absent():
    absent = state.parse(manifest_data())
    present = manifest_data()
    present["legacy"] = {}

    assert absent.legacy == {}
    assert state.parse(present).legacy == {}


@pytest.mark.parametrize(
    "value",
    [None, False, 0, "", []],
    ids=["null", "false", "zero", "empty-string", "list"],
)
def test_manifest_rejects_present_falsy_non_object_legacy(value):
    data = manifest_data()
    data["legacy"] = value

    with pytest.raises(state.Malformed, match="legacy must be an object"):
        state.parse(data)


@pytest.mark.parametrize(
    "root_skills,legacy_skills,only_root,only_legacy",
    [
        (("review",), (), "review", "none"),
        ((), ("review",), "none", "review"),
    ],
)
def test_dual_manifest_direct_comparison_is_symmetric(
    root_skills, legacy_skills, only_root, only_legacy
):
    root = state.Manifest(("claude",), root_skills)
    legacy = state.Manifest(("claude",), legacy_skills)

    with pytest.raises(state.Malformed) as excinfo:
        state.merge(root, legacy)

    detail = str(excinfo.value)
    assert f"only in root: {only_root}" in detail
    assert f"only in legacy: {only_legacy}" in detail
