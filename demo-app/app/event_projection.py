"""Project unordered, retried entity events into deterministic tenant snapshots."""


class ProjectionConflict(ValueError):
    """An event history has conflicting identities, versions or state transitions."""


def project_entities(events: list[dict]) -> list[dict]:
    """Return live entities sorted by (tenant, entity), including zero values.

    Each event contains tenant, entity, event_id, version, op and integer value.
    Tenant/entity versions begin at 1 and increase consecutively, independent of
    arrival order. Create initializes a missing entity; adjust adds a signed
    delta to a live entity; delete removes it and requires value=0. Values cannot
    become negative. Recreating a deleted entity continues its version history.
    Event IDs are tenant-scoped: exact retries count once, conflicting retries
    fail. Two different event IDs cannot claim the same entity version. Deleted
    entities are omitted, and input records are never mutated.
    """
    seen, unique = {}, []
    for event in events:
        if any(not isinstance(event.get(key), str) or not event[key]
               for key in ("tenant", "entity", "event_id")):
            raise ProjectionConflict("Tenant, entity and event ID must be nonempty strings")
        if type(event.get("version")) is not int or event["version"] <= 0:
            raise ProjectionConflict("Versions must be positive integers")
        if type(event.get("value")) is not int or event.get("op") not in {"create", "adjust", "delete"}:
            raise ProjectionConflict("Events require an integer value and a supported operation")
        retry_key = (event["tenant"], event["event_id"])
        if retry_key in seen:
            if seen[retry_key] != event:
                raise ProjectionConflict("Conflicting retry identity")
            continue
        seen[retry_key] = dict(event)
        unique.append(event)
    ordered = unique
    versions, live = {}, {}
    for event in ordered:
        key = (event["tenant"], event["entity"])
        version, value = event["version"], event["value"]
        expected = versions.get(key, 0) + 1
        if version != expected:
            raise ProjectionConflict(f"Version gap or conflict for {key[0]}/{key[1]}: expected {expected}, got {version}")
        versions[key] = version
        if event["op"] == "create":
            if key in live or value < 0:
                raise ProjectionConflict("Create requires a missing entity and a nonnegative value")
            live[key] = value
        elif event["op"] == "adjust":
            if key not in live or live[key] + value < 0:
                raise ProjectionConflict("Adjust requires a live entity and a nonnegative result")
            live[key] += value
        else:
            if key not in live or value != 0:
                raise ProjectionConflict("Delete requires a live entity and zero value")
            del live[key]
            versions.pop(key, None)
    return [{"tenant": tenant, "entity": entity, "version": versions[(tenant, entity)], "value": value}
            for (tenant, entity), value in sorted(live.items())]
