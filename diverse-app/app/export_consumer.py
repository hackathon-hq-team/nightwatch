"""Read CSV transport payloads and reject malformed records."""
import csv
from io import StringIO

from app.export_payload import build_export_payload


class ExportPayloadError(RuntimeError):
    """An export has malformed fields, values or an inconsistent row count."""


def read_export(payload: dict) -> list[dict]:
    """Return ordered sku/quantity records after checking the CSV transport."""
    try:
        reader = csv.DictReader(StringIO(payload["content"]), strict=True)
        if reader.fieldnames != ["sku", "quantity"]:
            raise ExportPayloadError("Export has unexpected CSV headers")
        records = []
        for row in reader:
            if set(row) != {"sku", "quantity"} or not row["sku"] or row["quantity"] is None:
                raise ExportPayloadError("Export row has unexpected CSV fields")
            quantity = int(row["quantity"])
            if quantity < 0 or str(quantity) != row["quantity"]:
                raise ExportPayloadError("Export quantities must be nonnegative integers")
            records.append({"sku": row["sku"], "quantity": quantity})
    except (csv.Error, ValueError) as error:
        raise ExportPayloadError("Malformed CSV export") from error
    if len(records) != payload["rows"]:
        raise ExportPayloadError("Export row count does not match its manifest")
    return records


def export_rows(records: list[dict]) -> list[dict]:
    """Build the transport, then read it through the fixed consumer contract."""
    payload = build_export_payload(records)
    return read_export(payload)
