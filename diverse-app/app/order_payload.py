"""Build order payloads using integer prices in cents."""


def build_order_payload(sku: str, quantity: int, unit_price_cents: int) -> dict:
    """Preserve the SKU and quantity; total_cents equals quantity times unit price.

    Valid inputs use a nonempty SKU, a positive integer quantity, and a
    nonnegative integer unit price. No discount, fee or rounding applies.
    """
    return {"sku": sku, "quantity": quantity, "unit_price_cents": unit_price_cents,
            "total_cents": unit_price_cents}
