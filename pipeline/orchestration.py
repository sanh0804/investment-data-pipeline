"""UTC interval adaptation and post-load checks, independent of Airflow imports."""
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from uuid import UUID

from pipeline.config import PRODUCTS, SOURCE, load_settings
from pipeline.normalize import normalize


def resolve_interval(data_start, data_end, conf=None, *, manual=False, today=None):
    today = today or datetime.now(timezone.utc).date()
    conf = conf or {}
    if set(conf) - {'start', 'end'}:
        raise ValueError('Only start/end overrides are supported')
    if manual:
        if set(conf) != {'start', 'end'}:
            raise ValueError('Manual runs require start and end ISO dates')
        start, end = date.fromisoformat(conf['start']), date.fromisoformat(conf['end'])
        if (end - start).days > 7:
            raise ValueError('Manual range is limited to seven days')
    else:
        if conf:
            raise ValueError('Scheduled/backfill runs use their data interval, not conf')
        for boundary in (data_start, data_end):
            if boundary is None or boundary.utcoffset() != timedelta(0) or boundary.timetz().replace(tzinfo=None) != time():
                raise ValueError('Interval boundaries must be midnight UTC')
        start, end = data_start.date(), data_end.date()
        if (end - start).days != 1:
            raise ValueError('Scheduled/backfill intervals must be one UTC day')
    if not 1 <= (end - start).days <= 7 or end > today:
        raise ValueError('Use completed UTC days with positive duration')
    return start, end


def interval_settings(start, end):
    settings = load_settings(start_date=start, end_date=end)
    if settings.products != PRODUCTS:
        raise ValueError('Airflow requires BTC-USD,ETH-USD,SOL-USD in that order')
    return settings


def verify_interval(conn, result):
    """Compare committed rows and lineage against the exact successful raw bodies."""
    start = date.fromisoformat(result['start'])
    end = date.fromisoformat(result['end_exclusive'])
    if result['result'] != 'live_collection_succeeded' or tuple(result['products']) != PRODUCTS:
        raise ValueError('Unexpected collector result')
    if not 1 <= (end - start).days <= 7:
        raise ValueError('Invalid result interval')
    ids = [UUID(value) for value in result['fetch_ids']]
    if len(ids) != len(set(ids)) or result['requests_succeeded'] != len(ids):
        raise ValueError('Duplicate or missing successful fetch IDs')
    expected = {}
    for fetch_id in ids:
        row = conn.execute('''SELECT source,product_id,http_status,request_params,
            response_body,response_sha256 FROM raw.price_fetches WHERE fetch_id=%s''', (fetch_id,)).fetchone()
        if not row or row[0] != SOURCE or row[1] not in PRODUCTS or row[2] != 200:
            raise ValueError('Successful raw lineage missing')
        source, product, _, params, body, checksum = row
        if hashlib.sha256(body).hexdigest() != checksum:
            raise ValueError('Raw checksum mismatch')
        a = datetime.fromisoformat(params['start'].replace('Z', '+00:00'))
        b = datetime.fromisoformat(params['end'].replace('Z', '+00:00'))
        if not start <= a.date() < b.date() <= end or params['granularity'] != 86400:
            raise ValueError('Raw request boundaries outside task interval')
        for candle in normalize(body, a.date(), b.date()):
            key = (source, product, candle.bucket_start.date())
            if key in expected:
                raise ValueError('Overlapping raw windows')
            expected[key] = (candle.bucket_start, candle.open, candle.high,
                             candle.low, candle.close, candle.volume, fetch_id)
    keys = {(SOURCE, product, start + timedelta(days=i))
            for product in PRODUCTS for i in range((end-start).days)}
    if set(expected) != keys or result['normalized_rows'] != len(keys):
        raise ValueError('Incomplete task output coverage')
    rows = conn.execute('''SELECT source,product_id,price_date,bucket_start,
        open_price,high_price,low_price,close_price,volume_base,last_fetch_id
        FROM market.daily_prices WHERE source=%s AND product_id=ANY(%s)
        AND price_date >= %s AND price_date < %s''', (SOURCE, list(PRODUCTS), start, end)).fetchall()
    actual = {tuple(row[:3]): tuple(row[3:]) for row in rows}
    if len(rows) != len(keys) or actual != expected:
        raise ValueError('Committed prices/lineage differ from this collection result')
    return {'verified': True, 'rows': len(rows), 'start': str(start), 'end_exclusive': str(end)}
