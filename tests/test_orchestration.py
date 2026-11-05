from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock
import hashlib
import json
from uuid import uuid4

import pytest

from pipeline.config import PRODUCTS, SOURCE
from pipeline.orchestration import resolve_interval, interval_settings, verify_interval

START = datetime(2026, 10, 28, tzinfo=timezone.utc)
END = START + timedelta(days=1)
TODAY = date(2026, 11, 1)


def test_scheduled_day_and_explicit_dates_override_stale_env(monkeypatch):
    assert resolve_interval(START, END, today=TODAY) == (START.date(), END.date())
    monkeypatch.setenv('START_DATE', 'not-a-date')
    monkeypatch.setenv('END_DATE', '2099-01-01')
    monkeypatch.setenv('PRODUCTS', ','.join(PRODUCTS))
    settings = interval_settings(START.date(), END.date())
    assert (settings.start, settings.end, settings.products) == (START.date(), END.date(), PRODUCTS)


def test_manual_run_requires_explicit_dates():
    with pytest.raises(ValueError, match='require'):
        resolve_interval(START, END, manual=True, today=TODAY)
    assert resolve_interval(None, None, {'start': '2026-10-28', 'end': '2026-10-31'}, manual=True, today=TODAY) == (START.date(), date(2026,10,31))


@pytest.mark.parametrize('a,b,conf,manual', [
    (START.replace(tzinfo=None), END, {}, False),
    (START + timedelta(hours=1), END, {}, False),
    (START, END + timedelta(days=1), {}, False),
    (START, END, {'start':'2026-10-28','end':'2026-10-29'}, False),
    (None,None,{'start':'2026-10-28','end':'2026-11-02'},True),
    (None,None,{'start':'2026-10-28','end':'2026-10-28'},True),
    (None,None,{'start':'2026-10-01','end':'2026-10-10'},True),
    (None,None,{'start':'2026-10-28','end':'2026-10-29','anything':1},True),
])
def test_invalid_intervals(a,b,conf,manual):
    with pytest.raises(ValueError):
        resolve_interval(a,b,conf,manual=manual,today=TODAY)


def verification_input():
    ids = [uuid4() for _ in PRODUCTS]
    result = {'result':'live_collection_succeeded','products':list(PRODUCTS), 'start':'2026-10-28',
              'end_exclusive':'2026-10-29','fetch_ids':list(map(str,ids)),
              'requests_succeeded':3,'normalized_rows':3}
    body=json.dumps([[int(START.timestamp()),10,20,12,15,100]]).encode()
    raw=[(SOURCE,p,200,{'start':'2026-10-28T00:00:00Z','end':'2026-10-29T00:00:00Z','granularity':86400},
          body,hashlib.sha256(body).hexdigest()) for p in PRODUCTS]
    prices=[(SOURCE,p,START.date(),START,12,20,10,15,100,i) for p,i in zip(PRODUCTS,ids)]
    return result,raw,prices


def test_verification_compares_exact_values_and_lineage():
    result,raw,prices=verification_input()
    conn=Mock();responses=[Mock() for _ in range(4)]
    for response,row in zip(responses,raw):response.fetchone.return_value=row
    responses[-1].fetchall.return_value=prices
    conn.execute.side_effect=responses
    assert verify_interval(conn,result)['rows']==3


@pytest.mark.parametrize('defect',['checksum','missing','value','lineage'])
def test_corrupt_or_incomplete_load_fails_verification(defect):
    result,raw,prices=verification_input()
    if defect=='checksum':raw[0]=(*raw[0][:-1],'wrong')
    if defect=='missing':prices=prices[:-1]
    if defect=='value':prices[0]=(*prices[0][:4],99,*prices[0][5:])
    if defect=='lineage':prices[0]=(*prices[0][:-1],uuid4())
    conn=Mock();responses=[Mock() for _ in range(4)]
    for response,row in zip(responses,raw):response.fetchone.return_value=row
    responses[-1].fetchall.return_value=prices
    conn.execute.side_effect=responses
    with pytest.raises(ValueError):verify_interval(conn,result)
