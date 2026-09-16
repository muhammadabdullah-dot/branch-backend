from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.types import Qty

# The places a grocery-and-pharmacy branch actually keeps stock. "godown" is the branch's own
# storeroom — the central warehouse is a separate thing, run from the Cloud App.
LocationKind = Literal["floor", "back-room", "cold", "godown"]


class LocationOut(BaseModel):
    id: str
    name: str
    kind: str
    priority: int
    active: bool
    # What's sitting there now — shown on the Locations screen, and the reason a location can't be
    # switched off while it still holds anything.
    itemsHeld: int = 0
    unitsHeld: Qty = 0


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: LocationKind = "floor"
    # Legacy `Priority`: lower comes first in pickers, and the lowest active one is the default.
    priority: int = Field(default=1, ge=1, le=99)


class LocationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    kind: LocationKind | None = None
    priority: int | None = Field(default=None, ge=1, le=99)
    active: bool | None = None
