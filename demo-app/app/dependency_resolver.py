"""Resolve a small in-memory package registry without downloads or installation."""
from __future__ import annotations

from packaging.specifiers import SpecifierSet
from packaging.version import Version


class DependencyConflict(ValueError):
    """No complete assignment satisfies the requested dependency graph."""


def resolve_dependencies(registry: dict, requirements: dict, *, allow_prereleases: bool = False) -> dict:
    """Return a complete package-name to normalized-version mapping.

    Registry entries have a unique normalized PEP 440 version, an optional ``requires``
    name-to-specifier mapping, and an optional ``yanked`` flag. Inputs contain
    valid versions/specifiers. Every root and transitive constraint must hold;
    missing packages or an unsatisfiable graph raise DependencyConflict.

    Determinism: select the alphabetically first unresolved package, try its
    eligible versions in descending PEP 440 order, and take the first COMPLETE
    solution in that depth-first order. Registry/root insertion order is irrelevant.
    Yanked releases are never eligible. Pre/dev releases require explicit opt-in;
    specifiers retain their PEP 440 meaning. Compatible cycles are allowed.
    Return only roots and their selected dependencies, with keys sorted. Failed
    alternatives must not affect the final assignment. Do not mutate inputs.
    """
    constraints = {name: [specifier] for name, specifier in requirements.items()}

    def eligible(version, specs):
        return all(SpecifierSet(spec).contains(version, prereleases=True) for spec in specs)

    def search(selected, constraints):
        # Later dependencies can narrow a package that was selected earlier.
        if any(not eligible(Version(release["version"]), constraints[name])
               for name, release in selected.items()):
            return None
        pending = sorted(set(constraints) - set(selected))
        if not pending:
            return selected
        name = pending[0]
        candidates = sorted(
            (release for release in registry.get(name, [])
             if not release.get("yanked", False)
             and (allow_prereleases or not Version(release["version"]).is_prerelease)
             and eligible(Version(release["version"]), constraints[name])),
            key=lambda release: Version(release["version"]), reverse=True,
        )
        for release in candidates[:1]:
            branch = constraints.copy()
            for dependency, specifier in release.get("requires", {}).items():
                branch.setdefault(dependency, []).append(specifier)
            result = search({**selected, name: release}, branch)
            if result is not None:
                return result
        return None

    selected = search({}, constraints)
    if selected is None:
        raise DependencyConflict("No compatible dependency assignment")
    return {name: str(Version(selected[name]["version"])) for name in sorted(selected)}
