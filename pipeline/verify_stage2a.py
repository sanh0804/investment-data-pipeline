"""Read-only checks of live coverage and the stored synthetic dataset."""
import argparse
import json
from datetime import timedelta

from pipeline.config import PRODUCTS
from pipeline.db import connect
from pipeline.synthetic import business_hash, customer_rows, load_prices, trade_rows


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def verify(conn, dataset_id):
    meta = conn.execute(
        """SELECT start_date,end_date,customer_count,trade_count,prices_sha256,content_sha256
           FROM synthetic.datasets WHERE dataset_id=%s""", (dataset_id,),
    ).fetchone()
    require(meta is not None, "Synthetic dataset not found")
    start, end, nc, nt, price_hash, content_hash = meta
    prices, current_hash = load_prices(conn, start, end)
    require(current_hash == price_hash, "Live reference prices changed since generation")
    customers, trades = customer_rows(conn, dataset_id), trade_rows(conn, dataset_id)
    require(len(customers) == nc == 1000, "Expected 1000 customers")
    require(len(trades) == nt == 50000, "Expected 50000 matched trades")
    require(business_hash(customers, trades) == content_hash, "Synthetic business content checksum mismatch")
    require(len({r[0] for r in customers}) == nc and len({r[0] for r in trades}) == nt, "Duplicate stable IDs")
    violations = conn.execute(
        """SELECT
          count(*) FILTER (WHERE c.customer_id IS NULL),
          count(*) FILTER (WHERE p.product_id IS NULL),
          count(*) FILTER (WHERE f.fetch_id IS NULL OR f.http_status<>200 OR f.product_id<>t.product_id),
          count(*) FILTER (WHERE t.traded_at <= c.created_at),
          count(*) FILTER (WHERE t.quantity<=0 OR t.execution_price<=0 OR t.fee_usd<0 OR
             t.side NOT IN ('BUY','SELL') OR t.status<>'MATCHED' OR
             t.execution_price<>round(t.reference_close*(1+t.slippage_bps::numeric/10000),2) OR
             t.notional_usd<>round(t.quantity*t.execution_price,2) OR
             t.fee_usd<>round(t.notional_usd*0.001,2) OR t.reference_close<>p.close_price)
          FROM synthetic.trades t
          LEFT JOIN synthetic.customers c ON c.dataset_id=t.dataset_id AND c.customer_id=t.customer_id
          LEFT JOIN market.daily_prices p ON p.source=t.source AND p.product_id=t.product_id AND p.price_date=t.price_date
          LEFT JOIN raw.price_fetches f ON f.fetch_id=t.reference_fetch_id
          WHERE t.dataset_id=%s""", (dataset_id,),
    ).fetchone()
    names = ("orphan_customer", "orphan_price", "invalid_raw_lineage", "trade_before_customer", "invalid_money")
    checks = dict(zip(names, violations))
    require(all(n == 0 for n in violations), f"Violations: {checks}")
    activity = {}
    for row in trades:
        require(start <= row[4].date() < end and row[4].date() == row[7], "Trade outside date range")
        activity.setdefault(row[3], []).append(row[4].date())
    profiles = {"never": 0, "regular": 0, "ceased": 0}
    max_regular_gap = 0
    for customer in customers:
        cid, _, _, _, profile = customer
        profiles[profile] += 1
        days = sorted(set(activity.get(cid, [])))
        if profile == "never":
            require(not days, "Never-traded customer has trades")
        elif profile == "ceased":
            require(bool(days) and days[-1] < end - timedelta(days=60), "Ceased customer has recent trades")
        else:
            require(days[0] == start and days[-1] == end - timedelta(days=1), "Regular customer's range is incomplete")
            gap = max((b-a).days for a,b in zip(days, days[1:]))
            max_regular_gap = max(max_regular_gap, gap)
            require(gap <= 4, "Regular customer has a gap >4 days in this 180-day dataset")
    require(profiles == {"never":100,"regular":600,"ceased":300}, "Incorrect profile counts")
    totals = conn.execute(
        "SELECT sum(notional_usd),sum(fee_usd),count(DISTINCT customer_id) FROM synthetic.trades WHERE dataset_id=%s",
        (dataset_id,),
    ).fetchone()
    return {"passed":True,"dataset_id":dataset_id,"start":str(start),"end_exclusive":str(end),
            "live_prices_by_product":{p:sum(key[0]==p for key in prices) for p in PRODUCTS},
            "customers":nc,"trades":nt,"profiles":profiles,"violations":checks,
            "max_regular_gap_days":max_regular_gap,"notional_usd":str(totals[0]),
            "fee_usd":str(totals[1]),"trading_customers":totals[2],"content_sha256":content_hash,
            "prices_sha256":price_hash}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", default="portfolio-v1")
    args = parser.parse_args()
    with connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        print(json.dumps(verify(conn,args.dataset_id), indent=2))


if __name__ == "__main__":
    main()
