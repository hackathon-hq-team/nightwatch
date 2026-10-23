import pytest

from app.reservations import CapacityExceeded, reservation_peaks


def booking(identifier, start, end, units, resource="gpu", status="confirmed"):
    return {"id": identifier, "resource": resource, "start": start, "end": end,
            "units": units, "status": status}


def test_overlapping_reservations_and_resource_isolation():
    rows = [
        booking("a", "2026-01-01T10:00:00+00:00", "2026-01-01T12:00:00+00:00", 2),
        booking("b", "2026-01-01T11:00:00+00:00", "2026-01-01T13:00:00+00:00", 1),
        booking("c", "2026-01-01T10:30:00+00:00", "2026-01-01T14:00:00+00:00", 4, "cpu"),
    ]
    assert reservation_peaks(rows, {"gpu": 3, "cpu": 4, "idle": 0}) == {"gpu": 3, "cpu": 4, "idle": 0}


def test_real_overcapacity_is_rejected():
    rows = [booking("a", "2026-01-01T10:00:00+00:00", "2026-01-01T12:00:00+00:00", 4)]
    with pytest.raises(CapacityExceeded, match="gpu"):
        reservation_peaks(rows, {"gpu": 3})


def test_cancellation_and_explicit_offsets():
    rows = [
        booking("a", "2026-01-01T11:00:00+01:00", "2026-01-01T11:30:00+00:00", 2),
        booking("b", "2026-01-01T10:00:00+00:00", "2026-01-01T12:00:00+00:00", 99, status="cancelled"),
    ]
    assert reservation_peaks(rows, {"gpu": 2}) == {"gpu": 2}


def test_invalid_times_units_and_duplicate_ids():
    row = booking("a", "2026-01-01T10:00:00+00:00", "2026-01-01T12:00:00+00:00", 1)
    for changes in ({"units": 0}, {"units": True}, {"start": row["end"]}, {"start": "2026-01-01T10:00:00"}):
        with pytest.raises(ValueError):
            reservation_peaks([{**row, **changes}], {"gpu": 2})
    with pytest.raises(ValueError, match="unique"):
        reservation_peaks([row, row], {"gpu": 2})
