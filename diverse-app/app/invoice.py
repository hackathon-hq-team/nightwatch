"""Compute an invoice in integer cents; display configuration is independent."""


class InvoiceError(ValueError):
    """The invoice cannot preserve its monetary contract."""


def invoice_total(prices: list[int], discount_bps: int) -> int:
    """Sum nonnegative integer cents, then subtract the floor of the discount.

    Discount basis points must be an integer in [0, 10000]. Empty invoices cost
    zero. The result cannot exceed the subtotal. Do not mutate input prices.
    Receipt colors, labels and layout never influence monetary calculations.
    """
    if any(type(price) is not int or price < 0 for price in prices):
        raise InvoiceError("Prices must be nonnegative integer cents")
    if type(discount_bps) is not int or not 0 <= discount_bps <= 10000:
        raise InvoiceError("Discount must be integer basis points in [0, 10000]")
    subtotal = sum(prices)
    discount = subtotal * discount_bps // 10000
    total = subtotal + discount
    if total > subtotal:
        raise InvoiceError("Discounted total exceeds subtotal")
    return total
