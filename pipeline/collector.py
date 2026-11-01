import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from uuid import uuid4

import requests
from psycopg.types.json import Jsonb

from pipeline.config import PRODUCT, SOURCE, date_windows, endpoint, load_settings
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


def fetch(conn, session, settings, product=PRODUCT, start=None, end=None):
    start, end = start or settings.start, end or settings.end
    url = endpoint(product)
    run_id = uuid4()
    params = {
        "granularity": 86400,
        "start": f"{start.isoformat()}T00:00:00Z",
        "end": f"{end.isoformat()}T00:00:00Z",
    }
    for attempt in range(1, settings.max_attempts + 1):
        started = datetime.now(timezone.utc)
        fetch_id = uuid4()
        response = None
        error = None
        transient = False
        try:
            response = session.get(
                url, params=params,
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
                (fetch_id, run_id, attempt, SOURCE, product, url, Jsonb(params),
                 started, fetched, status, Jsonb(headers), body,
                 hashlib.sha256(response.content).hexdigest() if response is not None else None, error),
            )
        print(json.dumps({"run_id": str(run_id), "fetch_id": str(fetch_id),
                          "product": product, "start": str(start), "end": str(end),
                          "attempt": attempt, "http_status": status, "error": error}), flush=True)
        if status == 200:
            return fetch_id, body
        transient = transient or status in RETRY_STATUSES
        if not transient or attempt == settings.max_attempts:
            raise RuntimeError(f"Live API fetch failed: status={status}, error={error}; raw fetch_id={fetch_id}")
        time.sleep(max(settings.request_interval, retry_delay(headers.get("retry-after"), attempt, settings)))
    raise RuntimeError("No successful API response")


def collect(conn, session, settings):
    # autocommit ensures raw attempts persist independently of normalization.
    conn.autocommit = True
    batches = []
    for product in settings.products:
        for start, end in date_windows(settings.start, settings.end, settings.chunk_days):
            if batches:
                time.sleep(settings.request_interval)
            fetch_id, body = fetch(conn, session, settings, product, start, end)
            try:
                candles = normalize(body, start, end)
            except ValueError as exc:
                raise ValueError(f"{product} [{start},{end}) fetch_id={fetch_id}: {exc}") from exc
            batches.append((product, candles, fetch_id))
    # All requested products/windows must pass before any normalized write.
    with conn.transaction():
        for product, candles, fetch_id in batches:
            upsert(conn, candles, fetch_id, product)
    return {"result": "live_collection_succeeded", "products": settings.products,
            "normalized_rows": sum(len(c) for _, c, _ in batches),
            "requests_succeeded": len(batches), "start": str(settings.start),
            "end_exclusive": str(settings.end)}


def main():
    try:
        settings = load_settings()
        with connect() as conn, requests.Session() as session:
            print(json.dumps(collect(conn, session, settings)))
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
