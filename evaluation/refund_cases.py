"""Trusted cumulative-refund fixtures and literal oracles; exclude from model input."""
from textwrap import dedent


REQUEST = {
    "sales": [{"id": "s", "currency": "USD", "units": 5,
               "weights": {"merchant": 3, "platform": 2}}],
    "refunds": [
        {"id": "r1", "sale": "s", "currency": "USD", "units": 2},
        {"id": "r1", "sale": "s", "currency": "USD", "units": 2},
        {"id": "r2", "sale": "s", "currency": "USD", "units": 1},
        {"id": "r3", "sale": "s", "currency": "USD", "units": 2},
    ],
}
EXPECTED = [
    {"currency": "USD", "sale": "s", "refund": "r1", "account": "merchant", "units": 1},
    {"currency": "USD", "sale": "s", "refund": "r1", "account": "platform", "units": 1},
    {"currency": "USD", "sale": "s", "refund": "r2", "account": "merchant", "units": 0},
    {"currency": "USD", "sale": "s", "refund": "r2", "account": "platform", "units": 1},
    {"currency": "USD", "sale": "s", "refund": "r3", "account": "merchant", "units": 2},
    {"currency": "USD", "sale": "s", "refund": "r3", "account": "platform", "units": 0},
]

BALANCE_BLOCK = '''        total, allocation = book[key]
        previous = refunded[key]
        cumulative = previous + amount
        if cumulative > total:
            raise RefundError("Refund exceeds the original sale total")
        refunded[key] = cumulative
'''
REPLAY_BLOCK = '''        replay_key = (*key, refund_id)
        if replay_key in seen:
            if seen[replay_key] != refund:
                raise RefundError("Conflicting refund retry")
            continue
        seen[replay_key] = dict(refund)
'''
ROUNDING_LINE = '        merchant = amount * allocation["merchant"] // total'
REPLAY_FIX = [BALANCE_BLOCK + REPLAY_BLOCK, REPLAY_BLOCK + BALANCE_BLOCK]
ROUNDING_FIX = [ROUNDING_LINE,
               '        merchant = (cumulative * allocation["merchant"] // total -\n'
               '                    previous * allocation["merchant"] // total)']

