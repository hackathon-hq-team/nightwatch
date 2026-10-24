"""Existing coverage for ordinary refunds, ledger identity and invalid requests."""
from copy import deepcopy

import pytest

from app.refunds import RefundError, refund_ledger


def sale(identifier="s", currency="USD", units=10, merchant=1, platform=1):
    return dict(id=identifier, currency=currency, units=units,
                weights={"merchant": merchant, "platform": platform})


def refund(identifier="r", sale_id="s", currency="USD", units=2):
    return dict(id=identifier, sale=sale_id, currency=currency, units=units)


def rows(sale_id, currency, refund_id, merchant, platform):
    return [{"currency": currency, "sale": sale_id, "refund": refund_id,
             "account": "merchant", "units": merchant},
            {"currency": currency, "sale": sale_id, "refund": refund_id,
             "account": "platform", "units": platform}]


def test_single_partial_and_full_refund_use_original_allocations():
    assert refund_ledger([sale()], [refund(units=3)]) == rows("s", "USD", "r", 1, 2)
    assert refund_ledger([sale(units=9, merchant=2)], [refund(units=9)]) == rows("s", "USD", "r", 6, 3)


def test_sale_and_currency_identity_are_independent_and_inputs_are_unchanged():
    sales = [sale(), sale(currency="JPY", units=8), sale(identifier="other", currency="KWD", units=4)]
    events = [refund(), refund(currency="JPY"), refund(sale_id="other", currency="KWD")]
    before = deepcopy((sales, events))
    assert refund_ledger(sales, events) == (rows("s", "USD", "r", 1, 1) +
        rows("s", "JPY", "r", 1, 1) + rows("other", "KWD", "r", 1, 1))
    assert (sales, events) == before


def test_final_identical_retry_emits_no_extra_rows_and_zero_amounts_remain():
    event = refund(units=1)
    assert refund_ledger([sale()], [event, dict(event)]) == rows("s", "USD", "r", 0, 1)
    assert refund_ledger([sale(units=1)], [event]) == rows("s", "USD", "r", 0, 1)
    assert refund_ledger([], []) == []


@pytest.mark.parametrize("events", [
    [refund(units=11)], [refund(units=0)], [refund(units=True)],
    [refund(sale_id="missing")], [refund(), refund(units=3)],
])
def test_invalid_refund_is_rejected(events):
    with pytest.raises(RefundError):
        refund_ledger([sale()], events)


@pytest.mark.parametrize("sales", [
    [sale(), sale()], [sale(currency="XYZ")], [sale(units=0)],
    [sale(merchant=0)], [sale(platform=True)],
])
def test_invalid_sale_is_rejected(sales):
    with pytest.raises(RefundError):
        refund_ledger(sales, [])
