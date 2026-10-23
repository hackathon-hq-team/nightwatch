"""Trusted fixture inputs, independent expected answers and deliberately bad patches.

These evaluation-only sources are never copied into the generated application.
Prepared patches check the mechanism; they are not evidence of live model ability.
"""
from textwrap import dedent


SETTLEMENT_REQUEST = {"batches": [
    {"currency": "KWD", "gross": "0.001", "fee": "0", "weights": {"z": 1, "a": 1, "m": 1}},
    {"currency": "USD", "gross": "100.05", "fee": "100.00", "weights": {"b": 1, "a": 1, "c": 1}},
]}
SETTLEMENT_EXPECTED = [
    {"currency": "KWD", "account": "a", "amount": "0.001"},
    {"currency": "KWD", "account": "m", "amount": "0.000"},
    {"currency": "KWD", "account": "z", "amount": "0.000"},
    {"currency": "USD", "account": "a", "amount": "0.02"},
    {"currency": "USD", "account": "b", "amount": "0.02"},
    {"currency": "USD", "account": "c", "amount": "0.01"},
]
SETTLEMENT_FIX = [
    ["from decimal import Decimal, ROUND_HALF_UP", "from decimal import Decimal"],
    ["            quota = Decimal(numerator) / Decimal(denominator)\n", ""],
    ["            shares[account] = int(quota.to_integral_value(rounding=ROUND_HALF_UP))",
     "            shares[account] = quotient"],
]


def _event(tenant, event_id, seq, kind, quantity, ref=None, sku="bolt"):
    record = dict(tenant=tenant, id=event_id, seq=seq, kind=kind, quantity=quantity, sku=sku)
    if ref is not None:
        record["ref"] = ref
    return record


INVENTORY_REQUEST = {"opening": {"alpha": {"bolt": 10}, "beta": {"bolt": 8}}, "events": [
    _event("beta", "return", 3, "return", 1, "ship"),
    _event("alpha", "ship", 2, "ship", 7),
    _event("beta", "receive", 1, "receive", 5),
    _event("alpha", "return", 3, "return", 2, "ship"),
    _event("beta", "ship", 2, "ship", 6),
    _event("alpha", "receive", 1, "receive", 2),
    _event("alpha", "ship", 2, "ship", 7),
]}
INVENTORY_EXPECTED = {"alpha": {"bolt": 7}, "beta": {"bolt": 8}}
INVENTORY_FIX = [["        key = event_id", "        key = (tenant, event_id)"]]


