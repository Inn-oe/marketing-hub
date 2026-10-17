"""Glue between the chat, the database and the pricing engine."""
import json
from datetime import date, datetime, timedelta, timezone

from app.config import get_settings
from app.db.pool import pool
from app.quoting.bom import CatalogItem, select_bom
from app.quoting.engine import PricingRules, QuoteResult, StockItem, build_quote, missing_fields, size_system

DEFAULT_DISTANCE_KM = 15.0  # until geocoding exists; the quote is flagged so a human checks it
VALIDITY_DAYS = 7

QUESTIONS = {
    "monthly_kwh": "roughly how many kWh of electricity you use per month (it's on your bill)",
    "location": "the suburb or town where the system will be installed",
}


def rules_from_settings() -> PricingRules:
    s = get_settings()
    return PricingRules(
        value_cap=s.quote_value_cap,
        history_tolerance=s.quote_history_tolerance,
        price_max_age_days=s.price_max_age_days,
    )


def followup_question(spec: dict) -> str | None:
    missing = missing_fields(spec)
    if not missing:
        return None
    asks = " and ".join(QUESTIONS[m] for m in missing)
    return f"To prepare your quote I just need {asks}."


async def _load_catalogue() -> tuple[list[CatalogItem], dict[str, StockItem]]:
    rows = await pool().fetch(
        "SELECT p.sku, p.name, p.category, p.specs, s.unit_price, s.qty_on_hand - s.qty_reserved AS avail, s.updated_at "
        "FROM products p JOIN stock_levels s ON s.product_id = p.id WHERE p.active"
    )
    catalogue, stock = [], {}
    for r in rows:
        specs = r["specs"] if isinstance(r["specs"], dict) else json.loads(r["specs"] or "{}")
        price = float(r["unit_price"])
        catalogue.append(CatalogItem(r["sku"], r["category"], specs, price, r["avail"]))
        stock[r["sku"]] = StockItem(r["sku"], r["name"], price, r["avail"], r["updated_at"])
    return catalogue, stock


async def _history_totals(sizing_kw: float) -> list[float]:
    """Totals of past sent/accepted quotes within +-30% of this system size."""
    rows = await pool().fetch(
        "SELECT total FROM quotes WHERE status IN ('sent','accepted') "
        "AND (spec->'sizing'->>'panel_kw')::numeric BETWEEN $1 AND $2",
        sizing_kw * 0.7,
        sizing_kw * 1.3,
    )
    return [float(r["total"]) for r in rows]


async def create_quote(contact_id: str, conversation_id: str, spec: dict) -> tuple[str, QuoteResult]:
    """Price the spec, store it, and return (quote_id, result). Status reflects whether a Director must review."""
    sizing = size_system(spec)
    catalogue, stock = await _load_catalogue()
    bom, notes = select_bom(sizing, catalogue)
    result = build_quote(
        spec, stock, bom, DEFAULT_DISTANCE_KM, rules_from_settings(), await _history_totals(sizing["panel_kw"])
    )
    result.flags += notes + ["distance_unverified"]

    status = "pending_director" if result.needs_director else "approved"
    valid_until = date.today() + timedelta(days=VALIDITY_DAYS)
    async with pool().acquire() as conn, conn.transaction():
        quote_id = await conn.fetchval(
            "INSERT INTO quotes (contact_id, conversation_id, spec, subtotal, labour, transport, margin_pct, total, "
            "flags, status, valid_until) VALUES ($1,$2,$3::jsonb,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id",
            contact_id,
            conversation_id,
            json.dumps({**spec, "sizing": sizing}),
            result.subtotal,
            result.labour,
            result.transport,
            rules_from_settings().margin_pct,
            result.total,
            result.flags,
            status,
            valid_until,
        )
        for line in result.lines:
            await conn.execute(
                "INSERT INTO quote_items (quote_id, product_id, qty, unit_price_snapshot, line_total, in_stock) "
                "SELECT $1, id, $3, $4, $5, $6 FROM products WHERE sku=$2",
                quote_id,
                line.sku,
                line.qty,
                line.unit_price,
                line.total,
                stock[line.sku].qty_available >= line.qty,
            )
    return quote_id, result


def format_quote_for_client(quote: dict, lines: list[dict]) -> str:
    """Quote text is built from stored numbers only; no LLM touches it."""
    if "director_price" in (quote["flags"] or []):
        # The Director set the total by hand, so itemised lines would no longer add up.
        breakdown = ""
    else:
        body = "\n".join(f"- {l['qty']} x {l['name']}: {float(l['line_total']):,.2f}" for l in lines)
        breakdown = (
            f"{body}\nInstallation: {float(quote['labour']):,.2f}\nTransport: {float(quote['transport']):,.2f}\n"
        )
    return (
        f"Here is your solar quote:\n{breakdown}"
        f"Total: {quote['currency']} {float(quote['total']):,.2f}\n"
        f"Valid until {quote['valid_until']:%d %b %Y}. Prices are provisional until a site survey. "
        "Reply YES to accept and we'll schedule your installation."
    )


def format_quote_for_director(quote_id: str, quote: dict, flags: list[str]) -> str:
    return (
        f"Quote {str(quote_id)[:8]} needs your review.\nTotal: {quote['currency']} {float(quote['total']):,.2f}\n"
        f"Flags: {', '.join(flags) or 'none'}\n"
        f"Reply with the buttons, or send: price {str(quote_id)[:8]} <amount> to set your own total."
    )


async def load_quote(quote_id_or_prefix: str) -> tuple[dict, list[dict]] | None:
    q = await pool().fetchrow(
        "SELECT * FROM quotes WHERE id::text LIKE $1 || '%' ORDER BY created_at DESC LIMIT 1", quote_id_or_prefix
    )
    if q is None:
        return None
    lines = await pool().fetch(
        "SELECT qi.qty, qi.line_total, p.name FROM quote_items qi JOIN products p ON p.id = qi.product_id "
        "WHERE qi.quote_id = $1",
        q["id"],
    )
    return dict(q), [dict(l) for l in lines]


async def set_status(quote_id: str, status: str, approved_by: str | None = None) -> None:
    await pool().execute(
        "UPDATE quotes SET status=$2, approved_by=COALESCE($3, approved_by) WHERE id=$1", quote_id, status, approved_by
    )


async def override_total(quote_id: str, total: float, approved_by: str) -> None:
    await pool().execute(
        "UPDATE quotes SET total=$2, status='approved', approved_by=$3, flags = flags || ARRAY['director_price'] WHERE id=$1",
        quote_id,
        total,
        approved_by,
    )
