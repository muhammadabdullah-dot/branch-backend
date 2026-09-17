"""Stock never goes below zero. Empty means empty: nothing is sold, returned to a supplier, adjusted off, counted down or
sent that the branch doesn't hold.

Every screen checks first and says what is there; the services check again inside their transaction with the words a
person reads; and `models/inventory.py` refuses any movement that would still take a location below zero, whatever path
wrote it, so no future path can forget.

A sale takes from Main Store first (where the counters sell from), then from the branch's other locations in name order, so
an Item kept only in the fridge or the back room still sells without any one location going below zero."""
from decimal import Decimal

from tortoise.functions import Sum

from app.models import Location, Product, StockMovement
from app.models.inventory import StockBelowZero

ZERO = Decimal("0")
PLACES = Decimal("0.001")
# Where the counters sell from and customer returns go back to.
SELLING_LOCATION_ID = "loc-1"


class StockShortError(StockBelowZero):
    """Said before anything is written, in the words of what the person was doing."""


def qty_text(value: Decimal) -> str:
    return format(Decimal(value).quantize(PLACES).normalize(), "f")


def _clean(total) -> Decimal:
    return Decimal(str(total)).quantize(PLACES) if total is not None else ZERO


async def held_at(product_id: str, location_id: str) -> Decimal:
    rows = await StockMovement.filter(product_id=product_id, location_id=location_id).annotate(total=Sum("qty")).values_list("total", flat=True)
    return _clean(rows[0] if rows else None)


async def held_by_location(product_id: str) -> list[tuple[Location, Decimal]]:
    """What each switched-on location holds, the selling location first, then by name. Only locations holding some."""
    rows = await (
        StockMovement.filter(product_id=product_id).annotate(total=Sum("qty")).group_by("location_id").values_list("location_id", "total")
    )
    held = {loc_id: _clean(total) for loc_id, total in rows}
    locations = await Location.filter(id__in=list(held), active=True)
    ordered = sorted(locations, key=lambda loc: (loc.id != SELLING_LOCATION_ID, loc.name.lower()))
    return [(loc, held[loc.id]) for loc in ordered if held[loc.id] > ZERO]


async def on_hand(product_ids: list[str]) -> dict[str, Decimal]:
    """What the branch holds of each Item across its switched-on locations: what a sale can take."""
    if not product_ids:
        return {}
    active = set(await Location.filter(active=True).values_list("id", flat=True))
    rows = await (
        StockMovement.filter(product_id__in=product_ids)
        .annotate(total=Sum("qty"))
        .group_by("product_id", "location_id")
        .values_list("product_id", "location_id", "total")
    )
    out = {pid: ZERO for pid in product_ids}
    for pid, loc_id, total in rows:
        value = _clean(total)
        if loc_id in active and value > ZERO:
            out[str(pid)] = out.get(str(pid), ZERO) + value
    return out


async def plan_sale(product: Product, qty: Decimal) -> list[tuple[Location, Decimal]]:
    """Where a sale of `qty` comes from, or StockShortError naming what the branch holds."""
    plan: list[tuple[Location, Decimal]] = []
    left = Decimal(qty).quantize(PLACES)
    places = await held_by_location(str(product.id))
    for location, held in places:
        if left <= ZERO:
            break
        take = min(held, left)
        plan.append((location, take))
        left -= take
    if left > ZERO:
        have = sum((held for _, held in places), ZERO)
        if have <= ZERO:
            raise StockShortError(f"{product.name} is out of stock. Nothing can be sold until stock is received or counted in.")
        raise StockShortError(f"Only {qty_text(have)} of {product.name} in stock; this bill needs {qty_text(qty)}.")
    return plan


async def require_held(product: Product, location: Location, qty: Decimal, doing: str) -> None:
    """Taking `qty` out of one location for `doing` ("this return to the supplier", "this adjustment")."""
    held = await held_at(str(product.id), location.id)
    if held < Decimal(qty).quantize(PLACES):
        have = "none" if held <= ZERO else f"only {qty_text(held)}"
        raise StockShortError(f"{location.name} holds {have} of {product.name}; {doing} needs {qty_text(qty)}. Stock can't go below zero.")
