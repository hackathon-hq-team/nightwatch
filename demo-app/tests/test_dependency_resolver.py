"""Original checks for dependency graphs with no rejected candidate alternatives."""
import copy

import pytest

from app.dependency_resolver import DependencyConflict, resolve_dependencies


def test_numeric_versions_and_deterministic_output_order():
    registry = {
        "zlib": [{"version": "1.9"}, {"version": "1.10"}],
        "app": [{"version": "1.0", "requires": {"zlib": ">=1"}}],
    }
    original = copy.deepcopy(registry)
    result = resolve_dependencies(registry, {"app": ">=1"})
    assert result == {"app": "1.0", "zlib": "1.10"}
    assert list(result) == ["app", "zlib"]
    assert registry == original


def test_compatible_dependency_cycle_terminates():
    assert resolve_dependencies({
        "a": [{"version": "1", "requires": {"b": ">=1"}}],
        "b": [{"version": "1", "requires": {"a": "<2"}}],
    }, {"a": ">=1"}) == {"a": "1", "b": "1"}


def test_yanked_and_prerelease_policy():
    registry = {"a": [{"version": "3", "yanked": True}, {"version": "2rc1"}, {"version": "1"}]}
    assert resolve_dependencies(registry, {"a": ">=1"}) == {"a": "1"}
    assert resolve_dependencies(registry, {"a": ">=1"}, allow_prereleases=True) == {"a": "2rc1"}


def test_impossible_requirement_and_missing_package_raise():
    with pytest.raises(DependencyConflict, match="No compatible"):
        resolve_dependencies({"a": [{"version": "1"}]}, {"a": ">=2"})
    with pytest.raises(DependencyConflict, match="No compatible"):
        resolve_dependencies({}, {"missing": ">=1"})


def test_empty_roots_do_not_resolve_unrequested_packages():
    assert resolve_dependencies({"unused": [{"version": "1"}]}, {}) == {}
