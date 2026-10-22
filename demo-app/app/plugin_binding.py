"""Select a compatible plugin release while preserving PEP 440 ordering.

The registry accepts canonical PEP 440 versions and these historical labels:
``<release>-stable`` and ``<release>-final`` mean a final release;
``<release>-hotfix.<number>`` means a post-release. A local ``+build`` suffix
is retained. Other malformed versions must raise ``InvalidVersion``.

"""
from __future__ import annotations

from packaging.version import Version, parse


def _parse_release(label: str) -> Version:
    # Parse one registry label before applying release-selection policy.
    return parse(label)


def select_plugin_release(
    releases: list[dict],
    minimum: str,
    maximum: str,
    *,
    allow_prereleases: bool = False,
) -> dict | None:
    """Choose the highest non-yanked version in [minimum, maximum).

    Pre/dev releases require explicit opt-in. Equal normalized versions keep
    the first registry entry. Return both the canonical version and original
    label; callers rely on the original label when retrieving the artifact.
    Skip yanked entries before parsing their labels. Never mutate the input.
    """
    lower, upper = parse(minimum), parse(maximum)
    if lower >= upper:
        raise ValueError("minimum must be lower than maximum")
    chosen = None
    chosen_version = None
    for release in releases:
        if release.get("yanked", False):
            continue
        version = _parse_release(release["version"])
        if version.is_prerelease and not allow_prereleases:
            continue
        if not lower <= version < upper:
            continue
        if chosen_version is None or version > chosen_version:
            chosen_version = version
            chosen = {
                "id": release["id"],
                "version": str(version),
                "source_version": release["version"],
            }
    return chosen
