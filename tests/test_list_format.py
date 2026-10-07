import pytest

from kura import scope, views
from kura.commands import listing


def _row(view_map, *, row_state=listing.LINKED, installed=scope.PROJECT, is_global=False):
    return {
        "name": "backend",
        "state": row_state,
        "installed": installed,
        "global": is_global,
        "groups": (),
        "dependencies": (),
        "parent": None,
        "views": view_map,
    }


@pytest.mark.parametrize("member", ("", "skill helper", "agent architect"))
def test_linked_rows_show_only_harness_ids(member, plain):
    view_map = {
        f"{harness_id} {member}".strip(): {"state": views.CURRENT, "target": "catalog-source"}
        for harness_id in ("claude", "pi")
    }

    assert listing.format_row(_row(view_map)) == "  ✓ backend (linked: claude, pi)"


def test_linked_bundle_lists_each_harness_once(plain):
    view_map = {
        f"{harness_id} {member}": {"state": views.CURRENT, "target": "catalog-source"}
        for member in ("skill helper", "skill reviewer", "agent architect")
        for harness_id in ("pi", "claude")
    }

    assert listing.format_row(_row(view_map)) == "  ✓ backend (linked: claude, pi)"


def test_global_install_is_marked_without_global_policy(plain):
    row = _row({"claude": {"state": views.CURRENT}}, installed=scope.GLOBAL)

    assert listing.format_row(row) == "  ✓ backend (linked: claude) (global)"


def test_global_policy_does_not_mark_a_project_install_as_global(plain):
    row = _row({"claude": {"state": views.CURRENT}}, is_global=True)

    assert listing.format_row(row) == "  ✓ backend (linked: claude)"


@pytest.mark.parametrize("view_state", (views.MISSING, views.STALE, views.FOREIGN, views.REAL))
def test_drift_keeps_view_states_without_paths(view_state, plain):
    row = _row(
        {
            "claude": {"state": views.CURRENT, "target": "catalog-source"},
            "pi": {"state": view_state, "target": "other-source"},
        },
        row_state=listing.DRIFT,
    )

    assert listing.format_row(row) == f"  ! backend (drift: claude linked; pi {view_state})"


def test_bundle_drift_identifies_member_views(plain):
    row = _row(
        {
            "claude skill helper": {"state": views.CURRENT, "target": "catalog-source"},
            "pi agent architect": {"state": views.MISSING},
        },
        row_state=listing.DRIFT,
        installed=None,
    )

    assert listing.format_row(row) == "  ! backend (drift: claude skill helper linked; pi agent architect missing)"


@pytest.mark.parametrize(
    ("row_state", "expected"),
    ((listing.AVAILABLE, "  · backend"), (listing.MISSING, "  ↓ backend (missing)")),
)
def test_uninstalled_rows_have_no_link_detail(row_state, expected, plain):
    assert listing.format_row(_row({}, row_state=row_state, installed=None)) == expected
