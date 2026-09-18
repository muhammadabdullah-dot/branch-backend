"""Stock never goes below zero. Empty means empty: nothing is sold, returned to a supplier, adjusted off, counted down or
sent that the branch doesn't hold.

Every screen checks first and says what is there; the services check again inside their transaction with the words a
person reads; and `models/inventory.py` refuses any movement that would still take a location below zero, whatever path
wrote it, so no future path can forget.

A sale takes from Main Store first (where the counters sell from), then from the branch's other locations in name order, so
an Item kept only in the fridge or the back room still sells without any one location going below zero.

One exception, for chosen people only: someone with "Sell when stock shows zero" can sell what the shelf holds but the
records don't (its delivery isn't entered yet). What the branch holds is still taken first; only what is missing is
taken from Main Store below zero, marked "Sold past zero". The Item then shows on Sold without stock until Main Store is
back to zero or above, which a delivery received into it does by itself. Supplier returns, adjustments, counts,
transfers and moves between locations stay never-below-zero for everyone."""
from datetime import datetime, timezone
from decimal import Decimal

from tortoise import Tortoise
from tortoise.functions import Sum

from app.models import Location, Product, StockMovement, User, UserPermission
from app.models.inventory import PAST_ZERO_REASON, SELL_PAST_ZERO_RESOURCE, StockBelowZero

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


async def may_sell_past_zero(user: User) -> bool:
    return await UserPermission.filter(user_id=user.id, resource=SELL_PAST_ZERO_RESOURCE, can_execute=True).exists()


async def plan_sale(product: Product, qty: Decimal, past_zero: bool = False) -> list[tuple[Location, Decimal, str | None]]:
    """Where a sale of `qty` comes from: (location, how many, the movement's reason). Without `past_zero`, StockShortError
    naming what the branch holds when it isn't enough; with it, what is missing comes from Main Store below zero."""
    plan: list[tuple[Location, Decimal, str | None]] = []
    left = Decimal(qty).quantize(PLACES)
    places = await held_by_location(str(product.id))
    for location, held in places:
        if left <= ZERO:
            break
        take = min(held, left)
        plan.append((location, take, None))
        left -= take
    if left > ZERO and past_zero:
        plan.append((await Location.get(id=SELLING_LOCATION_ID), left, PAST_ZERO_REASON))
        return plan
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


