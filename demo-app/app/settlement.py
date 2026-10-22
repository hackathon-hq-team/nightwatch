"""Allocate net settlement balances in currency minor units without losing money."""
from decimal import Decimal, ROUND_HALF_UP


class SettlementError(ValueError):
    """A settlement cannot satisfy its monetary or allocation constraints."""


def _format_units(units: int, places: int) -> str:
    whole, fraction = divmod(units, 10 ** places)
    return f"{whole}.{fraction:0{places}d}" if places else str(whole)


def allocate_settlements(batches: list[dict]) -> list[dict]:
    """Allocate each gross-minus-fee balance by positive integer weights.

    USD has two decimal places, JPY zero and KWD three. Every batch uses
    largest remainders, with lexicographic account IDs breaking equal ties.
    Aggregate repeated accounts only within their currency; return rows sorted
    by currency then account. Each gross and fee must be finite, nonnegative,
    exact minor units and strictly below 1,000,000,000,000 currency units.
    Aggregated account totals may exceed that per-input bound.
    """
    digits = {"USD": 2, "JPY": 0, "KWD": 3}
    totals = {}
    for batch in batches:
        currency = batch["currency"]
        if currency not in digits:
            raise SettlementError("Unsupported currency")
        scale = Decimal(10) ** digits[currency]
        gross, fee = (Decimal(batch[key]) for key in ("gross", "fee"))
        if any(not value.is_finite() or value < 0 or value >= Decimal("1000000000000") or
               value != value.quantize(Decimal(1) / scale) for value in (gross, fee)):
            raise SettlementError("Amounts must be exact minor units in [0, 1000000000000)")
        units = int((gross - fee) * scale)
        if units < 0:
            raise SettlementError("Fee exceeds gross settlement")
        weights = batch["weights"]
        if not weights or any(not isinstance(account, str) or not account or
                              type(weight) is not int or weight <= 0
                              for account, weight in weights.items()):
            raise SettlementError("Accounts require positive integer weights")
        denominator = sum(weights.values())
        shares, remainders = {}, {}
        for account, weight in weights.items():
            # Keep the fractional numerator exact even when decimal division repeats.
            numerator = units * weight
            quotient, remainder = divmod(numerator, denominator)
            quota = Decimal(numerator) / Decimal(denominator)
            shares[account] = int(quota.to_integral_value(rounding=ROUND_HALF_UP))
            remainders[account] = remainder
        residual = units - sum(shares.values())
        if residual < 0:
            raise SettlementError("Rounded allocations exceed the net settlement")
        ranking = sorted(weights, key=lambda account: (-remainders[account], account))
        for account in ranking[:residual]:
            shares[account] += 1
        if sum(shares.values()) != units:
            raise SettlementError("Settlement allocation does not conserve minor units")
        for account, amount in shares.items():
            key = (currency, account)
            totals[key] = totals.get(key, 0) + amount
    return [{"currency": currency, "account": account,
             "amount": _format_units(units, digits[currency])}
            for (currency, account), units in sorted(totals.items())]
