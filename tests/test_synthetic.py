from collections import Counter
from datetime import timedelta
from decimal import Decimal

from pipeline.config import PRODUCTS
from pipeline.synthetic import DEFAULT_END, DEFAULT_START, amounts, generate


def test_money_half_up_and_fee_rounding():
    assert amounts(Decimal("0.5"), Decimal("110"), 0) == (Decimal("110.00"), Decimal("55.00"), Decimal("0.06"))
    assert amounts(Decimal("2.5"), Decimal("12"), 25) == (Decimal("12.03"), Decimal("30.08"), Decimal("0.03"))


def test_generator_ids_values_profiles_and_seed_are_reproducible():
    prices = {(product, DEFAULT_START + timedelta(days=i)): (Decimal("100"), None)
              for product in PRODUCTS for i in range(180)}
    args = ("unit-test-only", 42, DEFAULT_START, DEFAULT_END, prices)
    first = generate(*args, customer_count=10, trade_count=90)
    second = generate(*args, customer_count=10, trade_count=90)
    assert first == second
    customers, trades = first
    lookup = {c.customer_id: c for c in customers}
    assert Counter(c.profile for c in customers) == {"never": 1, "regular": 6, "ceased": 3}
    assert len({t.trade_id for t in trades}) == 90
    assert {t.side for t in trades} == {"BUY", "SELL"}
    assert {t.product for t in trades} == set(PRODUCTS)
    for customer in customers:
        activity = sorted(t.traded_at.date() for t in trades if t.customer_id == customer.customer_id)
        if customer.profile == "never":
            assert not activity
        elif customer.profile == "regular":
            assert activity[0] == DEFAULT_START and activity[-1] == DEFAULT_END - timedelta(days=1)
        else:
            assert activity[-1] < DEFAULT_END - timedelta(days=60)
    for trade in trades:
        assert lookup[trade.customer_id].created_at < trade.traded_at
        assert trade.quantity > 0 and trade.execution_price > 0 and trade.fee >= 0
    changed = generate("unit-test-only", 43, DEFAULT_START, DEFAULT_END, prices, customer_count=10, trade_count=90)
    assert [t.trade_id for t in changed[1]] == [t.trade_id for t in trades]
    assert changed[1] != trades
