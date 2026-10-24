"""Trusted dependency-resolution answers and mutations, excluded from model context."""
from __future__ import annotations

import textwrap


REGISTRY = {
    "app": [
        {"version": "2", "requires": {"core": ">=3", "gate": ">=1"}},
        {"version": "1", "requires": {"core": "<3"}},
    ],
    "core": [{"version": "3"}, {"version": "2"}, {"version": "1"}],
    "gate": [{"version": "1", "requires": {"core": "<3"}}],
}
REQUIREMENTS = {"app": ">=1", "core": ">=1"}
EXPECTED = {"app": "1", "core": "2"}

EXPLORE = ("for release in candidates[:1]:", "for release in candidates:")
ISOLATE = (
    "branch = constraints.copy()",
    "branch = {package: list(specs) for package, specs in constraints.items()}",
)
GOOD = [EXPLORE, ISOLATE]

REPRODUCER = (
    "from app.dependency_resolver import resolve_dependencies\n\n"
    "def test_conflicting_newest_release_falls_back_without_leaking_constraints():\n"
    f"    registry = {REGISTRY!r}\n"
    f"    assert resolve_dependencies(registry, {REQUIREMENTS!r}) == {EXPECTED!r}\n"
)

# The expected maps are literal answers, not computed by the reference repair.
# Compatible solutions were independently enumerated across the finite registries.
ACCEPTANCE = textwrap.dedent('''\
    import copy
    import pytest
    from app.dependency_resolver import DependencyConflict, resolve_dependencies

    @pytest.mark.parametrize("registry, roots, allow, expected", [
        ({
            "a": [{"version": "2"}, {"version": "1"}],
            "z": [{"version": "1", "requires": {"a": "<2"}}],
        }, {"a": ">=1", "z": ">=1"}, False, {"a": "1", "z": "1"}),
        ({
            "app": [{"version": "1", "requires": {"core": ">=1", "gate": ">=1"}}],
            "core": [{"version": "3"}, {"version": "2"}, {"version": "1"}],
            "gate": [{"version": "1", "requires": {"core": "<3"}}],
        }, {"app": ">=1"}, False, {"app": "1", "core": "2", "gate": "1"}),
        ({
            "app": [{"version": "1", "requires": {"left": ">=1", "right": ">=1"}}],
            "left": [{"version": "2", "requires": {"core": ">=2,<3"}},
                     {"version": "1", "requires": {"core": ">=1,<3"}}],
            "right": [{"version": "2", "requires": {"core": ">=3"}},
                      {"version": "1", "requires": {"core": "<3"}}],
            "core": [{"version": "3"}, {"version": "2"}, {"version": "1"}],
        }, {"app": ">=1"}, False, {"app": "1", "core": "2", "left": "2", "right": "1"}),
        ({
            "a": [{"version": "2", "requires": {"b": ">=2"}},
                  {"version": "1", "requires": {"b": ">=1"}}],
            "b": [{"version": "2", "requires": {"a": "<2"}},
                  {"version": "1", "requires": {"a": "<2"}}],
        }, {"a": ">=1"}, False, {"a": "1", "b": "2"}),
        ({
            "app": [{"version": "2", "requires": {"missing": ">=1"}},
                    {"version": "1", "requires": {"lib": ">=1"}}],
            "lib": [{"version": "2", "yanked": True}, {"version": "1"}],
        }, {"app": ">=1"}, False, {"app": "1", "lib": "1"}),
        ({
            "a": [{"version": "2", "requires": {"core": ">=2rc1"}},
                  {"version": "1", "requires": {"core": ">=1"}}],
            "core": [{"version": "2rc1"}, {"version": "1"}],
        }, {"a": ">=1"}, False, {"a": "1", "core": "1"}),
        ({
            "a": [{"version": "2", "requires": {"core": ">=2rc1"}},
                  {"version": "1", "requires": {"core": ">=1"}}],
            "core": [{"version": "2rc1"}, {"version": "1"}],
        }, {"a": ">=1"}, True, {"a": "2", "core": "2rc1"}),
        ({
            "z": [{"version": "2", "requires": {"core": "<2"}},
                  {"version": "1", "requires": {"core": ">=2"}}],
            "core": [{"version": "1"}, {"version": "2"}],
            "a": [{"version": "1", "requires": {"core": "<2"}},
                  {"version": "2", "requires": {"core": ">=2"}}],
        }, {"z": ">=1", "a": ">=1"}, False, {"a": "2", "core": "2", "z": "1"}),
    ])
    def test_complete_solution_and_deterministic_search_order(registry, roots, allow, expected):
        original_registry, original_roots = copy.deepcopy(registry), dict(roots)
        result = resolve_dependencies(registry, roots, allow_prereleases=allow)
        assert result == expected
        assert list(result) == sorted(expected)
        # Input permutation must not change candidate preference or search order.
        reversed_registry = {name: list(reversed(versions)) for name, versions in reversed(list(registry.items()))}
        assert resolve_dependencies(reversed_registry, dict(reversed(list(roots.items()))),
                                    allow_prereleases=allow) == expected
        assert registry == original_registry and roots == original_roots

    @pytest.mark.parametrize("registry, roots", [
        ({
            "a": [{"version": "1", "requires": {"core": ">=2"}}],
            "b": [{"version": "1", "requires": {"core": "<2"}}],
            "core": [{"version": "2"}, {"version": "1"}],
        }, {"a": ">=1", "b": ">=1"}),
        ({
            "a": [{"version": "1", "requires": {"b": ">=1"}}],
            "b": [{"version": "1", "requires": {"a": ">=2"}}],
        }, {"a": ">=1"}),
        ({"a": [{"version": "2", "requires": {"missing": ">=1"}},
                {"version": "1", "requires": {"also-missing": ">=1"}}]}, {"a": ">=1"}),
    ])
    def test_conflicting_graphs_do_not_return_partial_assignments(registry, roots):
        original = copy.deepcopy(registry)
        with pytest.raises(DependencyConflict, match="No compatible"):
            resolve_dependencies(registry, roots)
        assert registry == original
''')

