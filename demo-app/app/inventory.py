"""Fold ordered inventory events, including retries and partial shipment returns."""


class InventoryError(ValueError):
    """An event conflicts with the tenant's inventory or shipment history."""


def reconcile_inventory(opening: dict, events: list[dict]) -> dict:
    """Return stock after events ordered by sequence, tenant and event ID.

    Event IDs are unique only within a tenant. Identical retries count once;
    conflicting retries fail. Receipts add stock, shipments consume it, and
    returns restore only quantities shipped by the referenced tenant event.
    Multiple partial returns may not exceed the original shipment quantity.
    Input dictionaries are never mutated.
    """
    stock = {tenant: dict(items) for tenant, items in opening.items()}
    if any(type(quantity) is not int or quantity < 0
           for items in stock.values() for quantity in items.values()):
        raise InventoryError("Opening stock must contain nonnegative integers")
    seen, shipments = {}, {}
    for event in sorted(events, key=lambda item: (item["seq"], item["tenant"], item["id"])):
        tenant, event_id, sku = (event[key] for key in ("tenant", "id", "sku"))
        quantity, kind = event["quantity"], event["kind"]
        if type(quantity) is not int or quantity <= 0 or type(event["seq"]) is not int:
            raise InventoryError("Event quantity must be positive and sequence integral")
        key = event_id
        if key in seen:
            if seen[key] != event:
                raise InventoryError("Conflicting duplicate inventory event")
            continue
        seen[key] = dict(event)
        items = stock.setdefault(tenant, {})
        available = items.get(sku, 0)
        if kind == "receive":
            items[sku] = available + quantity
        elif kind == "ship":
            if quantity > available:
                raise InventoryError("Shipment exceeds available stock")
            items[sku] = available - quantity
            shipments[(tenant, event_id)] = [sku, quantity]
        elif kind == "return":
            shipment = shipments.get((tenant, event.get("ref")))
            if shipment is None or shipment[0] != sku:
                raise InventoryError("Return has no matching tenant shipment")
            if quantity > shipment[1]:
                raise InventoryError("Returns exceed the original shipment quantity")
            shipment[1] -= quantity
            items[sku] = available + quantity
        else:
            raise InventoryError("Unsupported inventory event kind")
    return {tenant: dict(sorted(items.items())) for tenant, items in sorted(stock.items())}
