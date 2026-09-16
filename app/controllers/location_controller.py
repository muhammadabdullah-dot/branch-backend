from decimal import Decimal

from fastapi import HTTPException, status

from app.models import Location
from app.schemas.locations import LocationCreate, LocationOut, LocationUpdate
from app.services import location_service


def _to_out(location: Location, held: dict[str, tuple[int, Decimal]]) -> LocationOut:
    items, units = held.get(location.id, (0, Decimal("0")))
    return LocationOut(
        id=location.id, name=location.name, kind=location.kind, priority=location.priority,
        active=location.active, itemsHeld=items, unitsHeld=units,
    )


async def list_all() -> list[LocationOut]:
    held = await location_service.holdings()
    return [_to_out(loc, held) for loc in await location_service.list_all()]


async def create(data: LocationCreate) -> LocationOut:
    try:
        location = await location_service.create(data)
    except location_service.LocationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _to_out(location, {})


async def update(location_id: str, data: LocationUpdate) -> LocationOut:
    try:
        location = await location_service.update(location_id, data)
    except location_service.LocationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _to_out(location, await location_service.holdings())
