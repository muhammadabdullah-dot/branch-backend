import secrets
from decimal import Decimal

from tortoise import Tortoise
from tortoise.transactions import atomic

from app.models import Adjustment, Location, PhysicalCount
from app.schemas.locations import LocationCreate, LocationUpdate


# Billing takes stock from here and customer returns put it back here (sales_service, returns_service).
SALES_LOCATION_ID = "loc-1"


class LocationError(Exception):
    def __init__(self, message: str):
        self.message = message


_HOLDINGS_SQL = """
SELECT location_id, COUNT(*) AS items, COALESCE(SUM(qty), 0) AS units
FROM (
    SELECT product_id, location_id, SUM(qty) AS qty
    FROM stock_movements
    GROUP BY product_id, location_id
    HAVING SUM(qty) != 0
) b
GROUP BY location_id
"""


async def holdings() -> dict[str, tuple[int, Decimal]]:
    """Items and units currently at each location, folded from the ledger."""
    rows = await Tortoise.get_connection("default").execute_query_dict(_HOLDINGS_SQL)
    return {r["location_id"]: (int(r["items"]), Decimal(str(r["units"]))) for r in rows}


async def list_all() -> list[Location]:
    return await Location.all().order_by("-active", "priority", "name")


async def _name_taken(name: str, except_id: str | None = None) -> bool:
    qs = Location.filter(name__iexact=name.strip())
    if except_id:
        qs = qs.exclude(id=except_id)
    return await qs.exists()


@atomic()
async def create(data: LocationCreate) -> Location:
    name = data.name.strip()
    if await _name_taken(name):
        raise LocationError(f"There's already a location called {name}.")
    return await Location.create(
        id=f"loc-{secrets.token_hex(4)}", name=name, kind=data.kind, priority=data.priority, active=True,
    )


@atomic()
async def update(location_id: str, data: LocationUpdate) -> Location:
    location = await Location.get_or_none(id=location_id)
    if not location:
        raise LocationError("That location doesn't exist.")

    if data.name is not None:
        name = data.name.strip()
        if await _name_taken(name, except_id=location.id):
            raise LocationError(f"There's already a location called {name}.")
        location.name = name
    if data.kind is not None:
        location.kind = data.kind
    if data.priority is not None:
        location.priority = data.priority

    if data.active is False and location.active:
        await _refuse_deactivation(location)
        location.active = False
    elif data.active is True:
        location.active = True

    await location.save()
    return location


async def _refuse_deactivation(location: Location) -> None:
    """Switching a location off hides it from every picker. That's only safe once nothing is left
    there — otherwise the stock would still count in the branch's totals but nobody could count,
    adjust or move it, because the place it sits no longer appears anywhere."""
    if location.id == SALES_LOCATION_ID:
        raise LocationError(
            f"{location.name} is where the counter sells from and returns go back to, so it stays switched on. Rename it if the name no longer fits."
        )
    items, units = (await holdings()).get(location.id, (0, Decimal("0")))
    if items:
        raise LocationError(
            f"{location.name} still holds {items} item{'s' if items != 1 else ''} ({units.normalize():f} units). "
            "Move or adjust that stock out before switching it off."
        )
    pending_counts = await PhysicalCount.filter(location_id=location.id, status="pending").count()
    pending_adjustments = await Adjustment.filter(location_id=location.id, status="pending").count()
    if pending_counts or pending_adjustments:
        raise LocationError(
            f"{location.name} has {pending_counts} count(s) and {pending_adjustments} adjustment(s) waiting for approval. "
            "Approve or reject them first."
        )
    if await Location.filter(active=True).exclude(id=location.id).count() == 0:
        raise LocationError("This is the branch's last active location. Add another before switching it off.")
