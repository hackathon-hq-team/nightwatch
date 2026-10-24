import copy

import pytest

from app.event_projection import ProjectionConflict, project_entities


def event(version, op, value=0, tenant="a", entity="item", event_id=None):
    return {"tenant": tenant, "entity": entity, "event_id": event_id or str(version),
            "version": version, "op": op, "value": value}


def test_ordered_adjustments_preserve_zero_entities_and_inputs():
    events = [event(1, "create", 8, entity="z", event_id="z1"),
              event(1, "create", 5), event(2, "adjust", -5)]
    before = copy.deepcopy(events)
    assert project_entities(events) == [
        {"tenant": "a", "entity": "item", "version": 2, "value": 0},
        {"tenant": "a", "entity": "z", "version": 1, "value": 8},
    ]
    assert events == before
    assert project_entities([]) == []


def test_terminal_delete_omits_entity_and_exact_retries_count_once():
    created = event(1, "create", 3)
    removed = event(2, "delete")
    assert project_entities([created, dict(created), removed, dict(removed)]) == []


def test_retry_identifiers_are_scoped_to_tenants():
    assert project_entities([event(1, "create", 4, tenant="b"), event(1, "create", 7)]) == [
        {"tenant": "a", "entity": "item", "version": 1, "value": 7},
        {"tenant": "b", "entity": "item", "version": 1, "value": 4},
    ]


@pytest.mark.parametrize("events", [
    [event(2, "create", 1)],
    [event(1, "create", 1), event(3, "adjust", 1)],
    [event(1, "create", 1), event(2, "create", 1)],
    [event(1, "adjust", 1)],
    [event(1, "delete")],
    [event(1, "create", 1), event(1, "create", 1, event_id="another")],
    [event(1, "create", 1), event(1, "create", 2)],
    [event(1, "create", 1), event(2, "adjust", -2)],
    [event(True, "create", 1)],
    [event(1, "create", True)],
])
def test_invalid_projection_history_is_rejected(events):
    with pytest.raises(ProjectionConflict):
        project_entities(events)