async def sold_without_stock() -> list[dict]:
    """Every Item Main Store is below zero on now: how many short, since when, who sold past zero and when, and what the
    branch's other locations hold of it. An Item leaves the list by itself once Main Store is back to zero or above."""
    conn = Tortoise.get_connection("default")
    short = await conn.execute_query_dict(
        "SELECT product_id AS pid FROM stock_movements WHERE location_id = ? GROUP BY product_id "
        "HAVING SUM(CAST(qty AS REAL)) < -0.0005",
        [SELLING_LOCATION_ID],
    )
    if not short:
        return []
    ids = [str(r["pid"]) for r in short]
    products = {p.id: p for p in await Product.filter(id__in=ids)}
    active = set(await Location.filter(active=True).exclude(id=SELLING_LOCATION_ID).values_list("id", flat=True))
    out: list[dict] = []
    for pid in ids:
        rows = await conn.execute_query_dict(
            "SELECT at, qty, kind, reason, origin_user_id AS uid FROM stock_movements WHERE product_id = ? AND location_id = ? "
            "ORDER BY at, rowid",
            [pid, SELLING_LOCATION_ID],
        )
        # The shortfall open now began the last time the running figure went from zero or more to below zero.
        balance, start = ZERO, 0
        for index, row in enumerate(rows):
            before = balance
            balance += _clean(row["qty"])
            if before >= ZERO > balance:
                start = index
        if balance >= ZERO:
            continue
        episode = rows[start:]
        sold = [r for r in episode if r["reason"] == PAST_ZERO_REASON] or [r for r in episode if _clean(r["qty"]) < ZERO]
        others = await conn.execute_query_dict(
            "SELECT location_id AS loc, SUM(CAST(qty AS REAL)) AS total FROM stock_movements WHERE product_id = ? AND location_id != ? "
            "GROUP BY location_id",
            [pid, SELLING_LOCATION_ID],
        )
        product = products.get(pid)
        from app.services import sell_levels

        per_unit = sell_levels.pieces_per_unit(product) if product else None
        short_text = (pieces_text(product, sell_levels.pieces_of(-balance, per_unit)) if per_unit
                      else f"{qty_text(-balance)} {product.unit if product else ''}".strip())
        out.append({
            "productId": pid, "sku": product.sku if product else pid, "name": product.name if product else pid,
            "unit": product.unit if product else None, "short": -balance, "shortText": short_text, "since": _instant(episode[0]["at"]),
            "elsewhere": sum((_clean(r["total"]) for r in others if r["loc"] in active and (r["total"] or 0) > 0), ZERO),
            "sales": [{"userId": str(r["uid"]) if r["uid"] else None, "at": _instant(r["at"]), "qty": -_clean(r["qty"])} for r in sold],
        })
    user_ids = list({sale["userId"] for o in out for sale in o["sales"] if sale["userId"]})
    names = {str(u.id): u.name for u in await User.filter(id__in=user_ids)} if user_ids else {}
    for o in out:
        for sale in o["sales"]:
            sale["by"] = names.get(sale["userId"] or "", "Not recorded")
    out.sort(key=lambda o: o["since"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out


def _instant(value) -> datetime | None:
    """A stored instant as an aware UTC datetime, however the raw query handed it back."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ── Loose pieces (services/sell_levels.py) ─────────────────────────────────────────────────────
# Stock of an Item sold loose is still kept in stocked units, as whole pieces over pieces per unit. Each location's
# figure is moved so it always stays a whole number of pieces: what is left of the last unit opened is what is loose,
# and the next unit opens by itself when those run out.

async def plan_pieces(product: Product, pieces: int, per_unit: int, past_zero: bool = False) -> list[tuple[Location, Decimal, str | None]]:
    """Where `pieces` loose pieces come from, like plan_sale: (location, stocked units taken, reason), Main Store first.
    Without `past_zero`, StockShortError when the branch doesn't hold that many; with it, the rest comes from Main Store
    below zero."""
    from app.services import sell_levels

    plan: list[tuple[Location, Decimal, str | None]] = []
    now: dict[str, Decimal] = {}
    left = pieces
    places = await held_by_location(str(product.id))
    for location, held in places:
        if left <= 0:
            break
        have = sell_levels.pieces_of(held, per_unit)
        take = min(have, left)
        if take <= 0:
            continue
        after = sell_levels.units_of(have - take, per_unit)
        plan.append((location, held - after, None))
        now[location.id] = after
        left -= take
    if left > 0 and past_zero:
        held = now[SELLING_LOCATION_ID] if SELLING_LOCATION_ID in now else await held_at(str(product.id), SELLING_LOCATION_ID)
        after = sell_levels.units_of(sell_levels.pieces_of(held, per_unit) - left, per_unit)
        plan.append((await Location.get(id=SELLING_LOCATION_ID), held - after, PAST_ZERO_REASON))
        return plan
    if left > 0:
        have = sum((sell_levels.pieces_of(held, per_unit) for _, held in places), 0)
        name = sell_levels.detail(product, "piece")
        if have <= 0:
            raise StockShortError(f"{product.name} is out of stock. Nothing can be sold until stock is received or counted in.")
        raise StockShortError(f"Only {pieces_text(product, have)} of {product.name} in stock; this bill needs {pieces} {name}.")
    return plan


async def return_pieces(product: Product, pieces: int, per_unit: int) -> Decimal:
    """The stocked units `pieces` loose pieces coming back to Main Store add, keeping its figure a whole number of pieces."""
    from app.services import sell_levels

    held = await held_at(str(product.id), SELLING_LOCATION_ID)
    return sell_levels.units_of(sell_levels.pieces_of(held, per_unit) + pieces, per_unit) - held


def pieces_text(product: Product, pieces: int) -> str:
    """"3 Box + 45 tablet" for an Item sold loose, from its pieces."""
    from app.services import sell_levels

    per_unit = sell_levels.pieces_per_unit(product)
    name = sell_levels.detail(product, "piece")
    if not per_unit:
        return f"{pieces} {_plural(name, pieces)}"
    units, loose = divmod(pieces, per_unit)
    unit = (product.unit or "unit").strip()
    if not units:
        return f"{loose} {_plural(name, loose)}"
    return f"{units} {unit}" + (f" + {loose} {_plural(name, loose)}" if loose else "")


def _plural(name: str, count: int) -> str:
    """"tablets" for more than one tablet; a short unit like "pc" stays as it is."""
    if count == 1 or len(name) <= 3 or name.lower().endswith("s"):
        return name
    return f"{name}s"
