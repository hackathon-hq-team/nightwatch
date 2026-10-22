import pytest

from app.pagination import event_page, export_events


def test_distinct_times_span_pages_and_filter_tenants():
    events = [
        {"tenant": "a", "at": 20, "id": "02"},
        {"tenant": "a", "at": 30, "id": "01"},
        {"tenant": "b", "at": 25, "id": "foreign"},
        {"tenant": "a", "at": 10, "id": "03"},
        {"tenant": "a", "at": 40, "id": "future"},
    ]
    assert export_events(events, "a", 30, 1) == ["01", "02", "03"]


def test_tied_events_fit_one_page_in_id_order():
    events = [{"tenant": "a", "at": 5, "id": value} for value in ["b", "a"]]
    page, cursor = event_page(events, "a", 5, 10)
    assert [row["id"] for row in page] == ["a", "b"]
    assert cursor == (5, "b")


def test_empty_snapshot_and_invalid_size():
    assert event_page([], "a", 10, 2) == ([], None)
    assert export_events([], "a", 10, 2) == []
    for size in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            event_page([], "a", 10, size)


def test_duplicate_ids_are_rejected_in_the_selected_tenant():
    rows = [{"tenant": "a", "at": at, "id": "same"} for at in [1, 2]]
    with pytest.raises(ValueError, match="unique"):
        event_page(rows, "a", 2, 1)
