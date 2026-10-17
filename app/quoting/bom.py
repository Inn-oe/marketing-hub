"""Pick a bill of materials from the catalogue for a sized system. Pure functions, no I/O."""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogItem:
    sku: str
    category: str  # panel | inverter | battery | mounting
    specs: dict  # watts, kva, kwh
    unit_price: float
    qty_available: int


def _cheapest(items: list[CatalogItem]) -> CatalogItem | None:
    # Prefer in-stock items, then lowest price, so out-of-stock only wins when nothing else fits.
    return min(items, key=lambda i: (i.qty_available <= 0, i.unit_price), default=None)


def select_bom(sizing: dict[str, float], catalogue: list[CatalogItem]) -> tuple[dict[str, int], list[str]]:
    """Returns ({sku: qty}, notes). Notes name categories that could not be filled."""
    bom: dict[str, int] = {}
    notes: list[str] = []

    panels = [i for i in catalogue if i.category == "panel" and i.specs.get("watts")]
    if panel := _cheapest(panels):
        count = math.ceil(sizing["panel_kw"] * 1000 / panel.specs["watts"])
        bom[panel.sku] = count
        mounts = [i for i in catalogue if i.category == "mounting"]
        if mount := _cheapest(mounts):
            bom[mount.sku] = count  # one mounting set per panel
    else:
        notes.append("no_panel_in_catalogue")

    inverters = [i for i in catalogue if i.category == "inverter" and i.specs.get("kva", 0) >= sizing["inverter_kva"]]
    if inv := _cheapest(inverters):
        bom[inv.sku] = 1
    else:
        notes.append("no_inverter_large_enough")

    if sizing["battery_kwh"] > 0:
        batteries = [i for i in catalogue if i.category == "battery" and i.specs.get("kwh")]
        if batt := _cheapest(batteries):
            bom[batt.sku] = math.ceil(sizing["battery_kwh"] / batt.specs["kwh"])
        else:
            notes.append("no_battery_in_catalogue")

    return bom, notes
