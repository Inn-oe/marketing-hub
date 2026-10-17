from app.quoting.bom import CatalogItem, select_bom
from app.quoting.service import followup_question

CAT = [
    CatalogItem("P550", "panel", {"watts": 550}, 120.0, 50),
    CatalogItem("P450", "panel", {"watts": 450}, 90.0, 0),  # cheaper but out of stock
    CatalogItem("MNT", "mounting", {}, 15.0, 100),
    CatalogItem("INV3", "inverter", {"kva": 3}, 500.0, 5),
    CatalogItem("INV5", "inverter", {"kva": 5}, 800.0, 5),
    CatalogItem("BAT5", "battery", {"kwh": 5}, 900.0, 4),
]


def test_prefers_in_stock_and_sizes_up():
    bom, notes = select_bom({"panel_kw": 3.3, "inverter_kva": 4.0, "battery_kwh": 8.0}, CAT)
    assert bom == {"P550": 6, "MNT": 6, "INV5": 1, "BAT5": 2}
    assert notes == []


def test_reports_unfillable_categories():
    bom, notes = select_bom({"panel_kw": 3, "inverter_kva": 20, "battery_kwh": 5}, [CAT[0]])
    assert "no_inverter_large_enough" in notes and "no_battery_in_catalogue" in notes


def test_no_battery_when_not_requested():
    bom, _ = select_bom({"panel_kw": 2, "inverter_kva": 2, "battery_kwh": 0}, CAT)
    assert "BAT5" not in bom


def test_followup_question():
    assert followup_question({"monthly_kwh": 300, "location": "Harare"}) is None
    assert "kWh" in followup_question({"location": "Harare"})
