"""Trusted packaging migration fixture; never send this module to the runtime model.

packaging 22.0 removed LegacyVersion and parse() no longer accepts arbitrary
legacy labels. This case uses the actual installed packaging package, already
required by pytest, and captures its actual InvalidVersion traceback. No old
package is downloaded and no dependency upgrade is claimed to have been run.

Upstream references:
https://packaging.pypa.io/en/stable/changelog.html
https://packaging.pypa.io/en/stable/version.html
"""
from __future__ import annotations

import textwrap


BROKEN_PARSER = '''    # Parse one registry label before applying release-selection policy.
    return parse(label)
'''

GOOD_PARSER = r'''    # Normalize only documented registry aliases; preserve all other PEP 440 semantics.
    import re

    alias = re.fullmatch(
        r"(v?\d+(?:\.\d+)*)(?:-(stable|final)|-hotfix\.(\d+))"
        r"(\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?",
        label.strip(),
        re.IGNORECASE,
    )
    if alias:
        label = alias.group(1)
        if alias.group(3) is not None:
            label += ".post" + alias.group(3)
        label += alias.group(4) or ""
    return parse(label)
'''

RECORDS = [
    {"id": "withdrawn", "version": "corrupt", "yanked": True},
    {"id": "older", "version": "1.9.9"},
    {"id": "stable", "version": "1.10.0-stable"},
    {"id": "hotfix", "version": "1.10.0-hotfix.2+GPU.3"},
    {"id": "preview", "version": "1.11.0rc1"},
    {"id": "next-major", "version": "2.0.0-final"},
]

REPRODUCER = textwrap.dedent('''\
    from app.plugin_binding import select_plugin_release

    def test_legacy_registry_selects_compatible_hotfix():
        records = [
            {"id": "withdrawn", "version": "corrupt", "yanked": True},
            {"id": "older", "version": "1.9.9"},
            {"id": "stable", "version": "1.10.0-stable"},
            {"id": "hotfix", "version": "1.10.0-hotfix.2+GPU.3"},
            {"id": "preview", "version": "1.11.0rc1"},
            {"id": "next-major", "version": "2.0.0-final"},
        ]
        assert select_plugin_release(records, "1.0", "2.0") == {
            "id": "hotfix",
            "version": "1.10.0.post2+gpu.3",
            "source_version": "1.10.0-hotfix.2+GPU.3",
        }
''')

ACCEPTANCE = textwrap.dedent('''\
    import copy
    import pytest
    from packaging.version import InvalidVersion
    from app.plugin_binding import select_plugin_release

    @pytest.mark.parametrize("label, expected", [
        ("1.10.0-stable", "1.10.0"),
        ("v1.10.0-FINAL+GPU.3", "1.10.0+gpu.3"),
        ("1.10.0-hotfix.12", "1.10.0.post12"),
        (" 1.10.0-hotfix.0+linux_x86 ", "1.10.0.post0+linux.x86"),
        ("1.10.0.post3", "1.10.0.post3"),
    ])
    def test_supported_labels_preserve_artifact_identity(label, expected):
        records = [{"id": "artifact", "version": label}]
        original = copy.deepcopy(records)
        assert select_plugin_release(records, "1.0", "2.0") == {
            "id": "artifact", "version": expected, "source_version": label,
        }
        assert records == original

    def test_numeric_hotfix_and_local_build_ordering():
        records = [
            {"id": "two", "version": "1.10.0-hotfix.2"},
            {"id": "ten", "version": "1.10.0-hotfix.10+gpu.2"},
            {"id": "ten-local", "version": "1.10.0-hotfix.10+gpu.10"},
            {"id": "nine", "version": "1.9.9-final"},
        ]
        assert select_plugin_release(records, "1.0", "2.0")["id"] == "ten-local"

    def test_prerelease_and_dev_policy_survives_migration():
        records = [
            {"id": "final", "version": "1.10.0-final"},
            {"id": "rc", "version": "1.11.0-rc.2"},
            {"id": "dev", "version": "1.12.0.dev1"},
        ]
        assert select_plugin_release(records, "1.0", "2.0")["id"] == "final"
        assert select_plugin_release(records, "1.0", "2.0", allow_prereleases=True)["id"] == "dev"

    def test_inclusive_lower_exclusive_upper_and_equal_aliases():
        records = [
            {"id": "first", "version": "1.10.0-final"},
            {"id": "same", "version": "1.10-stable"},
            {"id": "upper", "version": "2.0.0-stable"},
        ]
        assert select_plugin_release(records, "1.10", "2.0")["id"] == "first"

    @pytest.mark.parametrize("label", ["1.10.0-garbage", "1.10.0-hotfix.x", "1.10.0-stable+", "1.10.0-stable-extra"])
    def test_unknown_labels_still_fail_closed(label):
        with pytest.raises(InvalidVersion):
            select_plugin_release([{"id": "invalid", "version": label}], "1.0", "2.0")

    def test_withdrawn_legacy_entry_does_not_hide_valid_candidate():
        assert select_plugin_release([
            {"id": "removed", "version": "bad-label", "yanked": True},
            {"id": "eligible", "version": "1.10.0-stable"},
        ], "1.0", "2.0")["id"] == "eligible"

    def test_no_compatible_candidate_remains_none():
        assert select_plugin_release([
            {"id": "old", "version": "0.9.0-final"},
            {"id": "new", "version": "2.0.0-hotfix.1"},
        ], "1.0", "2.0") is None
''')

