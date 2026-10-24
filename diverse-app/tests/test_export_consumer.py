import pytest

from app.export_consumer import ExportPayloadError, export_rows, read_export


def test_simple_csv_records_preserve_order_and_zero_quantity():
    records = [{"sku": "bolt", "quantity": 3}, {"sku": "nut", "quantity": 0}]
    assert export_rows(records) == [{"sku": "bolt", "quantity": 3}, {"sku": "nut", "quantity": 0}]


def test_empty_csv_export_keeps_header_without_data_rows():
    assert export_rows([]) == []


@pytest.mark.parametrize("payload", [
    {"content": "quantity,sku\n3,bolt\n", "rows": 1},
    {"content": "sku,quantity\nbolt,3,extra\n", "rows": 1},
    {"content": "sku,quantity\nbolt,three\n", "rows": 1},
    {"content": "sku,quantity\nbolt,3\n", "rows": 2},
])
def test_consumer_rejects_invalid_transport(payload):
    with pytest.raises(ExportPayloadError):
        read_export(payload)
