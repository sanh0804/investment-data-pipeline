import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pipeline.synthetic import amounts

FIXTURE = Path(__file__).parent / "fixtures/reconciliation"


def read_csv(name):
    with (FIXTURE / name).open() as handle:
        return list(csv.DictReader(handle))


def check_total(rows, expected):
    assert len(rows) == expected["trades"]
    for column in ("notional_usd", "fee_usd", "quantity"):
        if column in expected:
            assert sum((Decimal(r[column]) for r in rows), Decimal(0)) == Decimal(expected[column])


def test_fixture_arithmetic_and_independent_expected_totals():
    customers = read_csv("customers.csv")
    trades = read_csv("trades.csv")
    prices = read_csv("daily_prices.csv")
    expected = json.loads((FIXTURE / "expected_totals.json").read_text())
    assert len(customers) == expected["customers"]
    assert len({c["customer_id"] for c in customers}) == len(customers)
    assert len({t["trade_id"] for t in trades}) == len(trades)
    lookup = {c["customer_id"]: c for c in customers}
    price_lookup = {(p["source"], p["product_id"], p["price_date"]): Decimal(p["close_price"]) for p in prices}
    for trade in trades:
        assert trade["source"] == "fixture"
        assert trade["side"] in {"BUY", "SELL"} and trade["status"] == "MATCHED"
        assert datetime.fromisoformat(trade["traded_at"]) > datetime.fromisoformat(lookup[trade["customer_id"]]["created_at"])
        assert trade["traded_at"][:10] == trade["price_date"]
        reference = price_lookup[(trade["source"], trade["product_id"], trade["price_date"])]
        quantity = Decimal(trade["quantity"])
        assert quantity > 0
        calculated = amounts(quantity, reference, int(trade["slippage_bps"]))
        assert calculated == tuple(Decimal(trade[c]) for c in ("execution_price", "notional_usd", "fee_usd"))
    assert len({t["customer_id"] for t in trades}) == expected["customers_with_trades"]
    assert len(customers) - len({t["customer_id"] for t in trades}) == expected["customers_without_trades"]
    check_total(trades, expected)
    for group, column in (("by_side", "side"), ("by_product", "product_id"),
                          ("by_customer", "customer_id"), ("by_date", "price_date")):
        for key, totals in expected[group].items():
            check_total([t for t in trades if t[column] == key], totals)
