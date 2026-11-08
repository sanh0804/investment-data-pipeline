"""Publish dbt mart snapshots to Delta, then reproduce and diff past publications.

dbt rebuilds analytics tables in place, so an earlier report disappears. Publishing
copies one dataset's marts into gold Delta tables (replacing that dataset's rows, one
table version per publish) and appends a row to a publications log that records
which versions hold the report. Metrics are never recomputed here; the copy is
checked against PostgreSQL by the verification scripts.

Delta has no multi-table transaction: gold tables are written first, the log last.
A crash in between leaves gold ahead of the log, which verify detects because the
current gold versions are not listed. Time travel also relies on not running VACUUM
below the retention window; the log keeps totals so a lost version is detectable.
"""
import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from uuid import uuid4

import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from psycopg import sql
from psycopg.rows import dict_row

from pipeline.config import ROOT
from pipeline.lake import UTC_MICROS, arrow_table, load_lake_settings, open_table, query

MONEY = pa.decimal128(38, 2)  # mart totals are sums of cent-rounded amounts
DATASET_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def schema(*fields):
    return pa.schema([pa.field(n, t, nullable=null) for n, t, null in fields]
                     + [pa.field("publication_id", pa.string(), nullable=False)])


REPORTS = {
    "daily_trading": {
        "mart": "mart_daily_trading", "key": ("dataset_id", "trade_date"),
        "schema": schema(("dataset_id", pa.string(), False), ("trade_date", pa.date32(), False),
                         ("as_of_date", pa.date32(), False), ("trade_count", pa.int64(), False),
                         ("active_customers", pa.int64(), False),
                         ("trade_volume_usd", MONEY, False), ("fees_usd", MONEY, False)),
    },
    "customer_summary": {
        "mart": "mart_customer_summary", "key": ("dataset_id", "customer_id"),
        # Dates and recency are NULL for customers who never traded before the cutoff.
        "schema": schema(("dataset_id", pa.string(), False), ("customer_id", pa.string(), False),
                         ("customer_key", pa.string(), False), ("created_at", UTC_MICROS, False),
                         ("as_of_date", pa.date32(), False), ("first_trade_date", pa.date32(), True),
                         ("last_trade_date", pa.date32(), True),
                         ("days_since_last_trade", pa.int32(), True),
                         ("total_trades", pa.int64(), False), ("total_volume_usd", MONEY, False),
                         ("total_fee_usd", MONEY, False), ("activity_segment", pa.string(), False)),
    },
}
PUBLICATIONS_SCHEMA = pa.schema([
    pa.field("publication_id", pa.string(), nullable=False),
    pa.field("dataset_id", pa.string(), nullable=False),
    pa.field("as_of_date", pa.date32(), nullable=False),
    pa.field("source_schema", pa.string(), nullable=False),
    pa.field("git_sha", pa.string(), nullable=False),
    pa.field("published_at", UTC_MICROS, nullable=False),
    pa.field("daily_trading_version", pa.int64(), nullable=False),
    pa.field("customer_summary_version", pa.int64(), nullable=False),
    pa.field("daily_rows", pa.int64(), nullable=False),
    pa.field("customer_rows", pa.int64(), nullable=False),
    pa.field("trade_count", pa.int64(), nullable=False),
    pa.field("trade_volume_usd", MONEY, nullable=False),
    pa.field("fees_usd", MONEY, nullable=False),
    pa.field("note", pa.string()),
])


class Lake:
    """Locations of the gold tables under one root (bucket or test prefix)."""

    def __init__(self, root, storage_options):
        self.root, self.options = root.rstrip("/"), storage_options

    def uri(self, name):
        return f"{self.root}/gold/{name}"


def git_sha():
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.strip()
    return sha + ("-dirty" if dirty else "")


def read_mart(conn, source_schema, mart, dataset_id, columns):
    statement = sql.SQL("SELECT {} FROM {}.{} WHERE dataset_id = %s").format(
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.Identifier(source_schema), sql.Identifier(mart))
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(statement, (dataset_id,)).fetchall()
    for row in rows:
        if "customer_id" in row:
            row["customer_id"] = str(row["customer_id"])
    return rows


def content(rows, key):
    """Report rows by key, without publication_id. Decimal and aware datetimes compare
    by value, so PostgreSQL and Delta representations of the same row are equal."""
    return {tuple(r[k] for k in key): {c: v for c, v in r.items() if c != "publication_id"}
            for r in rows}


def report_rows(lake, name, dataset_id, version=None):
    spec = REPORTS[name]
    table = open_table(lake.uri(name), lake.options, spec["schema"])
    if table is None:
        return []
    if version is not None:
        table.load_as_version(version)
    return query(table, f"SELECT * FROM t WHERE dataset_id = '{dataset_id}'").to_pylist()


def publications(lake, dataset_id=None):
    table = open_table(lake.uri("publications"), lake.options, PUBLICATIONS_SCHEMA)
    if table is None:
        return []
    where = f" WHERE dataset_id = '{dataset_id}'" if dataset_id else ""
    # Every publish writes daily_trading, so its version is a strict publish order that,
    # unlike published_at, cannot tie or move with the clock.
    return query(table, f"SELECT * FROM t{where} ORDER BY daily_trading_version").to_pylist()


