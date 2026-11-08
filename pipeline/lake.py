"""Copy raw Coinbase responses to content-addressed objects plus a Delta manifest.

raw.price_fetches in PostgreSQL stays the system of record; this module only copies.
Objects are keyed by the SHA-256 of their bytes, so repeated identical responses are
stored once. The manifest keeps one row per HTTP attempt, including attempts without
a body (transport errors), and points to the object that holds the bytes.

Read Delta tables on S3 only through QueryBuilder: with deltalake 1.6.6 and RustFS
1.0.3, to_pyarrow_table()/to_pyarrow_dataset() returned data but the process hung at
interpreter exit (docs/verification.md, stage 5A).
"""
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import boto3
import pyarrow as pa
from botocore.exceptions import ClientError
from deltalake import DeltaTable, QueryBuilder, write_deltalake
from dotenv import load_dotenv
from psycopg.rows import dict_row

from pipeline.config import ROOT

BRONZE_PREFIX = "bronze/coinbase/"
MANIFEST_PATH = "manifest/coinbase_price_fetches"
UTC_MICROS = pa.timestamp("us", tz="UTC")

MANIFEST_SCHEMA = pa.schema([
    pa.field("fetch_id", pa.string(), nullable=False),
    pa.field("run_id", pa.string(), nullable=False),
    pa.field("attempt_number", pa.int32(), nullable=False),
    pa.field("source", pa.string(), nullable=False),
    pa.field("product_id", pa.string(), nullable=False),
    pa.field("endpoint", pa.string(), nullable=False),
    pa.field("request_params", pa.string(), nullable=False),  # canonical JSON
    pa.field("started_at", UTC_MICROS, nullable=False),
    pa.field("fetched_at", UTC_MICROS, nullable=False),
    pa.field("http_status", pa.int32()),
    pa.field("response_headers", pa.string(), nullable=False),  # canonical JSON
    pa.field("response_sha256", pa.string()),
    pa.field("body_bytes", pa.int64()),
    pa.field("object_key", pa.string()),
    pa.field("transport_error", pa.string()),
    pa.field("exported_at", UTC_MICROS, nullable=False),
])
# Delta's own type names for Arrow types used here; used to detect a drifted table.
DELTA_TYPES = {pa.string(): "string", pa.int32(): "integer", pa.int64(): "long",
               pa.date32(): "date", UTC_MICROS: "timestamp"}

RAW_QUERY = """
    SELECT fetch_id, run_id, attempt_number, source, product_id, endpoint,
           request_params, started_at, fetched_at, http_status, response_headers,
           response_body, response_sha256, transport_error
    FROM raw.price_fetches
    ORDER BY fetched_at, fetch_id
"""


@dataclass(frozen=True)
class LakeSettings:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"

    @property
    def manifest_uri(self):
        return f"s3://{self.bucket}/{MANIFEST_PATH}"

    def storage_options(self):
        options = {"AWS_ENDPOINT_URL": self.endpoint, "AWS_ACCESS_KEY_ID": self.access_key,
                   "AWS_SECRET_ACCESS_KEY": self.secret_key, "AWS_REGION": self.region}
        if self.endpoint.startswith("http://"):
            options["AWS_ALLOW_HTTP"] = "true"  # local storage only, bound to 127.0.0.1
        return options


def load_lake_settings():
    load_dotenv(ROOT / ".env")
    port = os.getenv("LAKE_PORT", "59000")
    return LakeSettings(
        endpoint=os.getenv("LAKE_ENDPOINT", f"http://127.0.0.1:{port}"),
        access_key=os.environ["LAKE_ACCESS_KEY"],
        secret_key=os.environ["LAKE_SECRET_KEY"],
        bucket=os.getenv("LAKE_BUCKET", "investment-lake"),
    )


def s3_client(settings):
    return boto3.client(
        "s3", endpoint_url=settings.endpoint, region_name=settings.region,
        aws_access_key_id=settings.access_key, aws_secret_access_key=settings.secret_key,
        config=boto3.session.Config(connect_timeout=5, read_timeout=30,
                                    retries={"max_attempts": 3, "mode": "standard"}),
    )


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def object_key(sha256):
    return BRONZE_PREFIX + sha256


def manifest_row(raw, exported_at):
    """Validate one raw attempt and describe it for the manifest.

    A body whose hash differs from response_sha256 means the stored raw row is
    inconsistent; stop instead of copying it under either hash.
    """
    body = raw["response_body"]
    if body is None:
        if raw["response_sha256"] is not None:
            raise ValueError(f"fetch {raw['fetch_id']}: hash recorded without a body")
        key = size = None
    else:
        body = bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        if digest != raw["response_sha256"]:
            raise ValueError(f"fetch {raw['fetch_id']}: body hash {digest} != recorded hash")
        key, size = object_key(digest), len(body)
    return {
        "fetch_id": str(raw["fetch_id"]), "run_id": str(raw["run_id"]),
        "attempt_number": raw["attempt_number"], "source": raw["source"],
        "product_id": raw["product_id"], "endpoint": raw["endpoint"],
        "request_params": canonical_json(raw["request_params"]),
        "started_at": raw["started_at"], "fetched_at": raw["fetched_at"],
        "http_status": raw["http_status"],
        "response_headers": canonical_json(raw["response_headers"]),
        "response_sha256": raw["response_sha256"], "body_bytes": size,
        "object_key": key, "transport_error": raw["transport_error"],
        "exported_at": exported_at,
    }


def manifest_table(rows):
    return arrow_table(rows, MANIFEST_SCHEMA)


