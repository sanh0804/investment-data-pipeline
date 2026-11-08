"""Offline tests for publications: idempotency, reproduction, diff and dataset isolation."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from pipeline import lake_publish
from pipeline.lake_publish import Lake, diff, publications, publish, reproduce

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def marts(dataset, volume, cutoff=date(2026, 5, 3)):
    daily = [{"dataset_id": dataset, "trade_date": date(2026, 5, d), "as_of_date": cutoff,
              "trade_count": 1, "active_customers": 1, "trade_volume_usd": v,
              "fees_usd": Decimal("0.10")} for d, v in ((1, Decimal("200.00")), (2, volume))]
    summary = [{"dataset_id": dataset, "customer_id": f"c{i}", "customer_key": f"k{i}",
                "created_at": datetime(2026, 4, i, tzinfo=timezone.utc), "as_of_date": cutoff,
                "first_trade_date": None if i == 2 else date(2026, 5, 1),
                "last_trade_date": None if i == 2 else date(2026, 5, 2),
                "days_since_last_trade": None if i == 2 else 1, "total_trades": 0 if i == 2 else 2,
                "total_volume_usd": Decimal("0.00") if i == 2 else Decimal("200.00") + volume,
                "total_fee_usd": Decimal("0.00") if i == 2 else Decimal("0.20"),
                "activity_segment": "never_traded" if i == 2 else "active"} for i in (1, 2)]
    return {"mart_daily_trading": daily, "mart_customer_summary": summary}


@pytest.fixture
def source(monkeypatch):
    data = {}
    monkeypatch.setattr(lake_publish, "read_mart",
                        lambda conn, schema, mart, dataset, columns: [dict(r) for r in data[dataset][mart]])
    return data


def run(lake, dataset, note=None):
    return publish(None, lake, "analytics", dataset, sha="test", note=note, now=NOW)


def test_republishing_identical_content_creates_nothing(tmp_path, source):
    lake = Lake(str(tmp_path), {})
    source["a"] = marts("a", Decimal("100.00"))
    first = run(lake, "a")
    again = run(lake, "a")
    assert first["published"] and not again["published"]
    assert again["publication_id"] == first["publication_id"]
    assert len(publications(lake)) == 1
    assert (first["trade_count"], first["trade_volume_usd"]) == (2, Decimal("300.00"))


def test_correction_is_reproducible_and_explained_by_diff(tmp_path, source):
    lake = Lake(str(tmp_path), {})
    source["a"] = marts("a", Decimal("100.00"))
    old = run(lake, "a")["publication_id"]
    source["a"] = marts("a", Decimal("120.00"))
    new = run(lake, "a", note="corrected trade")["publication_id"]

    # The earlier report is still readable exactly as published.
    earlier = {r["trade_date"]: r["trade_volume_usd"] for r in reproduce(lake, old)["daily_trading"]}
    assert earlier[date(2026, 5, 2)] == Decimal("100.00")
    changes = diff(lake, old, new)
    assert changes["daily_trading"] == {"added": [], "removed": [], "changed": [
        {"key": ["a", date(2026, 5, 2)],
         "changes": {"trade_volume_usd": [Decimal("100.00"), Decimal("120.00")]}}]}
    assert changes["customer_summary"]["changed"] == [
        {"key": ["a", "c1"], "changes": {"total_volume_usd": [Decimal("300.00"), Decimal("320.00")]}}]


def test_publishing_another_dataset_leaves_earlier_reports_intact(tmp_path, source):
    lake = Lake(str(tmp_path), {})
    source["a"] = marts("a", Decimal("100.00"))
    source["b"] = marts("b", Decimal("50.00"))
    pub_a = run(lake, "a")["publication_id"]
    run(lake, "b")
    run(lake, "b")  # unchanged, no new version
    assert [p["dataset_id"] for p in publications(lake)] == ["a", "b"]
    assert len(reproduce(lake, pub_a)["daily_trading"]) == 2
    assert {r["dataset_id"] for r in lake_publish.report_rows(lake, "daily_trading", "a")} == {"a"}


def test_rejects_mixed_cutoffs_and_unsafe_dataset_ids(tmp_path, source):
    lake = Lake(str(tmp_path), {})
    data = marts("a", Decimal("100.00"))
    data["mart_daily_trading"][0]["as_of_date"] = date(2026, 5, 2)
    source["a"] = data
    with pytest.raises(ValueError, match="one as_of_date"):
        run(lake, "a")
    with pytest.raises(ValueError, match="unsafe"):
        run(lake, "a' OR '1'='1")
