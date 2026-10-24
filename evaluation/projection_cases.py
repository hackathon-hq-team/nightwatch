"""Independent expected snapshots and repairs for the versioned projection case."""
from textwrap import dedent


EVENTS = [
    {"tenant": "a", "entity": "x", "event_id": "3", "version": 3, "op": "delete", "value": 0},
    {"tenant": "b", "entity": "x", "event_id": "1", "version": 1, "op": "create", "value": 9},
    {"tenant": "a", "entity": "x", "event_id": "5", "version": 5, "op": "adjust", "value": 2},
    {"tenant": "a", "entity": "x", "event_id": "1", "version": 1, "op": "create", "value": 10},
    {"tenant": "a", "entity": "x", "event_id": "4", "version": 4, "op": "create", "value": 5},
    {"tenant": "a", "entity": "x", "event_id": "2", "version": 2, "op": "adjust", "value": -3},
    {"tenant": "a", "entity": "x", "event_id": "2", "version": 2, "op": "adjust", "value": -3},
]

ORDER_FIX = ["    ordered = unique", '    ordered = sorted(unique, key=lambda row: (row["tenant"], row["entity"], row["version"]))']
TOMBSTONE_FIX = ["            versions.pop(key, None)\n", ""]

CASES = [{
    "slug": "versioned-projection",
    "title": "Unordered entity retries lose version continuity across delete and recreate",
    "app_file": "app/event_projection.py",
    "exception_type": "ProjectionConflict",
    "baseline_count": 13,
    "acceptance_count": 6,
    "request": {"events": EVENTS},
    "call": f"from app.event_projection import project_entities\nproject_entities({EVENTS!r})\n",
    "good_replacements": [ORDER_FIX, TOMBSTONE_FIX],
    "reproducer": (
        "from app.event_projection import project_entities\n\n"
        "def test_out_of_order_delete_recreate_with_retries():\n"
        f"    events = {EVENTS!r}\n"
        "    assert project_entities(events) == [\n"
        "        {'tenant': 'a', 'entity': 'x', 'version': 5, 'value': 7},\n"
        "        {'tenant': 'b', 'entity': 'x', 'version': 1, 'value': 9},\n"
        "    ]\n"
    ),
    "acceptance": dedent('''\
        import copy
        from itertools import permutations
        import pytest
        from app.event_projection import ProjectionConflict, project_entities

        def event(version, op, value=0, tenant="a", entity="x", event_id=None):
            return {"tenant": tenant, "entity": entity, "version": version, "op": op,
                    "value": value, "event_id": event_id or str(version)}

        def test_recreate_continues_version_without_reusing_old_value():
            events = [event(1, "create", 100), event(2, "delete"),
                      event(3, "create", 6), event(4, "adjust", -2)]
            assert project_entities(events) == [
                {"tenant": "a", "entity": "x", "version": 4, "value": 4}]

        def test_every_arrival_permutation_and_delayed_retry_preserves_snapshot():
            events = [event(1, "create", 8), event(2, "adjust", -3),
                      event(3, "delete"), event(4, "create", 2)]
            expected = [{"tenant": "a", "entity": "x", "version": 4, "value": 2}]
            for ordered in permutations(events):
                replay = [*ordered, dict(events[0]), dict(events[2]), dict(events[1])]
                before = copy.deepcopy(replay)
                assert project_entities(replay) == expected
                assert replay == before

        def test_tenant_entity_tombstones_and_retry_ids_are_independent():
            events = [event(3, "create", 3), event(2, "delete"),
                      event(1, "create", 9, tenant="b"), event(1, "create", 7),
                      event(1, "create", 0, entity="y", event_id="y1")]
            assert project_entities(events) == [
                {"tenant": "a", "entity": "x", "version": 3, "value": 3},
                {"tenant": "a", "entity": "y", "version": 1, "value": 0},
                {"tenant": "b", "entity": "x", "version": 1, "value": 9},
            ]

        def test_gaps_and_duplicate_versions_are_rejected_after_deletion():
            prefix = [event(1, "create", 8), event(2, "delete")]
            for suffix in ([event(4, "create", 5)],
                           [event(3, "adjust", 1)],
                           [event(3, "create", 2), event(3, "create", 2, event_id="other")]):
                with pytest.raises(ProjectionConflict):
                    project_entities(list(reversed(prefix + suffix)))

        def test_conflicting_delayed_retry_is_not_ignored_after_recreate():
            events = [event(1, "create", 8), event(2, "delete"), event(3, "create", 2)]
            for replacement in ({"value": 9}, {"entity": "other"}, {"op": "adjust"}):
                with pytest.raises(ProjectionConflict, match="retry"):
                    project_entities(events + [{**events[0], **replacement}])

        def test_bounded_adjustment_histories_have_recorded_expected_totals():
            # The expected totals below are literal oracles, not a second projection algorithm.
            for deltas, expected_value in [((-2, -1), 2), ((0, 0), 5), ((3, -2), 6), ((-5, 4), 4)]:
                events = [event(1, "create", 9), event(2, "delete"), event(3, "create", 5),
                          event(4, "adjust", deltas[0]), event(5, "adjust", deltas[1])]
                expected = [{"tenant": "a", "entity": "x", "version": 5, "value": expected_value}]
                assert project_entities(list(reversed(events))) == expected
                assert project_entities(events + [dict(events[-1])]) == expected
    '''),
    "bad_replacements": [
        {"name": "sort-events-but-forget-tombstone-version", "replacements": [ORDER_FIX]},
        {"name": "retain-tombstones-but-process-arrival-order", "replacements": [TOMBSTONE_FIX]},
        {"name": "skip-version-validation-on-create", "replacements": [
            ORDER_FIX, ["        if version != expected:", '        if version != expected and event["op"] != "create":'],
        ]},
        {"name": "ignore-conflicting-delayed-retries", "replacements": [
            ORDER_FIX, TOMBSTONE_FIX,
            ['                raise ProjectionConflict("Conflicting retry identity")', "                continue"],
        ]},
    ],
}]
