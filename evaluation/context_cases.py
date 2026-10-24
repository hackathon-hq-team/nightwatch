"""Cross-file fixture oracles: producer defects surface later in consumer frames."""
from textwrap import dedent


EXPORT_BEFORE = '''    content = "sku,quantity\\n"
    for row in records:
        content += f'{row["sku"]},{row["quantity"]}\\n'
'''
EXPORT_AFTER = '''    import csv
    from io import StringIO

    stream = StringIO()
    writer = csv.writer(stream, lineterminator="\\n")
    writer.writerow(["sku", "quantity"])
    for row in records:
        writer.writerow([row["sku"], row["quantity"]])
    content = stream.getvalue()
'''

CASES = [
    {
        "slug": "order-producer-total",
        "title": "Order consumer rejects a multi-unit payload after its producer returns",
        "app_file": "app/order_payload.py",
        "failure_file": "app/order_consumer.py",
        "source_files": ["app/order_payload.py", "app/order_consumer.py"],
        "baseline_files": ["tests/test_order_consumer.py"],
        "exception_type": "OrderPayloadError",
        "baseline_count": 8,
        "acceptance_count": 4,
        "call": "from app.order_consumer import place_order\nplace_order('bolt', 3, 125)\n",
        "request": {"sku": "bolt", "quantity": 3, "unit_price_cents": 125},
        "good_replacements": [['"total_cents": unit_price_cents}', '"total_cents": quantity * unit_price_cents}']],
        "reproducer": dedent('''\
            from app.order_consumer import place_order

            def test_multi_unit_order_total():
                assert place_order("bolt", 3, 125) == {"sku": "bolt", "quantity": 3, "total_cents": 375}
        '''),
        "acceptance": dedent('''\
            import pytest
            from app.order_consumer import place_order

            @pytest.mark.parametrize("quantity,total", [(2, 250), (4, 500), (10, 1250)])
            def test_alternate_quantities_have_literal_totals(quantity, total):
                assert place_order("bolt", quantity, 125) == {
                    "sku": "bolt", "quantity": quantity, "total_cents": total}

            def test_small_prices_and_free_orders_preserve_requested_quantity():
                assert place_order("nut", 7, 1) == {"sku": "nut", "quantity": 7, "total_cents": 7}
                assert place_order("sample", 100, 0) == {"sku": "sample", "quantity": 100, "total_cents": 0}
        '''),
        "bad_replacements": [
            {"name": "cap-multiplier-at-observed-quantity", "replacements": [
                ['"total_cents": unit_price_cents}', '"total_cents": min(quantity, 3) * unit_price_cents}'],
            ]},
        ],
    },
    {
        "slug": "csv-producer-escaping",
        "title": "Export consumer rejects comma-containing SKU after CSV producer returns",
        "app_file": "app/export_payload.py",
        "failure_file": "app/export_consumer.py",
        "source_files": ["app/export_payload.py", "app/export_consumer.py"],
        "baseline_files": ["tests/test_export_consumer.py"],
        "exception_type": "ExportPayloadError",
        "baseline_count": 6,
        "acceptance_count": 3,
        "call": "from app.export_consumer import export_rows\nexport_rows([{'sku': 'nut, M8', 'quantity': 4}])\n",
        "request": {"records": [{"sku": "nut, M8", "quantity": 4}]},
        "good_replacements": [[EXPORT_BEFORE, EXPORT_AFTER]],
        "reproducer": dedent('''\
            from app.export_consumer import export_rows

            def test_comma_in_sku_round_trips_through_csv():
                assert export_rows([{"sku": "nut, M8", "quantity": 4}]) == [{"sku": "nut, M8", "quantity": 4}]
        '''),
        "acceptance": dedent('''\
            import copy
            from app.export_consumer import export_rows

            def test_embedded_quotes_are_preserved():
                assert export_rows([{"sku": 'bolt "A"', "quantity": 2}]) == [{"sku": 'bolt "A"', "quantity": 2}]

            def test_newlines_quotes_and_commas_preserve_record_boundaries():
                records = [{"sku": 'nut, "M8"\\nblue', "quantity": 0}, {"sku": "washer", "quantity": 12}]
                original = copy.deepcopy(records)
                assert export_rows(records) == [
                    {"sku": 'nut, "M8"\\nblue', "quantity": 0}, {"sku": "washer", "quantity": 12}]
                assert records == original

            def test_empty_and_single_plain_row_keep_shape():
                assert export_rows([]) == []
                assert export_rows([{"sku": "plain", "quantity": 1}]) == [{"sku": "plain", "quantity": 1}]
        '''),
        "bad_replacements": [
            {"name": "quote-fields-without-escaping-embedded-quotes", "replacements": [
                ['        content += f\'{row["sku"]},{row["quantity"]}\\n\'',
                 '        content += f\'"{row["sku"]}",{row["quantity"]}\\n\''],
            ]},
        ],
    },
]
