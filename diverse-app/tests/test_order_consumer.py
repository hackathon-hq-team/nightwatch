import pytest

from app.order_consumer import OrderPayloadError, accept_order, place_order


def test_single_unit_order_retains_its_total():
    assert place_order("bolt", 1, 125) == {"sku": "bolt", "quantity": 1, "total_cents": 125}


def test_multiple_free_units_preserve_quantity():
    assert place_order("sample", 4, 0) == {"sku": "sample", "quantity": 4, "total_cents": 0}


def test_consumer_rejects_inconsistent_totals():
    with pytest.raises(OrderPayloadError, match="total"):
        accept_order({"sku": "bolt", "quantity": 2, "unit_price_cents": 125, "total_cents": 1})


@pytest.mark.parametrize("quantity,price", [(0, 1), (-1, 1), (True, 1), (1, -1), (1, True)])
def test_consumer_rejects_invalid_quantity_or_price(quantity, price):
    with pytest.raises(OrderPayloadError):
        place_order("bolt", quantity, price)
