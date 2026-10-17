"""Deterministic quoting. The LLM only supplies a spec; every number here comes from code and data."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median


@dataclass(frozen=True)
class StockItem:
    sku: str
    name: str
    unit_price: float
    qty_available: int
    price_updated_at: datetime


@dataclass(frozen=True)
class QuoteLine:
    sku: str
    name: str
    qty: int
    unit_price: float

    @property
    def total(self) -> float:
        return round(self.qty * self.unit_price, 2)


@dataclass
class QuoteResult:
    lines: list[QuoteLine]
    subtotal: float
    labour: float
    transport: float
    total: float
    flags: list[str] = field(default_factory=list)

    @property
    def needs_director(self) -> bool:
        return bool(self.flags)


@dataclass(frozen=True)
class PricingRules:
    labour_per_kw: float = 120.0
    transport_per_km: float = 0.8
    margin_pct: float = 0.15
    value_cap: float = 5000.0
    history_tolerance: float = 0.20
    price_max_age_days: int = 7


REQUIRED_FIELDS = ("monthly_kwh", "location")


def missing_fields(spec: dict) -> list[str]:
    return [f for f in REQUIRED_FIELDS if spec.get(f) in (None, "")]


def size_system(spec: dict) -> dict[str, float]:
    """Very simple sizing; replace with the company's own rules. Returns kW of panels, kVA inverter, kWh battery."""
    monthly_kwh = float(spec.get("monthly_kwh") or 0)
    peak_kw = float(spec.get("peak_load_kw") or 0) or max(monthly_kwh / 30 / 5, 1.0)
    backup_h = float(spec.get("backup_hours") or 0)
    panel_kw = round(monthly_kwh / 30 / 4.5, 1)  # ~4.5 peak sun hours per day
    return {
        "panel_kw": max(panel_kw, 1.0),
        "inverter_kva": round(peak_kw * 1.25, 1),
        "battery_kwh": round(peak_kw * backup_h, 1),
    }


def build_quote(
    spec: dict,
    stock: dict[str, StockItem],
    bom: dict[str, int],
    distance_km: float,
    rules: PricingRules,
    history_totals: list[float] | None = None,
    now: datetime | None = None,
) -> QuoteResult:
    """bom maps sku -> quantity. Flags explain every reason a human must review it."""
    now = now or datetime.now(timezone.utc)
    flags: list[str] = []

    if missing := missing_fields(spec):
        flags.append("missing_fields:" + ",".join(missing))

    lines: list[QuoteLine] = []
    for sku, qty in bom.items():
        item = stock.get(sku)
        if item is None:
            flags.append(f"unknown_sku:{sku}")
            continue
        if item.qty_available < qty:
            flags.append(f"out_of_stock:{sku}")
        if now - item.price_updated_at > timedelta(days=rules.price_max_age_days):
            flags.append(f"stale_price:{sku}")
        lines.append(QuoteLine(sku, item.name, qty, item.unit_price))

    subtotal = round(sum(line.total for line in lines), 2)
    panel_kw = size_system(spec)["panel_kw"]
    labour = round(panel_kw * rules.labour_per_kw, 2)
    transport = round(distance_km * 2 * rules.transport_per_km, 2)
    total = round((subtotal + labour + transport) * (1 + rules.margin_pct), 2)

    if total > rules.value_cap:
        flags.append("over_value_cap")
    if spec.get("property_type") in ("commercial", "industrial") or spec.get("grid_type") == "three_phase":
        flags.append("custom_job")

    if history_totals and len(history_totals) >= 3:
        mid = median(history_totals)
        if mid > 0 and abs(total - mid) / mid > rules.history_tolerance:
            flags.append(f"differs_from_history:{mid:.0f}")
    elif not history_totals:
        flags.append("no_history")

    return QuoteResult(lines=lines, subtotal=subtotal, labour=labour, transport=transport, total=total, flags=flags)
