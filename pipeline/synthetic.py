"""Generate seeded matched trades from a fixed daily-close snapshot."""
import argparse
import hashlib
import json
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import NAMESPACE_URL, uuid5

from pipeline.config import PRODUCTS, SOURCE
from pipeline.db import connect

VERSION = "synthetic-v1"
CENT = Decimal("0.01")
FEE_RATE = Decimal("0.001")
DEFAULT_START = date(2026, 5, 1)
DEFAULT_END = date(2026, 10, 28)
DEFAULT_SEED = 20261101


def fingerprint(rows):
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def amounts(quantity, reference_close, slippage_bps):
    execution = (reference_close * (1 + Decimal(slippage_bps) / 10000)).quantize(CENT, rounding=ROUND_HALF_UP)
    notional = (quantity * execution).quantize(CENT, rounding=ROUND_HALF_UP)
    fee = (notional * FEE_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
    return execution, notional, fee


@dataclass(frozen=True)
class Customer:
    customer_id: object
    dataset_id: str
    number: int
    created_at: datetime
    profile: str

    def row(self):
        return (self.customer_id, self.dataset_id, self.number, self.created_at, self.profile)


@dataclass(frozen=True)
class Trade:
    trade_id: object
    dataset_id: str
    number: int
    customer_id: object
    traded_at: datetime
    product: str
    quantity: Decimal
    reference_close: Decimal
    slippage_bps: int
    execution_price: Decimal
    notional: Decimal
    fee: Decimal
    side: str
    reference_fetch_id: object

    def row(self):
        return (self.trade_id, self.dataset_id, self.number, self.customer_id, self.traded_at,
                SOURCE, self.product, self.traded_at.date(), self.side, "MATCHED",
                self.quantity, self.reference_close, self.slippage_bps, self.execution_price,
                self.notional, self.fee, self.reference_fetch_id)


def load_prices(conn, start, end):
    rows = conn.execute(
        """SELECT product_id, price_date, close_price, last_fetch_id
           FROM market.daily_prices WHERE source=%s AND product_id=ANY(%s)
             AND price_date >= %s AND price_date < %s ORDER BY product_id, price_date""",
        (SOURCE, list(PRODUCTS), start, end),
    ).fetchall()
    prices = {(product, day): (close, fetch_id) for product, day, close, fetch_id in rows}
    expected = {(product, start + timedelta(days=i)) for product in PRODUCTS for i in range((end-start).days)}
    missing = sorted(expected - set(prices))
    if missing:
        raise ValueError(f"Missing {len(missing)} live daily prices; first missing keys: {missing[:5]}")
    # Ignore fetch metadata: a new fetch of identical business values is the same price input.
    price_hash = fingerprint((product, day, close) for product, day, close, _ in rows)
    return prices, price_hash


def generate(dataset_id, seed, start, end, prices, customer_count=1000, trade_count=50000):
    days = (end - start).days
    if days < 90 or end > datetime.now(timezone.utc).date():
        raise ValueError("Synthetic range must contain >=90 completed UTC days")
    if customer_count < 10 or trade_count < customer_count * 2:
        raise ValueError("Need >=10 customers and enough trades to cover activity profiles")
    namespace = uuid5(NAMESPACE_URL, f"investment-pipeline/{VERSION}/{dataset_id}")
    rng = random.Random(seed)
    never = customer_count // 10
    regular = customer_count * 6 // 10
    midnight = datetime.combine(start, datetime.min.time(), timezone.utc)
    customers = []
    for i in range(1, customer_count + 1):
        profile = "never" if i <= never else "regular" if i <= never + regular else "ceased"
        created = midnight - timedelta(days=rng.randint(1, 30))
        customers.append(Customer(uuid5(namespace, f"customer/{i}"), dataset_id, i, created, profile))
    active = customers[never:]
    trades = []
    for index in range(trade_count):
        customer = active[index % len(active)]
        ordinal = index // len(active)
        n = trade_count // len(active) + (index % len(active) < trade_count % len(active))
        # Regular: spread evenly over the full range. Ceased: no trades in last 60 days.
        span = days if customer.profile == "regular" else days - 60
        day_offset = ordinal * (span - 1) // (n - 1)
        traded_at = midnight + timedelta(days=day_offset, seconds=rng.randint(1, 86399))
        product = PRODUCTS[index % len(PRODUCTS)]
        reference, fetch_id = prices[(product, traded_at.date())]
        quantity = (Decimal(rng.randint(1000, 1000000)) / 1000000).quantize(Decimal("0.000001"))
        slippage = rng.randint(-25, 25)
        execution, notional, fee = amounts(quantity, reference, slippage)
        side = "BUY" if ordinal % 2 == 0 else "SELL"
        trades.append(Trade(uuid5(namespace, f"trade/{index+1}"), dataset_id, index+1,
                            customer.customer_id, traded_at, product, quantity, reference,
                            slippage, execution, notional, fee, side, fetch_id))
    return customers, trades


def customer_rows(conn, dataset_id):
    return conn.execute(
        """SELECT customer_id, dataset_id, customer_number, created_at, activity_profile
           FROM synthetic.customers WHERE dataset_id=%s ORDER BY customer_number""", (dataset_id,),
    ).fetchall()


def trade_rows(conn, dataset_id):
    return conn.execute(
        """SELECT trade_id, dataset_id, trade_number, customer_id, traded_at, source,
                  product_id, price_date, side, status, quantity, reference_close,
                  slippage_bps, execution_price, notional_usd, fee_usd, reference_fetch_id
           FROM synthetic.trades WHERE dataset_id=%s ORDER BY trade_number""", (dataset_id,),
    ).fetchall()


def business_hash(customers, trades):
    # Stable if live fetch metadata changes; exact business inputs and values remain frozen.
    return fingerprint([fingerprint(customers), fingerprint(row[:-1] for row in trades)])


def load_dataset(conn, dataset_id, seed, start, end):
    # One transaction: dataset record, customers first, then trades and verification.
    # Lock this local generator against a concurrent rerun of the same dataset.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"synthetic/{dataset_id}",))
    prices, price_hash = load_prices(conn, start, end)
    customers, trades = generate(dataset_id, seed, start, end, prices)
    expected_customers = [c.row() for c in customers]
    expected_trades = [t.row() for t in trades]
    content_hash = business_hash(expected_customers, expected_trades)
    metadata = (VERSION, seed, start, end, len(customers), len(trades), price_hash, content_hash)
    existing = conn.execute(
        """SELECT generator_version, seed, start_date, end_date, customer_count, trade_count,
                  prices_sha256, content_sha256 FROM synthetic.datasets WHERE dataset_id=%s""",
        (dataset_id,),
    ).fetchone()
    if existing and existing != metadata:
        raise ValueError("Dataset exists with different inputs/content; use a new dataset_id, never overwrite")
    conn.execute(
        """INSERT INTO synthetic.datasets
           (dataset_id,generator_version,seed,start_date,end_date,customer_count,trade_count,prices_sha256,content_sha256)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dataset_id) DO NOTHING""",
        (dataset_id, *metadata),
    )
    with conn.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO synthetic.customers
               (customer_id,dataset_id,customer_number,created_at,activity_profile)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT (customer_id) DO NOTHING""", expected_customers,
        )
        cursor.executemany(
            """INSERT INTO synthetic.trades
               (trade_id,dataset_id,trade_number,customer_id,traded_at,source,product_id,price_date,
                side,status,quantity,reference_close,slippage_bps,execution_price,notional_usd,fee_usd,reference_fetch_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (trade_id) DO NOTHING""", expected_trades,
        )
    stored_customers = customer_rows(conn, dataset_id)
    stored_trades = trade_rows(conn, dataset_id)
    if len(stored_customers) != 1000 or len(stored_trades) != 50000 or business_hash(stored_customers, stored_trades) != content_hash:
        raise AssertionError("Stored synthetic dataset differs from deterministic output")
    return {"dataset_id": dataset_id, "origin": "synthetic", "customers": len(stored_customers),
            "matched_trades": len(stored_trades), "seed": seed, "start": str(start),
            "end_exclusive": str(end), "content_sha256": content_hash, "prices_sha256": price_hash,
            "rerun": existing is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", default="portfolio-v1")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    args = parser.parse_args()
    with connect() as conn:
        report = load_dataset(conn, args.dataset_id, args.seed, args.start, args.end)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
