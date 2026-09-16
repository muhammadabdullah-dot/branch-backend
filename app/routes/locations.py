from fastapi import APIRouter, Depends

from app.controllers import location_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.locations import LocationCreate, LocationOut, LocationUpdate

router = APIRouter(prefix="/inventory/locations", tags=["locations"])

# Reading is wide on purpose: counts, adjustments, receiving, stock overview, the manager's
# dashboard and reports all name locations, and none of them should break for want of a grant on
# the Locations screen itself. Changing the master is its own permission.
_read = require_any_permission(
    ("inventory.locations", "R"), ("inventory.overview", "R"), ("reports", "R"),
    ("branch-console.dashboard", "R"), ("branch-console.approvals", "R"),
)
_write = require_permission("inventory.locations", "W")


@router.get("", response_model=list[LocationOut])
async def list_locations(user: User = Depends(_read)) -> list[LocationOut]:
    """Every location, switched-off ones included — history still has to name them. Pickers show
    only the active ones."""
    return await location_controller.list_all()


@router.post("", response_model=LocationOut)
async def create_location(payload: LocationCreate, user: User = Depends(_write)) -> LocationOut:
    return await location_controller.create(payload)


@router.patch("/{location_id}", response_model=LocationOut)
async def update_location(location_id: str, payload: LocationUpdate, user: User = Depends(_write)) -> LocationOut:
    """Rename, re-kind, re-prioritise, or switch on/off. Switching off is refused while the location
    holds stock or has counts/adjustments waiting."""
    return await location_controller.update(location_id, payload)
