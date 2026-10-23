"""Original registry checks: canonical versions work before the regression fix."""
import copy

import pytest
from packaging.version import InvalidVersion

from app.plugin_binding import select_plugin_release


def test_numeric_order_and_exclusive_upper_bound():
    records = [
        {"id": "nine", "version": "1.9.0"},
        {"id": "ten", "version": "1.10.0"},
        {"id": "next", "version": "2.0.0"},
    ]
    assert select_plugin_release(records, "1.0", "2.0") == {
        "id": "ten", "version": "1.10.0", "source_version": "1.10.0",
    }


def test_prerelease_policy_and_input_integrity():
    records = [
        {"id": "final", "version": "1.10.0"},
        {"id": "rc", "version": "1.11.0rc2"},
        {"id": "withdrawn", "version": "not-a-version", "yanked": True},
    ]
    original = copy.deepcopy(records)
    assert select_plugin_release(records, "1.0", "2.0")["id"] == "final"
    assert select_plugin_release(records, "1.0", "2.0", allow_prereleases=True)["id"] == "rc"
    assert records == original


def test_equal_versions_keep_first_registry_entry():
    assert select_plugin_release([
        {"id": "first", "version": "1.10"},
        {"id": "second", "version": "1.10.0"},
    ], "1.0", "2.0")["id"] == "first"


def test_empty_selection_and_invalid_bounds():
    assert select_plugin_release([], "1.0", "2.0") is None
    assert select_plugin_release([{"id": "old", "version": "0.9"}], "1.0", "2.0") is None
    with pytest.raises(ValueError, match="minimum"):
        select_plugin_release([], "2.0", "2.0")


def test_unknown_label_is_not_silently_accepted():
    with pytest.raises(InvalidVersion):
        select_plugin_release([{"id": "unknown", "version": "banana"}], "1.0", "2.0")
