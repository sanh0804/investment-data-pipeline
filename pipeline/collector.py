import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from uuid import uuid4

import requests
from psycopg.types.json import Jsonb

from pipeline.config import ENDPOINT, PRODUCT, SOURCE, load_settings
from pipeline.db import connect, upsert
from pipeline.normalize import normalize

RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
KEPT_HEADERS = {"content-type", "date", "retry-after", "etag", "cache-control"}


def retry_delay(header, attempt, settings):
    delay = settings.backoff * 2 ** (attempt - 1)
    if header:
        try:
            requested = float(header)
        except ValueError:
            try:
                requested = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                requested = 0
        delay = max(delay, requested)
    # Do not hammer the provider if Retry-After exceeds our permitted wait budget.
    if delay > settings.max_retry_delay:
        raise RuntimeError("Retry delay exceeds configured maximum; rerun later")
    return max(0, delay)


def fetch(conn, session, settings):
    run_id = uuid4()
    params = {
        "granularity": 86400,
        "start": f"{settings.start.isoformat()}T00:00:00Z",
        "end": f"{settings.end.isoformat()}T00:00:00Z",
    }
    for attempt in range(1, settings.max_attempts + 1):
        started = datetime.now(timezone.utc)
        fetch_id = uuid4()
        response = None
        error = None
        transient = False
        try:
            response = session.get(
                ENDPOINT, params=params,
                headers={"Accept": "application/json", "User-Agent": "investment-data-pipeline/0.1"},
                timeout=(settings.connect_timeout, settings.read_timeout),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            error = type(exc).__name__  # No credentials or proxy URLs in logs.
            transient = isinstance(exc, (requests.Timeout, requests.ConnectionError))
            if isinstance(exc, requests.exceptions.SSLError):
                transient = False
        fetched = datetime.now(timezone.utc)
        # Preserve body bytes, including malformed JSON/UTF-8, before validation.
        body = response.content if response is not None else None
        headers = ({k.lower(): v for k, v in response.headers.items() if k.lower() in KEPT_HEADERS}
                   if response is not None else {})
        status = response.status_code if response is not None else None
        with conn.transaction():
            conn.execute(
                """INSERT INTO raw.price_fetches
                    (fetch_id, run_id, attempt_number, source, product_id, endpoint,
                     request_params, started_at, fetched_at, http_status,
                     response_headers, response_body, response_sha256, transport_error)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (fetch_id, run_id, attempt, SOURCE, PRODUCT, ENDPOINT, Jsonb(params),
                 started, fetched, status, Jsonb(headers), body,
                 hashlib.sha256(response.content).hexdigest() if response is not None else None, error),
            )
        print(json.dumps({"run_id": str(run_id), "fetch_id": str(fetch_id),
                          "attempt": attempt, "http_status": status, "error": error}), flush=True)
        if status == 200:
            return fetch_id, body
        transient = transient or status in RETRY_STATUSES
        if not transient or attempt == settings.max_attempts:
            raise RuntimeError(f"Live API fetch failed: status={status}, error={error}; raw fetch_id={fetch_id}")
        time.sleep(retry_delay(headers.get("retry-after"), attempt, settings))
    raise RuntimeError("No successful API response")


def main():
    try:
        settings = load_settings()
        # autocommit ensures raw attempts persist independently of normalization.
        with connect() as conn, requests.Session() as session:
            conn.autocommit = True
            fetch_id, body = fetch(conn, session, settings)
            candles = normalize(body, settings.start, settings.end)
            with conn.transaction():
                upsert(conn, candles, fetch_id)
            print(json.dumps({"result": "live_collection_succeeded", "fetch_id": str(fetch_id),
                              "normalized_days": len(candles), "start": str(settings.start),
                              "end_exclusive": str(settings.end)}))
    except Exception as exc:
        # Do not echo a connection DSN/password from arbitrary library exceptions.
        if isinstance(exc, (ValueError, RuntimeError, KeyError)):
            message = str(exc)
        else:
            message = f"{type(exc).__name__}; check database/network configuration"
        print(f"Collection failed: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
