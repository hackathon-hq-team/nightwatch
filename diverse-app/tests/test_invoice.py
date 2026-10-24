"""Existing ordinary invoice behavior and input validation."""
import pytest

from app.invoice import InvoiceError, invoice_total
from app.receipt_view import receipt_theme


def test_no_discount_and_empty_invoice():
    assert invoice_total([199, 301], 0) == 500
    assert invoice_total([], 0) == 0


@pytest.mark.parametrize("prices,bps", [([-1], 0), ([True], 0), ([100], -1), ([100], 10001)])
def test_reject_invalid_amount_or_discount(prices, bps):
    with pytest.raises(InvoiceError):
        invoice_total(prices, bps)


def test_receipt_settings_are_only_presentation():
    theme = receipt_theme()
    assert set(theme) == {"accent", "heading", "badge"}
    assert isinstance(theme["heading"], str) and type(theme["badge"]) is bool
    assert invoice_total([100], 0) == 100
