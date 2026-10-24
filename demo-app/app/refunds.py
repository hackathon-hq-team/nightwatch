"""Replay partial refunds against the original merchant/platform sale allocation."""


class RefundError(ValueError):
    """A refund violates sale identity, replay integrity, or monetary limits."""


def refund_ledger(sales: list[dict], refunds: list[dict]) -> list[dict]:
    """Return merchant then platform rows for each unique refund, including zeros.

    Amounts are positive integer minor units in USD, JPY or KWD. Sale IDs are
    unique within currency; refund IDs are unique within a sale. Replay refunds
    in supplied order. Identical retries have no effect; conflicting retries fail.
    A sale's merchant share is floor(units * merchant weight / total weight),
    with the balance assigned to platform. At every refund prefix, merchant has
    received floor(total refunded * original merchant share / sale units), with
    platform receiving the balance. Never refund more than the original sale or
    either original recipient allocation. Leave caller-owned inputs unchanged.
    """
    accounts = ("merchant", "platform")
    book, refunded, returned, seen = {}, {}, {}, {}
    for sale in sales:
        key = (sale["currency"], sale["id"])
        units, weights = sale["units"], sale["weights"]
        if (key[0] not in ("USD", "JPY", "KWD") or
                not isinstance(key[1], str) or not key[1] or key in book):
            raise RefundError("Sale identity must be supported and unique")
        if type(units) is not int or units <= 0:
            raise RefundError("Sale units must be a positive integer")
        if set(weights) != set(accounts) or any(type(w) is not int or w <= 0 for w in weights.values()):
            raise RefundError("Merchant and platform require positive integer weights")
        merchant = units * weights["merchant"] // sum(weights.values())
        book[key] = (units, {"merchant": merchant, "platform": units - merchant})
        refunded[key], returned[key] = 0, {account: 0 for account in accounts}
    ledger = []
    for refund in refunds:
        key = (refund["currency"], refund["sale"])
        amount, refund_id = refund["units"], refund["id"]
        if key not in book or not isinstance(refund_id, str) or not refund_id:
            raise RefundError("Refund must identify an existing sale and a refund ID")
        if type(amount) is not int or amount <= 0:
            raise RefundError("Refund units must be a positive integer")
        total, allocation = book[key]
        previous = refunded[key]
        cumulative = previous + amount
        if cumulative > total:
            raise RefundError("Refund exceeds the original sale total")
        refunded[key] = cumulative
        replay_key = (*key, refund_id)
        if replay_key in seen:
            if seen[replay_key] != refund:
                raise RefundError("Conflicting refund retry")
            continue
        seen[replay_key] = dict(refund)
        merchant = amount * allocation["merchant"] // total
        amounts = {"merchant": merchant, "platform": amount - merchant}
        for account in accounts:
            returned[key][account] += amounts[account]
            if returned[key][account] > allocation[account]:
                raise RefundError("Refund exceeds the original recipient allocation")
            ledger.append({"currency": key[0], "sale": key[1], "refund": refund_id,
                           "account": account, "units": amounts[account]})
    return ledger