def publish(conn, lake, source_schema, dataset_id, sha=None, note=None, now=None):
    if not DATASET_ID.match(dataset_id):
        raise ValueError(f"unsafe dataset_id: {dataset_id!r}")
    new = {}
    for name, spec in REPORTS.items():
        columns = [f.name for f in spec["schema"] if f.name != "publication_id"]
        new[name] = read_mart(conn, source_schema, spec["mart"], dataset_id, columns)
    cutoffs = {r["as_of_date"] for rows in new.values() for r in rows}
    if len(cutoffs) != 1 or not new["customer_summary"]:
        raise ValueError(f"expected one as_of_date and customers for {dataset_id}, got {cutoffs}")

    if all(content(new[n], s["key"]) == content(report_rows(lake, n, dataset_id), s["key"])
           for n, s in REPORTS.items()):
        latest = publications(lake, dataset_id)
        return {"published": False, "reason": "unchanged since last publication",
                "publication_id": latest[-1]["publication_id"] if latest else None}

    publication_id = str(uuid4())
    versions = {}
    for name, spec in REPORTS.items():
        data = arrow_table([{**r, "publication_id": publication_id} for r in new[name]], spec["schema"])
        uri = lake.uri(name)
        if DeltaTable.is_deltatable(uri, storage_options=lake.options):
            # Replace only this dataset; Delta rejects rows outside the predicate.
            write_deltalake(uri, data, mode="overwrite", predicate=f"dataset_id = '{dataset_id}'",
                            storage_options=lake.options)
        else:
            write_deltalake(uri, data, mode="error", storage_options=lake.options)
        versions[name] = DeltaTable(uri, storage_options=lake.options).version()

    daily = new["daily_trading"]
    log = {"publication_id": publication_id, "dataset_id": dataset_id, "as_of_date": cutoffs.pop(),
           "source_schema": source_schema, "git_sha": sha or git_sha(),
           "published_at": now or datetime.now(timezone.utc),
           "daily_trading_version": versions["daily_trading"],
           "customer_summary_version": versions["customer_summary"],
           "daily_rows": len(daily), "customer_rows": len(new["customer_summary"]),
           "trade_count": sum(r["trade_count"] for r in daily),
           "trade_volume_usd": sum(r["trade_volume_usd"] for r in daily),
           "fees_usd": sum(r["fees_usd"] for r in daily), "note": note}
    write_deltalake(lake.uri("publications"), arrow_table([log], PUBLICATIONS_SCHEMA),
                    mode="append", storage_options=lake.options)
    return {"published": True, **log}


def publication(lake, publication_id):
    matches = [p for p in publications(lake) if p["publication_id"] == publication_id]
    if len(matches) != 1:
        raise ValueError(f"unknown publication {publication_id}")
    return matches[0]


def reproduce(lake, publication_id):
    """Read a publication's rows from the table versions recorded in the log."""
    pub = publication(lake, publication_id)
    rows = {name: report_rows(lake, name, pub["dataset_id"], pub[f"{name}_version"])
            for name in REPORTS}
    for name, data in rows.items():
        if {r["publication_id"] for r in data} != {publication_id}:
            raise ValueError(f"{name} version does not hold publication {publication_id}")
    return rows


def diff(lake, old_id, new_id):
    """Added, removed and changed rows (with old/new values) between two publications."""
    old, new = reproduce(lake, old_id), reproduce(lake, new_id)
    result = {}
    for name, spec in REPORTS.items():
        key = spec["key"]
        before = {tuple(r[k] for k in key): r for r in old[name]}
        after = {tuple(r[k] for k in key): r for r in new[name]}
        changed = []
        for k in sorted(before.keys() & after.keys()):
            cols = {c: [before[k][c], after[k][c]] for c in before[k]
                    if c != "publication_id" and before[k][c] != after[k][c]}
            if cols:
                changed.append({"key": list(k), "changes": cols})
        result[name] = {"added": [list(k) for k in sorted(after.keys() - before.keys())],
                        "removed": [list(k) for k in sorted(before.keys() - after.keys())],
                        "changed": changed}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pub = sub.add_parser("publish")
    pub.add_argument("--dataset-id", required=True)
    pub.add_argument("--schema", default="analytics")
    pub.add_argument("--note")
    sub.add_parser("list")
    d = sub.add_parser("diff")
    d.add_argument("old_publication_id")
    d.add_argument("new_publication_id")
    args = parser.parse_args()

    settings = load_lake_settings()
    lake = Lake(f"s3://{settings.bucket}", settings.storage_options())
    if args.command == "publish":
        from pipeline.db import connect
        with connect() as conn:
            result = publish(conn, lake, args.schema, args.dataset_id, note=args.note)
    elif args.command == "list":
        result = publications(lake)
    else:
        result = diff(lake, args.old_publication_id, args.new_publication_id)
    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
