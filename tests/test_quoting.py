from datetime import datetime, timedelta, timezone

from app.quoting.engine import PricingRules, StockItem, build_quote, missing_fields, size_system

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
RULES = PricingRules()
SPEC = {"monthly_kwh": 450, "location": "Harare", "property_type": "residential"}


def stock(qty=10, age_days=1, price=100.0):
    return {"PNL": StockItem("PNL", "Panel", price, qty, NOW - timedelta(days=age_days))}


def test_clean_quote_has_no_flags_with_matching_history():
    q = build_quote(SPEC, stock(), {"PNL": 10}, 10, RULES, history_totals=[1900, 2000, 2100], now=NOW)
    assert q.flags == []
    assert q.subtotal == 1000.0
    assert not q.needs_director


def test_out_of_stock_stale_price_and_unknown_sku_flagged():
    q = build_quote(SPEC, stock(qty=2, age_days=30), {"PNL": 10, "XYZ": 1}, 10, RULES, [1, 1, 1], now=NOW)
    assert "out_of_stock:PNL" in q.flags
    assert "stale_price:PNL" in q.flags
    assert "unknown_sku:XYZ" in q.flags


def test_history_divergence_and_cap():
    q = build_quote(SPEC, stock(price=600), {"PNL": 10}, 10, RULES, history_totals=[2000, 2000, 2000], now=NOW)
    assert "over_value_cap" in q.flags
    assert any(f.startswith("differs_from_history") for f in q.flags)


def test_missing_fields_and_custom_job_and_no_history():
    q = build_quote({"property_type": "commercial"}, stock(), {"PNL": 1}, 5, RULES, now=NOW)
    assert any(f.startswith("missing_fields") for f in q.flags)
    assert "custom_job" in q.flags
    assert "no_history" in q.flags


def test_helpers():
    assert missing_fields({"monthly_kwh": 300}) == ["location"]
    assert size_system({"monthly_kwh": 450, "backup_hours": 4, "peak_load_kw": 2})["battery_kwh"] == 8.0
