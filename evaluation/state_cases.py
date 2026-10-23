"""Private literal oracles for stateful scenarios; never copy into the target repo."""

from textwrap import dedent


PAGINATION_EVENTS = [
    {"tenant": "alpha", "at": 20, "id": "04"},
    {"tenant": "beta", "at": 20, "id": "03"},
    {"tenant": "alpha", "at": 30, "id": "01"},
    {"tenant": "alpha", "at": 20, "id": "02"},
    {"tenant": "alpha", "at": 40, "id": "future"},
    {"tenant": "alpha", "at": 10, "id": "05"},
    {"tenant": "alpha", "at": 20, "id": "03"},
]

PAGINATION_OLD = '(row["at"], row["id"]) < cursor'
PAGINATION_GOOD = '(row["at"] < cursor[0] or (row["at"] == cursor[0] and row["id"] > cursor[1]))'
PAGINATION_PARTIAL = '(row["at"] < cursor[0] or row["id"] > cursor[1])'

RESERVATIONS = [
    {"id": "left-a", "resource": "gpu", "start": "2026-01-01T09:00:00+00:00",
     "end": "2026-01-01T10:00:00+00:00", "units": 2, "status": "confirmed"},
    {"id": "left-b", "resource": "gpu", "start": "2026-01-01T06:30:00-03:00",
     "end": "2026-01-01T07:00:00-03:00", "units": 1, "status": "confirmed"},
    {"id": "right", "resource": "gpu", "start": "2026-01-01T12:00:00+02:00",
     "end": "2026-01-01T13:00:00+02:00", "units": 3, "status": "confirmed"},
    {"id": "cpu", "resource": "cpu", "start": "2026-01-01T09:45:00+00:00",
     "end": "2026-01-01T10:15:00+00:00", "units": 2, "status": "confirmed"},
    {"id": "cancelled", "resource": "gpu", "start": "2026-01-01T09:00:00+00:00",
     "end": "2026-01-01T11:00:00+00:00", "units": 100, "status": "cancelled"},
]

RESERVATION_CAPACITY_BLOCK = '''            peak = max(peak, used)
            if used > capacities[resource]:
                raise CapacityExceeded(f"Resource {resource} exceeds capacity at {instant.isoformat()}")'''