CASES = [
    {
        "slug": "settlement-rounding",
        "title": "Fee-adjusted mixed-currency payouts overspend during weighted rounding",
        "app_file": "app/settlement.py",
        "exception_type": "SettlementError",
        "baseline_count": 12,
        "acceptance_count": 4,
        "call": "from app.settlement import allocate_settlements\n"
                f"allocate_settlements(**{SETTLEMENT_REQUEST!r})\n",
        "request": SETTLEMENT_REQUEST,
        "good_replacements": SETTLEMENT_FIX,
        "reproducer": "from app.settlement import allocate_settlements\n\n"
                      "def test_fee_adjusted_mixed_currency_allocation():\n"
                      f"    assert allocate_settlements(**{SETTLEMENT_REQUEST!r}) == {SETTLEMENT_EXPECTED!r}\n",
        "acceptance": dedent('''\
            from copy import deepcopy
            import pytest
            from app.settlement import SettlementError, allocate_settlements

            def test_large_supported_net_values_and_rejection_beyond_the_contract():
                assert allocate_settlements([
                    {"currency": "USD", "gross": "999999999999.99", "fee": "999999999999.97", "weights": {"a": 1, "b": 1, "c": 2}},
                    {"currency": "USD", "gross": "999999999999.99", "fee": "0", "weights": {"a": 1}},
                ]) == [
                    {"currency": "USD", "account": "a", "amount": "1000000000000.00"},
                    {"currency": "USD", "account": "b", "amount": "0.00"},
                    {"currency": "USD", "account": "c", "amount": "0.01"},
                ]
                for gross in ("1000000000000", "100000000000000000000000000.01"):
                    with pytest.raises(SettlementError):
                        allocate_settlements([
                            {"currency": "USD", "gross": gross, "fee": "0", "weights": {"a": 1}},
                        ])

            def test_unequal_weights_aggregate_without_changing_the_input():
                batches = [
                    {"currency": "USD", "gross": "1.07", "fee": "1.00", "weights": {"a": 1, "b": 2, "c": 3}},
                    {"currency": "USD", "gross": "0.02", "fee": "0", "weights": {"c": 1, "b": 1, "a": 1}},
                    {"currency": "JPY", "gross": "13", "fee": "2", "weights": {"a": 2, "b": 3, "c": 5}},
                ]
                before = deepcopy(batches)
                assert allocate_settlements(batches) == [
                    {"currency": "JPY", "account": "a", "amount": "2"},
                    {"currency": "JPY", "account": "b", "amount": "3"},
                    {"currency": "JPY", "account": "c", "amount": "6"},
                    {"currency": "USD", "account": "a", "amount": "0.02"},
                    {"currency": "USD", "account": "b", "amount": "0.03"},
                    {"currency": "USD", "account": "c", "amount": "0.04"},
                ]
                assert batches == before

            def test_unequal_weights_can_require_downward_rounding_before_redistribution():
                assert allocate_settlements([
                    {"currency": "USD", "gross": "0.02", "fee": "0", "weights": {"a": 1, "b": 1, "c": 2}},
                ]) == [
                    {"currency": "USD", "account": "a", "amount": "0.01"},
                    {"currency": "USD", "account": "b", "amount": "0.00"},
                    {"currency": "USD", "account": "c", "amount": "0.01"},
                ]

            def test_remainder_awards_do_not_depend_on_mapping_insertion_order():
                for weights in ({"z": 1, "m": 1, "a": 1}, {"a": 1, "z": 1, "m": 1}):
                    assert allocate_settlements([
                        {"currency": "KWD", "gross": "0.002", "fee": "0", "weights": weights},
                    ]) == [
                        {"currency": "KWD", "account": "a", "amount": "0.001"},
                        {"currency": "KWD", "account": "m", "amount": "0.001"},
                        {"currency": "KWD", "account": "z", "amount": "0.000"},
                    ]
            '''),
        "bad_replacements": [
            {"name": "equal-weight-bailout", "replacements": [[
                '        if residual < 0:\n            raise SettlementError("Rounded allocations exceed the net settlement")',
                '        if residual < 0:\n            shares = {account: units // len(weights) for account in weights}\n'
                '            residual = units - sum(shares.values())',
            ]]},
            {"name": "overwrite-earlier-batch", "replacements": SETTLEMENT_FIX + [[
                "totals[key] = totals.get(key, 0) + amount", "totals[key] = amount",
            ]]},
        ],
    },
    {
        "slug": "inventory-replay",
        "title": "Overlapping tenant event IDs break ordered shipment and partial-return replay",
        "app_file": "app/inventory.py",
        "exception_type": "InventoryError",
        "baseline_count": 8,
        "acceptance_count": 3,
        "call": "from app.inventory import reconcile_inventory\n"
                f"reconcile_inventory(**{INVENTORY_REQUEST!r})\n",
        "request": INVENTORY_REQUEST,
        "good_replacements": INVENTORY_FIX,
        "reproducer": "from app.inventory import reconcile_inventory\n\n"
                      "def test_overlapping_tenant_ids_and_identical_retry():\n"
                      f"    assert reconcile_inventory(**{INVENTORY_REQUEST!r}) == {INVENTORY_EXPECTED!r}\n",
        "acceptance": dedent('''\
            from copy import deepcopy
            import pytest
            from app.inventory import InventoryError, reconcile_inventory

            def event(tenant, event_id, seq, kind, quantity, ref=None, sku="bolt"):
                record = dict(tenant=tenant, id=event_id, seq=seq, kind=kind, quantity=quantity, sku=sku)
                if ref is not None:
                    record["ref"] = ref
                return record

            def test_same_id_is_still_one_event_within_each_tenant():
                events = [event("a", "in", 1, "receive", 4), event("b", "in", 1, "receive", 7),
                          event("a", "s", 2, "ship", 3), event("b", "s", 2, "ship", 5),
                          event("a", "r1", 3, "return", 1, "s"), event("a", "r2", 4, "return", 2, "s"),
                          event("b", "r1", 3, "return", 2, "s"), event("b", "r2", 4, "return", 1, "s")]
                events += [dict(events[0]), dict(events[-1]), dict(events[2])]
                events.reverse()
                original = deepcopy(events)
                assert reconcile_inventory({}, events) == {"a": {"bolt": 4}, "b": {"bolt": 5}}
                assert events == original

            def test_conflicting_retry_cannot_become_a_second_business_event():
                with pytest.raises(InventoryError, match="Conflicting duplicate"):
                    reconcile_inventory({"a": {"bolt": 10}}, [
                        event("a", "in", 1, "receive", 2), event("a", "in", 2, "receive", 3)])

            def test_returns_remain_scoped_to_original_tenant_sku_and_remaining_quantity():
                opening = {"a": {"bolt": 10}, "b": {"bolt": 10}}
                shipments = [event("a", "s", 1, "ship", 4), event("b", "s", 1, "ship", 2)]
                assert reconcile_inventory(opening, shipments + [
                    event("a", "r", 2, "return", 3, "s"), event("b", "r", 2, "return", 1, "s")
                ]) == {"a": {"bolt": 9}, "b": {"bolt": 9}}
                for invalid in [event("b", "r", 2, "return", 3, "s"),
                                event("a", "r", 2, "return", 1, "s", sku="nut"),
                                event("c", "r", 2, "return", 1, "s")]:
                    with pytest.raises(InventoryError):
                        reconcile_inventory(opening, shipments + [invalid])
            '''),
        "bad_replacements": [
            {"name": "deduplicate-entire-payload", "replacements": [[
                "        key = event_id", "        key = tuple(sorted(event.items()))",
            ]]},
            {"name": "share-return-balances-between-tenants", "replacements": INVENTORY_FIX + [
                ["shipments[(tenant, event_id)]", "shipments[event_id]"],
                ['shipments.get((tenant, event.get("ref")))', 'shipments.get(event.get("ref"))'],
            ]},
        ],
    },
]
