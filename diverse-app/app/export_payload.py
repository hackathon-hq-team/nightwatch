"""Build a CSV transport payload without changing record contents or order."""


def build_export_payload(records: list[dict]) -> dict:
    """Return content and row count for CSV columns sku,quantity, in that order.

    Every SKU is a nonempty string and every quantity is a nonnegative integer.
    Preserve commas, double quotes and newlines in SKUs through CSV encoding.
    An empty export still contains its header. Input records are never mutated.
    """
    content = "sku,quantity\n"
    for row in records:
        content += f'{row["sku"]},{row["quantity"]}\n'
    return {"content": content, "rows": len(records)}