ACCEPTANCE = dedent('''\
    from copy import deepcopy
    import pytest
    from app.refunds import RefundError, refund_ledger

    def sale(units, merchant, platform, currency="USD", identifier="s"):
        return dict(id=identifier, currency=currency, units=units,
                    weights={"merchant": merchant, "platform": platform})

    def event(identifier, units, currency="USD", sale_id="s"):
        return dict(id=identifier, units=units, currency=currency, sale=sale_id)

    def rows(identifier, merchant, platform, currency="USD", sale_id="s"):
        return [{"currency": currency, "sale": sale_id, "refund": identifier,
                 "account": "merchant", "units": merchant},
                {"currency": currency, "sale": sale_id, "refund": identifier,
                 "account": "platform", "units": platform}]

    def test_fractional_carry_changes_the_recipient_at_several_partial_boundaries():
        assert refund_ledger([sale(8, 3, 1)], [
            event("a", 1), event("b", 1), event("c", 2), event("d", 1), event("e", 3),
        ]) == (rows("a", 0, 1) + rows("b", 1, 0) + rows("c", 2, 0) +
               rows("d", 0, 1) + rows("e", 3, 0))

    def test_replays_before_later_refunds_and_after_full_refund_are_noops():
        first, middle, last = event("a", 1), event("b", 1), event("c", 3)
        expected = rows("a", 0, 1) + rows("b", 1, 0) + rows("c", 2, 1)
        for refunds in ([first, middle, last], [first, first, middle, first, last],
                        [first, middle, last, middle, first, last]):
            before = deepcopy(refunds)
            assert refund_ledger([sale(5, 3, 2)], refunds) == expected
            assert refunds == before

    def test_identical_sale_and_refund_ids_stay_isolated_across_currencies():
        sales = [sale(5, 3, 2), sale(3, 1, 2, "JPY"), sale(7, 2, 3, "KWD", "other")]
        refunds = [event("a", 2), event("a", 1, "JPY"), event("a", 3, "KWD", "other"),
                   event("b", 3), event("b", 2, "JPY"), event("b", 4, "KWD", "other")]
        expected = (rows("a", 1, 1) + rows("a", 0, 1, "JPY") +
                    rows("a", 0, 3, "KWD", "other") + rows("b", 2, 1) +
                    rows("b", 1, 1, "JPY") + rows("b", 2, 2, "KWD", "other"))
        before = deepcopy((sales, refunds))
        assert refund_ledger(sales, refunds) == expected
        assert refund_ledger(list(reversed(sales)), refunds) == expected
        assert (sales, refunds) == before

    def test_retries_do_not_disable_overrefund_or_conflict_rejection():
        full = event("full", 5)
        for refunds in ([full, full, event("extra", 1)],
                        [full, event("full", 4)],
                        [event("a", 2), event("a", 3)]):
            with pytest.raises(RefundError):
                refund_ledger([sale(5, 3, 2)], refunds)

    @pytest.mark.parametrize("units, merchant, platform, cumulative_merchant", [
        (4, 3, 1, (0, 0, 1, 2, 3)),
        (5, 3, 2, (0, 0, 1, 1, 2, 3)),
        (7, 2, 3, (0, 0, 0, 0, 1, 1, 1, 2)),
    ])
    def test_every_small_partition_preserves_literal_prefix_balances(units, merchant, platform, cumulative_merchant):
        # Every composition of each small total is checked against precomputed balances.
        for mask in range(1 << (units - 1)):
            cuts = [0] + [i for i in range(1, units) if mask & (1 << (i - 1))] + [units]
            events = [event(str(i), right - left) for i, (left, right) in enumerate(zip(cuts, cuts[1:]))]
            ledger = refund_ledger([sale(units, merchant, platform)], events)
            assert len(ledger) == 2 * len(events)
            paid = 0
            for i, (request, right) in enumerate(zip(events, cuts[1:])):
                merchant_row, platform_row = ledger[2 * i:2 * i + 2]
                delta = cumulative_merchant[right] - paid
                assert [merchant_row, platform_row] == rows(request["id"], delta, request["units"] - delta)
                assert merchant_row["units"] >= 0 and platform_row["units"] >= 0
                paid = cumulative_merchant[right]
            repeated = [copy for request in events for copy in (request, dict(request))]
            assert refund_ledger([sale(units, merchant, platform)], repeated) == ledger
''')

CASES = [{
    "slug": "cumulative-refunds",
    "title": "Retried partial refunds exhaust recipient balances before a sale is fully reversed",
    "app_file": "app/refunds.py",
    "exception_type": "RefundError",
    "baseline_count": 13,
    "acceptance_count": 7,
    "call": f"from app.refunds import refund_ledger\nrefund_ledger(**{REQUEST!r})\n",
    "request": REQUEST,
    "good_replacements": [REPLAY_FIX, ROUNDING_FIX],
    "reproducer": "from app.refunds import refund_ledger\n\n"
                  "def test_replayed_partial_refunds_preserve_the_original_sale_split():\n"
                  f"    assert refund_ledger(**{REQUEST!r}) == {EXPECTED!r}\n",
    "acceptance": ACCEPTANCE,
    "bad_replacements": [
        {"name": "repair-retries-only", "replacements": [REPLAY_FIX]},
        {"name": "repair-cumulative-allocation-only", "replacements": [ROUNDING_FIX]},
        {"name": "repair-only-the-final-refund", "replacements": [REPLAY_FIX, [
            ROUNDING_LINE, ROUNDING_LINE + '\n        if cumulative == total:\n'
            '            merchant = allocation["merchant"] - returned[key]["merchant"]',
        ]]},
        {"name": "deduplicate-by-refund-id-without-sale-scope", "replacements": [
            REPLAY_FIX, ROUNDING_FIX,
            ['        replay_key = (*key, refund_id)', '        replay_key = refund_id'],
        ]},
    ],
}]
