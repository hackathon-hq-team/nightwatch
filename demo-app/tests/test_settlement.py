"""Original settlement coverage: ordinary payouts and independent validation."""
import pytest

from app.settlement import SettlementError, allocate_settlements


def test_weighted_net_payouts_keep_currency_accounts_separate():
    assert allocate_settlements([
        {"currency": "USD", "gross": "12.00", "fee": "2.00", "weights": {"a": 1, "b": 3}},
        {"currency": "JPY", "gross": "10", "fee": "2", "weights": {"a": 1, "b": 1}},
        {"currency": "USD", "gross": "1.00", "fee": "0", "weights": {"a": 1}},
    ]) == [{"currency": "JPY", "account": "a", "amount": "4"},
           {"currency": "JPY", "account": "b", "amount": "4"},
           {"currency": "USD", "account": "a", "amount": "3.50"},
           {"currency": "USD", "account": "b", "amount": "7.50"}]


def test_subunit_remainder_uses_account_order_and_zero_net_is_valid():
    assert allocate_settlements([
        {"currency": "KWD", "gross": "0.001", "fee": "0", "weights": {"z": 1, "a": 1, "m": 1}},
        {"currency": "KWD", "gross": "3.000", "fee": "3.000", "weights": {"a": 7}},
    ]) == [{"currency": "KWD", "account": "a", "amount": "0.001"},
           {"currency": "KWD", "account": "m", "amount": "0.000"},
           {"currency": "KWD", "account": "z", "amount": "0.000"}]


def test_supported_money_boundary_and_exact_aggregation():
    assert allocate_settlements([
        {"currency": "KWD", "gross": "999999999999.999", "fee": "0", "weights": {"a": 1}},
        {"currency": "KWD", "gross": "999999999999.999", "fee": "0", "weights": {"a": 1}},
        {"currency": "JPY", "gross": "999999999999", "fee": "999999999998", "weights": {"a": 1}},
        {"currency": "USD", "gross": "999999999999.99", "fee": "999999999999.98", "weights": {"a": 1}},
    ]) == [{"currency": "JPY", "account": "a", "amount": "1"},
           {"currency": "KWD", "account": "a", "amount": "1999999999999.998"},
           {"currency": "USD", "account": "a", "amount": "0.01"}]


@pytest.mark.parametrize("changes", [
    {"gross": "0.001"}, {"fee": "2.00"}, {"gross": "NaN"},
    {"weights": {"a": 0}}, {"weights": {"a": True}}, {"currency": "XYZ"},
    {"gross": "1000000000000"}, {"fee": "1000000000000"},
    {"gross": "100000000000000000000000000.01"},
])
def test_invalid_settlement_is_rejected(changes):
    batch = {"currency": "USD", "gross": "1.00", "fee": "0", "weights": {"a": 1}}
    with pytest.raises(SettlementError):
        allocate_settlements([batch | changes])
