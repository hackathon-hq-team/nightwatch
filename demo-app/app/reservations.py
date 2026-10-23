"""Capacity accounting for confirmed half-open reservations in explicit timezones."""

from datetime import datetime, timezone


class CapacityExceeded(RuntimeError):
    """Confirmed reservations exceed the configured capacity of one resource."""


def _instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Reservation timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def reservation_peaks(reservations, capacities):
    """Return peak units for each resource, rejecting any capacity violation.

    Reservations occupy [start, end): an end and a start at the same instant do
    not overlap. Cancelled records consume no capacity. Offsets describe instants,
    so equivalent timestamps must share a boundary even when their text differs.
    """
    for capacity in capacities.values():
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 0:
            raise ValueError("Capacities must be non-negative integers")
    events = {resource: [] for resource in capacities}
    seen = set()
    for row in reservations:
        if row["id"] in seen:
            raise ValueError("Reservation IDs must be unique")
        seen.add(row["id"])
        if row["status"] == "cancelled":
            continue
        if row["status"] != "confirmed" or row["resource"] not in capacities:
            raise ValueError("Unknown reservation status or resource")
        units = row["units"]
        if not isinstance(units, int) or isinstance(units, bool) or units <= 0:
            raise ValueError("Reservation units must be positive integers")
        start, end = _instant(row["start"]), _instant(row["end"])
        if start >= end:
            raise ValueError("Reservation end must follow its start")
        events[row["resource"]].extend(((start, units), (end, -units)))
    peaks = {}
    for resource, changes in events.items():
        used = peak = 0
        # Sweep endpoint changes to derive demand independently for each resource.
        for instant, change in sorted(changes, key=lambda item: (item[0], -item[1])):
            used += change
            peak = max(peak, used)
            if used > capacities[resource]:
                raise CapacityExceeded(f"Resource {resource} exceeds capacity at {instant.isoformat()}")
        peaks[resource] = peak
    return peaks
