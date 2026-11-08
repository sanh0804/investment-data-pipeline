"""Reconcile the Delta manifest and bronze objects with raw.price_fetches.

Read-only: compares every manifest field with PostgreSQL, checks that each object's
bytes hash to its key, and that the bucket holds exactly one object per distinct hash.
"""
import hashlib
import json
from pathlib import Path
import sys

from deltalake import DeltaTable
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.db import connect
from pipeline.lake import (BRONZE_PREFIX, RAW_QUERY, check_table_schema, load_lake_settings,
                           manifest_row, query, s3_client)


def check(value, message):
    if not value:
        raise AssertionError(message)


def bucket_keys(s3, bucket):
    keys = []
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=BRONZE_PREFIX):
        keys += [item['Key'] for item in page.get('Contents', [])]
    return keys


def main():
    settings = load_lake_settings()
    s3 = s3_client(settings)
    with connect() as conn, conn.cursor(row_factory=dict_row) as cursor:
        raws = cursor.execute(RAW_QUERY).fetchall()
    expected = {row['fetch_id']: row for row in (manifest_row(raw, None) for raw in raws)}

    table = DeltaTable(settings.manifest_uri, storage_options=settings.storage_options())
    check_table_schema(table)
    counts = query(table, 'SELECT count(*) AS n, count(DISTINCT fetch_id) AS d FROM t').to_pylist()[0]
    check(counts['n'] == counts['d'], 'Duplicate fetch_id in manifest')
    actual = {row['fetch_id']: row for row in query(table, 'SELECT * FROM t').to_pylist()}
    check(actual.keys() == expected.keys(),
          f'fetch_id sets differ: missing={len(expected.keys() - actual.keys())} '
          f'extra={len(actual.keys() - expected.keys())}')
    for fetch_id, row in expected.items():
        stored = {k: v for k, v in actual[fetch_id].items() if k != 'exported_at'}
        check(stored == {k: v for k, v in row.items() if k != 'exported_at'},
              f'Manifest row differs from PostgreSQL: {fetch_id}')

    hashes = {row['response_sha256'] for row in expected.values() if row['response_sha256']}
    keys = bucket_keys(s3, settings.bucket)
    check(sorted(keys) == sorted(BRONZE_PREFIX + h for h in hashes),
          f'Bucket objects {len(keys)} != distinct hashes {len(hashes)}')
    for key in keys:
        body = s3.get_object(Bucket=settings.bucket, Key=key)['Body'].read()
        check(hashlib.sha256(body).hexdigest() == key.removeprefix(BRONZE_PREFIX),
              f'Object bytes do not match key: {key}')

    report = {'raw_rows': len(raws), 'manifest_rows': counts['n'],
              'manifest_version': table.version(), 'distinct_hashes': len(hashes),
              'objects': len(keys), 'attempts_without_body': sum(r['object_key'] is None for r in expected.values()),
              'raw_body_bytes': sum(r['body_bytes'] or 0 for r in expected.values()), 'passed': True}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
