"""Validate order payloads before accepting their monetary total."""
from app.order_payload import build_order_payload


class OrderPayloadError(ValueError):
    """An order payload violates the consumer's fixed input contract."""


def accept_order(payload: dict) -> dict:
    """Return the SKU, quantity and total after validating integer-cent arithmetic."""
    if not isinstance(payload["sku"], str) or not payload["sku"]:
        raise OrderPayloadError("Order SKU must be a nonempty string")
    quantity, price = payload["quantity"], payload["unit_price_cents"]
    if type(quantity) is not int or quantity <= 0 or type(price) is not int or price < 0:
        raise OrderPayloadError("Order quantity and unit price are invalid")
    if type(payload["total_cents"]) is not int or payload["total_cents"] != quantity * price:
        raise OrderPayloadError("Order total does not match quantity times unit price")
    return {"sku": payload["sku"], "quantity": quantity, "total_cents": payload["total_cents"]}


def place_order(sku: str, quantity: int, unit_price_cents: int) -> dict:
    """Build a payload, then independently validate it before accepting the order."""
    payload = build_order_payload(sku, quantity, unit_price_cents)
    return accept_order(payload)