CASES = [
    {
        "slug": "snapshot-pagination",
        "title": "Snapshot export loses tied events across a composite cursor boundary",
        "app_file": "app/pagination.py",
        "exception_type": "IncompleteExport",
        "baseline_count": 4,
        "acceptance_count": 5,
        "call": f"from app.pagination import export_events\nexport_events({PAGINATION_EVENTS!r}, 'alpha', 30, 2)\n",
        "request": {"events": PAGINATION_EVENTS, "tenant": "alpha", "snapshot": 30, "page_size": 2},
        "good_replacements": [[PAGINATION_OLD, PAGINATION_GOOD]],
        "reproducer": (
            "from app.pagination import export_events\n\n"
            "def test_snapshot_export_retains_every_tied_event():\n"
            f"    events = {PAGINATION_EVENTS!r}\n"
            "    assert export_events(events, 'alpha', 30, 2) == ['01', '02', '03', '04', '05']\n"
        ),
        "acceptance": dedent('''\
            import copy
            import pytest
            from app.pagination import event_page, export_events

            @pytest.mark.parametrize("size", [1, 2, 3, 10])
            def test_cursor_comparison_preserves_both_sort_directions(size):
                events = [
                    {"tenant": "a", "at": 20, "id": "b"},
                    {"tenant": "a", "at": 10, "id": "c"},
                    {"tenant": "a", "at": 30, "id": "z"},
                    {"tenant": "a", "at": 20, "id": "a"},
                ]
                before = copy.deepcopy(events)
                assert export_events(events, "a", 30, size) == ["z", "a", "b", "c"]
                assert events == before

            def test_lexical_tie_order_exclusive_cursor_and_snapshot():
                events = [
                    {"tenant": "a", "at": 5, "id": "2"},
                    {"tenant": "a", "at": 5, "id": "10"},
                    {"tenant": "b", "at": 5, "id": "10"},
                    {"tenant": "a", "at": 6, "id": "future"},
                ]
                first, cursor = event_page(events, "a", 5, 1)
                assert [row["id"] for row in first] == ["10"]
                assert cursor == (5, "10")
                second, cursor = event_page(events, "a", 5, 1, cursor)
                assert [row["id"] for row in second] == ["2"]
                assert cursor == (5, "2")
                assert event_page(events, "a", 5, 1, cursor) == ([], None)
                assert export_events(events, "a", 4, 1) == []
                assert export_events(events, "b", 5, 1) == ["10"]
        '''),
        "bad_replacements": [
            {"name": "id-comparison-without-timestamp-equality", "replacements": [[PAGINATION_OLD, PAGINATION_PARTIAL]]},
            {"name": "inclusive-cursor-repeats-boundary", "replacements": [[PAGINATION_OLD, '(row["at"], row["id"]) <= cursor']]},
        ],
    },
    {
        "slug": "reservation-boundaries",
        "title": "Mixed-offset reservation handoff produces a phantom capacity violation",
        "app_file": "app/reservations.py",
        "exception_type": "CapacityExceeded",
        "baseline_count": 4,
        "acceptance_count": 4,
        "call": f"from app.reservations import reservation_peaks\nreservation_peaks({RESERVATIONS!r}, {{'gpu': 3, 'cpu': 2, 'idle': 0}})\n",
        "request": {"reservations": RESERVATIONS, "capacities": {"gpu": 3, "cpu": 2, "idle": 0}},
        "good_replacements": [["key=lambda item: (item[0], -item[1])", "key=lambda item: (item[0], item[1])"]],
        "reproducer": (
            "from app.reservations import reservation_peaks\n\n"
            "def test_offset_equivalent_handoff_does_not_overbook():\n"
            f"    reservations = {RESERVATIONS!r}\n"
            "    assert reservation_peaks(reservations, {'gpu': 3, 'cpu': 2, 'idle': 0}) == {'gpu': 3, 'cpu': 2, 'idle': 0}\n"
        ),
        "acceptance": dedent('''\
            import copy
            import pytest
            from app.reservations import CapacityExceeded, reservation_peaks

            def row(identifier, start, end, units, resource="gpu", status="confirmed"):
                return {"id": identifier, "resource": resource, "start": start, "end": end,
                        "units": units, "status": status}

            def test_multiway_handoff_preserves_remaining_occupancy():
                reservations = [
                    row("long", "2026-02-01T00:00:00+00:00", "2026-02-01T03:00:00+00:00", 1),
                    row("left", "2026-02-01T00:00:00+00:00", "2026-02-01T01:00:00+00:00", 2),
                    row("right-a", "2026-02-01T03:00:00+02:00", "2026-02-01T04:00:00+02:00", 1),
                    row("right-b", "2026-01-31T22:00:00-03:00", "2026-01-31T23:00:00-03:00", 1),
                ]
                before = copy.deepcopy(reservations)
                assert reservation_peaks(reservations, {"gpu": 3}) == {"gpu": 3}
                assert reservation_peaks(list(reversed(reservations)), {"gpu": 3}) == {"gpu": 3}
                assert reservations == before

            def test_real_overlap_one_microsecond_before_handoff_is_rejected():
                reservations = [
                    row("left", "2026-02-01T09:00:00+00:00", "2026-02-01T10:00:00+00:00", 2),
                    row("right", "2026-02-01T11:59:59.999999+02:00", "2026-02-01T13:00:00+02:00", 2),
                ]
                with pytest.raises(CapacityExceeded, match="gpu"):
                    reservation_peaks(reservations, {"gpu": 3})

            def test_peak_is_not_the_sum_of_non_overlapping_bookings():
                reservations = [
                    row("left", "2026-02-01T09:00:00+00:00", "2026-02-01T10:00:00+00:00", 2),
                    row("right", "2026-02-01T10:00:00+00:00", "2026-02-01T11:00:00+00:00", 1),
                    row("noise", "2026-02-01T09:00:00+00:00", "2026-02-01T11:00:00+00:00", 99, status="cancelled"),
                ]
                assert reservation_peaks(reservations, {"gpu": 7, "empty": 0}) == {"gpu": 2, "empty": 0}
                assert reservation_peaks([], {"gpu": 3}) == {"gpu": 0}

            def test_timezone_normalization_does_not_hide_true_overlap():
                reservations = [
                    row("utc", "2026-02-01T09:00:00+00:00", "2026-02-01T11:00:00+00:00", 2),
                    row("offset", "2026-02-01T11:30:00+02:00", "2026-02-01T12:00:00+02:00", 2),
                ]
                with pytest.raises(CapacityExceeded, match="gpu"):
                    reservation_peaks(reservations, {"gpu": 3})
        '''),
        "bad_replacements": [
            {"name": "clamp-peak-and-suppress-capacity-errors", "replacements": [
                [RESERVATION_CAPACITY_BLOCK, "            peak = min(capacities[resource], max(peak, used))"]
            ]},
            {"name": "compare-local-clock-times", "replacements": [
                ["key=lambda item: (item[0], -item[1])", "key=lambda item: (item[0], item[1])"],
                ["return parsed.astimezone(timezone.utc)", "return parsed.replace(tzinfo=timezone.utc)"],
            ]},
        ],
    },
]