CASES = [{
    "slug": "dependency-backtracking",
    "title": "A compatible dependency graph is rejected after alternatives introduce shared constraints",
    "app_file": "app/dependency_resolver.py",
    "exception_type": "DependencyConflict",
    "baseline_count": 5,
    "acceptance_count": 11,
    "call": "from app.dependency_resolver import resolve_dependencies\n"
            f"resolve_dependencies({REGISTRY!r}, {REQUIREMENTS!r})\n",
    "request": {"registry": REGISTRY, "requirements": REQUIREMENTS, "allow_prereleases": False},
    "good_replacements": GOOD,
    "reproducer": REPRODUCER,
    "acceptance": ACCEPTANCE,
    "bad_replacements": [
        {"name": "isolate-constraints-but-keep-greedy-choice", "replacements": [ISOLATE]},
        {"name": "retry-versions-but-leak-branch-constraints", "replacements": [EXPLORE]},
        {"name": "union-instead-of-intersect-shared-constraints", "replacements": GOOD + [(
            "return all(SpecifierSet(spec).contains(version, prereleases=True) for spec in specs)",
            "return any(SpecifierSet(spec).contains(version, prereleases=True) for spec in specs)",
        )]},
        {"name": "never-revalidate-selected-dependencies", "replacements": GOOD + [(
            "if any(not eligible(Version(release[\"version\"]), constraints[name])",
            "if False and any(not eligible(Version(release[\"version\"]), constraints[name])",
        )]},
        {"name": "backtrack-root-versions-only", "replacements": GOOD + [(
            "for release in candidates:",
            "for release in (candidates if name in requirements else candidates[:1]):",
        )]},
        {"name": "drop-transitive-version-constraints", "replacements": GOOD + [(
            "branch.setdefault(dependency, []).append(specifier)",
            "branch.setdefault(dependency, [])",
        )]},
        {"name": "accept-yanked-alternatives", "replacements": GOOD + [(
            "if not release.get(\"yanked\", False)", "if True",
        )]},
        {"name": "choose-oldest-complete-solution", "replacements": GOOD + [(
            "key=lambda release: Version(release[\"version\"]), reverse=True,",
            "key=lambda release: Version(release[\"version\"]), reverse=False,",
        )]},
    ],
}]
