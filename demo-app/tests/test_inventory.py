"""Original inventory coverage for ordered history and invalid returns."""
from copy import deepcopy

import pytest

from app.inventory import InventoryError, reconcile_inventory


def event(event_id, seq, kind, quantity, ref=None, tenant="alpha", sku="bolt"):
    record = dict(id=event_id, seq=seq, tenant=tenant, sku=sku, kind=kind, quantity=quantity)
    if ref is not None:
        record["ref"] = ref
    return record


def test_out_of_order_retries_and_two_partial_returns_preserve_inputs():
    opening = {"alpha": {"bolt": 8}}
    shipment = event("s1", 2, "ship", 7)
    events = [event("r2", 4, "return", 2, "s1"), shipment,
              event("in1", 1, "receive", 3), event("r1", 3, "return", 1, "s1"), shipment.copy()]
    snapshot = deepcopy((opening, events))
    assert reconcile_inventory(opening, events) == {"alpha": {"bolt": 7}}
    assert (opening, events) == snapshot


def test_multiple_shipments_have_independent_return_balances():
    assert reconcile_inventory({"alpha": {"bolt": 10}}, [
        event("s1", 1, "ship", 5), event("s2", 2, "ship", 2),
        event("r1", 3, "return", 4, "s1"), event("r2", 4, "return", 2, "s2"),
    ]) == {"alpha": {"bolt": 9}}


@pytest.mark.parametrize("events", [
    [event("s", 1, "ship", 11)],
    [event("s", 1, "ship", 3), event("r1", 2, "return", 2, "s"), event("r2", 3, "return", 2, "s")],
    [event("r", 1, "return", 1, "missing")],
    [event("s", 1, "ship", 3), event("r", 2, "return", 1, "s", sku="nut")],
    [event("s", 1, "ship", 3), event("r", 2, "return", 1, "s", tenant="beta")],
    [event("in", 1, "receive", 2), event("in", 1, "receive", 3)],
])
def test_invalid_history_is_rejected(events):
    with pytest.raises(InventoryError):
        reconcile_inventory({"alpha": {"bolt": 10}}, events)
