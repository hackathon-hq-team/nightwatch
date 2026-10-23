"""Snapshot exports ordered by descending timestamp, then ascending event ID."""


class IncompleteExport(RuntimeError):
    """A page sequence omitted or repeated records from the requested snapshot."""


def event_page(events, tenant, snapshot, limit, cursor=None):
    """Return at most limit records and their exclusive (timestamp, id) cursor.

    Timestamps are integer sequence times; IDs are strings unique within a tenant.
    Tenant filtering and the inclusive snapshot boundary apply before pagination.
    A nonempty last page still returns its cursor; an empty page returns None.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("Page size must be a positive integer")
    visible = [row for row in events if row["tenant"] == tenant and row["at"] <= snapshot]
    if len({row["id"] for row in visible}) != len(visible):
        raise ValueError("Event IDs must be unique within a tenant snapshot")
    ordered = sorted(visible, key=lambda row: (-row["at"], row["id"]))
    # The cursor identifies the final row emitted by the preceding page.
    eligible = [row for row in ordered if cursor is None or (row["at"], row["id"]) < cursor]
    page = eligible[:limit]
    next_cursor = (page[-1]["at"], page[-1]["id"]) if page else None
    return page, next_cursor


def export_events(events, tenant, snapshot, page_size):
    """Return every visible event exactly once, in the documented page ordering."""
    expected = sum(row["tenant"] == tenant and row["at"] <= snapshot for row in events)
    cursor = None
    seen = set()
    exported = []
    while True:
        page, next_cursor = event_page(events, tenant, snapshot, page_size, cursor)
        if not page:
            break
        for row in page:
            if row["id"] in seen:
                raise IncompleteExport("Snapshot pagination repeated an event")
            seen.add(row["id"])
            exported.append(row["id"])
        cursor = next_cursor
    if len(exported) != expected:
        raise IncompleteExport(f"Snapshot pagination lost records: expected {expected}, exported {len(exported)}")
    return exported
