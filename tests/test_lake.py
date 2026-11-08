"""Offline tests for the Delta manifest and object copy; Delta writes use a local path."""
import hashlib
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import UUID

import pyarrow as pa
import pytest
from botocore.exceptions import ClientError
from deltalake import DeltaTable

from pipeline.lake import (BRONZE_PREFIX, MANIFEST_SCHEMA, append_manifest, check_schema,
                           manifest_row, manifest_table, put_object_once, query)

EXPORTED = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
BODY = b'[[1777593600,1,2,1.5,1.6,10]]'


def raw(number, body=BODY, sha=None, status=200, error=None):
    return {
        "fetch_id": UUID(int=number), "run_id": UUID(int=1000), "attempt_number": number,
        "source": "coinbase_exchange", "product_id": "BTC-USD",
        "endpoint": "https://api.exchange.coinbase.com/products/BTC-USD/candles",
        "request_params": {"start": "2026-10-28T00:00:00Z", "granularity": 86400},
        "started_at": datetime(2026, 10, 29, 0, 0, number, tzinfo=timezone.utc),
        "fetched_at": datetime(2026, 10, 29, 0, 0, number, 500, tzinfo=timezone.utc),
        "http_status": status, "response_headers": {"content-type": "application/json"},
        "response_body": body,
        "response_sha256": sha if sha is not None else (hashlib.sha256(body).hexdigest() if body else None),
        "transport_error": error,
    }


def client_error(code):
    return ClientError({"Error": {"Code": code}}, "PutObject")


def test_manifest_row_points_to_content_addressed_object():
    row = manifest_row(raw(1), EXPORTED)
    digest = hashlib.sha256(BODY).hexdigest()
    assert row["object_key"] == BRONZE_PREFIX + digest
    assert row["body_bytes"] == len(BODY)
    assert row["fetch_id"] == "00000000-0000-0000-0000-000000000001"
    # Canonical JSON: key order in PostgreSQL JSONB must not change the stored text.
    assert row["request_params"] == '{"granularity":86400,"start":"2026-10-28T00:00:00Z"}'


def test_attempt_without_body_is_kept_without_object():
    row = manifest_row(raw(2, body=None, status=None, error="Timeout"), EXPORTED)
    assert (row["object_key"], row["body_bytes"], row["transport_error"]) == (None, None, "Timeout")
    assert manifest_table([row]).num_rows == 1


def test_inconsistent_raw_hash_stops_export():
    with pytest.raises(ValueError, match="hash"):
        manifest_row(raw(3, sha="0" * 64), EXPORTED)
    with pytest.raises(ValueError, match="without a body"):
        manifest_row(raw(4, body=None, sha="0" * 64), EXPORTED)


@pytest.mark.parametrize("field", [
    pa.field("attempt_number", pa.int64(), nullable=False),     # wider int
    pa.field("body_bytes", pa.float64()),                        # lossy numeric
    pa.field("started_at", pa.timestamp("us"), nullable=False),  # naive timestamp
])
def test_schema_guard_rejects_compatible_but_different_types(field):
    schema = MANIFEST_SCHEMA.set(MANIFEST_SCHEMA.get_field_index(field.name), field)
    with pytest.raises(ValueError, match="schema mismatch"):
        check_schema(schema)


def test_put_object_once_accepts_identical_existing_object():
    key = BRONZE_PREFIX + hashlib.sha256(BODY).hexdigest()
    s3 = Mock()
    s3.put_object.side_effect = client_error("PreconditionFailed")
    s3.get_object.return_value = {"Body": Mock(read=Mock(return_value=BODY))}
    assert put_object_once(s3, "lake", key, BODY) is False
    assert s3.put_object.call_args.kwargs["IfNoneMatch"] == "*"


def test_put_object_once_rejects_conflicting_existing_object():
    key = BRONZE_PREFIX + hashlib.sha256(BODY).hexdigest()
    s3 = Mock()
    s3.put_object.side_effect = client_error("PreconditionFailed")
    s3.get_object.return_value = {"Body": Mock(read=Mock(return_value=b"other"))}
    with pytest.raises(ValueError, match="different content"):
        put_object_once(s3, "lake", key, BODY)


def test_put_object_once_propagates_other_errors():
    s3 = Mock()
    s3.put_object.side_effect = client_error("AccessDenied")
    with pytest.raises(ClientError):
        put_object_once(s3, "lake", BRONZE_PREFIX + "x", BODY)


def test_manifest_rerun_inserts_nothing_and_keeps_version(tmp_path):
    uri = str(tmp_path / "manifest")
    rows = [manifest_row(raw(n), EXPORTED) for n in (1, 2)]
    assert append_manifest(uri, {}, rows) == (2, 0)
    assert append_manifest(uri, {}, rows) == (0, 0)  # same input: no new Delta version

    later = manifest_row(raw(3, body=None, status=None, error="Timeout"), EXPORTED)
    assert append_manifest(uri, {}, rows + [later]) == (1, 1)
    table = DeltaTable(uri)
    stored = query(table, "SELECT fetch_id, object_key FROM t ORDER BY fetch_id").to_pylist()
    assert [r["fetch_id"][-1] for r in stored] == ["1", "2", "3"]
    assert stored[2]["object_key"] is None
    # Time travel: the first version still shows only the first two attempts.
    assert query(DeltaTable(uri, version=0), "SELECT count(*) AS n FROM t").to_pylist() == [{"n": 2}]
