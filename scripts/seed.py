"""Load sample catalogue and staff. Run once after the first deploy:  python -m scripts.seed

SAMPLE PRICES ARE PLACEHOLDERS. Replace with real stock data before any quote reaches a client.
Set DIRECTOR_PHONE (digits only, international format) so Director approvals have somewhere to go.
"""
import asyncio
import json
import os

from app.db.pool import apply_schema, close_pool, init_pool, pool

PRODUCTS = [
    ("PNL-550", "550W Mono Panel", "panel", {"watts": 550}, 120.0, 40),
    ("MNT-SET", "Roof Mounting Set (per panel)", "mounting", {}, 18.0, 200),
    ("INV-3K", "3kVA Hybrid Inverter", "inverter", {"kva": 3}, 520.0, 6),
    ("INV-5K", "5kVA Hybrid Inverter", "inverter", {"kva": 5}, 820.0, 6),
    ("INV-8K", "8kVA Hybrid Inverter", "inverter", {"kva": 8}, 1350.0, 3),
    ("BAT-5K", "5kWh Lithium Battery", "battery", {"kwh": 5}, 950.0, 8),
]


async def main() -> None:
    await init_pool()
    await apply_schema()
    async with pool().acquire() as conn, conn.transaction():
        for sku, name, cat, specs, price, qty in PRODUCTS:
            pid = await conn.fetchval(
                "INSERT INTO products (sku, name, category, specs) VALUES ($1,$2,$3,$4::jsonb) "
                "ON CONFLICT (sku) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                sku, name, cat, json.dumps(specs),
            )
            await conn.execute(
                "INSERT INTO stock_levels (product_id, qty_on_hand, unit_price, unit_cost) VALUES ($1,$2,$3,$3*0.8) "
                "ON CONFLICT (product_id) DO UPDATE SET qty_on_hand=EXCLUDED.qty_on_hand, unit_price=EXCLUDED.unit_price, updated_at=now()",
                pid, qty, price,
            )
        if phone := os.environ.get("DIRECTOR_PHONE"):
            await conn.execute(
                "INSERT INTO staff_users (name, wa_phone, role, on_duty) VALUES ('Director',$1,'director',true) "
                "ON CONFLICT (wa_phone) DO UPDATE SET role='director', on_duty=true",
                phone,
            )
    await close_pool()
    print("Seeded.")


if __name__ == "__main__":
    asyncio.run(main())