def arrow_table(rows, schema):
    # pyarrow rejects Decimal values that would lose digits (scale/precision) here.
    table = pa.Table.from_pylist(rows, schema=schema)
    check_schema(table.schema, schema)
    return table


def check_schema(schema, expected=MANIFEST_SCHEMA):
    # Delta casts compatible types on write (a float becomes a rounded DECIMAL), so
    # require the exact Arrow schema rather than relying on Delta's checks.
    if not schema.equals(expected):
        raise ValueError(f"schema mismatch:\n{schema}\nexpected:\n{expected}")


def delta_type(arrow_type):
    if pa.types.is_decimal(arrow_type):
        return f"decimal({arrow_type.precision},{arrow_type.scale})"
    return DELTA_TYPES[arrow_type]


def check_table_schema(table, expected=MANIFEST_SCHEMA):
    actual = [(f.name, str(f.type), f.nullable) for f in table.schema().fields]
    wanted = [(f.name, f'PrimitiveType("{delta_type(f.type)}")', f.nullable) for f in expected]
    if actual != wanted:
        raise ValueError(f"Delta table schema drifted: {actual}")


def read_object(s3, bucket, key):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if hashlib.sha256(body).hexdigest() != key.removeprefix(BRONZE_PREFIX):
        raise ValueError(f"object {key} does not match its hash")
    return body


def open_table(uri, storage_options, schema):
    if not DeltaTable.is_deltatable(uri, storage_options=storage_options):
        return None
    table = DeltaTable(uri, storage_options=storage_options)
    check_table_schema(table, schema)
    return table


def upsert_changed(uri, storage_options, schema, key, rows):
    """Write only rows that are new or differ from the stored row with the same key.

    Comparing first avoids empty Delta versions on reruns; MERGE then applies the
    difference atomically. Rows absent from the input are left untouched.
    """
    table = open_table(uri, storage_options, schema)
    stored = {}
    if table is not None:
        stored = {tuple(r[k] for k in key): r for r in query(table, "SELECT * FROM t").to_pylist()}
    # Decimal and aware-datetime equality compare values, so a stored 1.500000 (scale
    # 18) equals an input 1.5 and a ZoneInfo("UTC") timestamp equals timezone.utc.
    changed = [r for r in rows if stored.get(tuple(r[k] for k in key)) != r]
    if not changed:
        return 0, table.version() if table else None
    data = arrow_table(changed, schema)
    if table is None:
        write_deltalake(uri, data, mode="error", storage_options=storage_options)
    else:
        condition = " AND ".join(f"t.{k} = s.{k}" for k in key)
        (table.merge(data, condition, source_alias="s", target_alias="t")
         .when_matched_update_all().when_not_matched_insert_all().execute())
    return len(changed), DeltaTable(uri, storage_options=storage_options).version()


def ensure_bucket(s3, bucket):
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket", "NotFound"):
            raise
        s3.create_bucket(Bucket=bucket)


def put_object_once(s3, bucket, key, body):
    """Upload unless the key exists; an existing object must hold identical bytes."""
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch="*")
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("PreconditionFailed", "412"):
            raise
    existing = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if hashlib.sha256(existing).hexdigest() != key.removeprefix(BRONZE_PREFIX):
        raise ValueError(f"object {key} exists with different content")
    return False


def query(table, sql):
    return pa.table(QueryBuilder().register("t", table).execute(sql).read_all())


def existing_fetch_ids(uri, storage_options):
    table = open_table(uri, storage_options, MANIFEST_SCHEMA)
    if table is None:
        return set(), None
    return set(query(table, "SELECT fetch_id FROM t").column("fetch_id").to_pylist()), table


def append_manifest(uri, storage_options, rows):
    """Insert rows whose fetch_id is new; existing rows are never updated.

    Skipping the commit when nothing is new keeps reruns from creating empty Delta
    versions. MERGE (not append) still guards the key if another run raced us.
    """
    known, table = existing_fetch_ids(uri, storage_options)
    new_rows = [row for row in rows if row["fetch_id"] not in known]
    if not new_rows:
        return 0, table.version() if table else None
    data = manifest_table(new_rows)
    if table is None:
        write_deltalake(uri, data, mode="error", storage_options=storage_options)
    else:
        (table.merge(data, "t.fetch_id = s.fetch_id", source_alias="s", target_alias="t")
         .when_not_matched_insert_all().execute())
    return len(new_rows), DeltaTable(uri, storage_options=storage_options).version()


def export(conn, s3, settings, exported_at=None):
    exported_at = exported_at or datetime.now(timezone.utc)
    with conn.cursor(row_factory=dict_row) as cursor:
        raws = cursor.execute(RAW_QUERY).fetchall()
    rows =[manifest_row(raw, exported_at) for raw in raws]  # validate all before writing

    # Objects first: a committed manifest row must never point to a missing object.
    uploaded = present = 0
    seen = set()
    for raw, row in zip(raws, rows):
        if row["object_key"] is None or row["object_key"] in seen:
            continue
        seen.add(row["object_key"])
        if put_object_once(s3, settings.bucket, row["object_key"], bytes(raw["response_body"])):
            uploaded += 1
        else:
            present += 1
    inserted, version = append_manifest(settings.manifest_uri, settings.storage_options(), rows)
    return {"raw_rows": len(rows), "objects_uploaded": uploaded, "objects_present": present,
            "manifest_rows_inserted": inserted, "manifest_version": version}


def main():
    from pipeline.db import connect

    settings = load_lake_settings()
    s3 = s3_client(settings)
    ensure_bucket(s3, settings.bucket)
    with connect() as conn:
        print(json.dumps(export(conn, s3, settings)))


if __name__ == "__main__":
    main()
