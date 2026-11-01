from contextlib import nullcontext
from datetime import date
from unittest.mock import Mock

import pytest
import requests

from pipeline.collector import fetch, retry_delay
from pipeline.config import Settings, load_settings

SETTINGS = Settings(date(2026, 10, 25), date(2026, 10, 26), 1, 2, 3, 0, 30)


def response(status, body=b"[]", headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = body
    result.headers.update(headers or {})
    return result


def connection():
    conn = Mock()
    conn.transaction.side_effect = lambda: nullcontext()
    return conn


def test_transient_failures_are_recorded_then_success(monkeypatch):
    monkeypatch.setattr("pipeline.collector.time.sleep", lambda seconds: None)
    conn, session = connection(), Mock()
    session.get.side_effect = [requests.Timeout(), response(503), response(200, b"[1]")]
    _, body = fetch(conn, session, SETTINGS)
    assert body == b"[1]"
    assert session.get.call_count == conn.execute.call_count == 3
    records = [call.args[1] for call in conn.execute.call_args_list]
    assert [record[9] for record in records] == [None, 503, 200]
    assert records[0][13] == "Timeout"
    assert records[2][11] == b"[1]"
    assert session.get.call_args.kwargs["timeout"] == (1, 2)


def test_429_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr("pipeline.collector.time.sleep", sleeps.append)
    conn, session = connection(), Mock()
    session.get.side_effect = [response(429, headers={"Retry-After": "2"}), response(200)]
    fetch(conn, session, SETTINGS)
    assert sleeps == [2]


def test_retry_budget_is_bounded(monkeypatch):
    monkeypatch.setattr("pipeline.collector.time.sleep", lambda seconds: None)
    conn, session = connection(), Mock()
    session.get.return_value = response(503)
    with pytest.raises(RuntimeError, match="Live API fetch failed"):
        fetch(conn, session, SETTINGS)
    assert session.get.call_count == conn.execute.call_count == 3


@pytest.mark.parametrize("failure", [response(401), response(403), response(400), requests.exceptions.SSLError()])
def test_permanent_failures_are_not_retried(failure):
    conn, session = connection(), Mock()
    if isinstance(failure, Exception):
        session.get.side_effect = failure
    else:
        session.get.return_value = failure
    with pytest.raises(RuntimeError):
        fetch(conn, session, SETTINGS)
    assert session.get.call_count == conn.execute.call_count == 1


def test_retry_after_over_budget_stops():
    with pytest.raises(RuntimeError, match="maximum"):
        retry_delay("3600", 1, SETTINGS)


def test_partial_date_configuration_is_rejected(monkeypatch):
    monkeypatch.setattr("pipeline.config.load_dotenv", lambda *args: None)
    monkeypatch.setenv("START_DATE", "2026-10-25")
    monkeypatch.delenv("END_DATE", raising=False)
    with pytest.raises(ValueError, match="both"):
        load_settings()