CASES = [{
    "slug": "packaging-legacy-release",
    "title": "Plugin release selection rejects registry version labels after an environment refresh",
    "app_file": "app/plugin_binding.py",
    "exception_type": "InvalidVersion",
    "baseline_count": 5,
    "acceptance_count": 14,
    "call": "from app.plugin_binding import select_plugin_release\n"
            f"select_plugin_release({RECORDS!r}, '1.0', '2.0')\n",
    "request": {"releases": RECORDS, "minimum": "1.0", "maximum": "2.0", "allow_prereleases": False},
    "good_replacements": [(BROKEN_PARSER, GOOD_PARSER)],
    "reproducer": REPRODUCER,
    "acceptance": ACCEPTANCE,
    "bad_replacements": [
        {
            "name": "discard-all-version-suffixes",
            "replacements": [(BROKEN_PARSER, '    return parse(label.split("-")[0])\n')],
        },
        {
            "name": "normalize-only-observed-stable-label",
            "replacements": [(BROKEN_PARSER, '    return parse(label.replace("-stable", ""))\n')],
        },
        {
            "name": "lexical-release-ranking",
            "replacements": [(BROKEN_PARSER, GOOD_PARSER), (
                "if chosen_version is None or version > chosen_version:",
                "if chosen_version is None or str(version) > str(chosen_version):",
            )],
        },
        {
            "name": "silently-enable-prereleases",
            "replacements": [(BROKEN_PARSER, GOOD_PARSER), (
                "if version.is_prerelease and not allow_prereleases:",
                "if False:",
            )],
        },
        {
            "name": "include-exclusive-upper-bound",
            "replacements": [(BROKEN_PARSER, GOOD_PARSER), (
                "if not lower <= version < upper:",
                "if not lower <= version <= upper:",
            )],
        },
        {
            "name": "overwrite-first-equivalent-artifact",
            "replacements": [(BROKEN_PARSER, GOOD_PARSER), (
                "if chosen_version is None or version > chosen_version:",
                "if chosen_version is None or version >= chosen_version:",
            )],
        },
        {
            "name": "compare-hotfix-and-build-lexically",
            "replacements": [(BROKEN_PARSER, GOOD_PARSER), (
                "if chosen_version is None or version > chosen_version:",
                "if chosen_version is None or (version != chosen_version and "
                "(version.release, str(version.post or 0), version.local or '') "
                "> (chosen_version.release, str(chosen_version.post or 0), chosen_version.local or '')):",
            )],
        },
        {
            "name": "accept-unrecognized-suffixes-after-alias-normalization",
            "replacements": [(BROKEN_PARSER, GOOD_PARSER.replace(
                "    return parse(label)\n",
                '    return parse(re.sub(r"-[A-Za-z].*$", "", label))\n',
            ))],
        },
    ],
}]
