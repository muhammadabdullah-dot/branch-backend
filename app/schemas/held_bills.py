from datetime import datetime

from pydantic import BaseModel

from app.schemas.sales import SaleLineOut


class HeldBillCreate(BaseModel):
    label: str
    lines: list[SaleLineOut]
    partyId: str | None = None


class HeldBillOut(BaseModel):
    id: str
    label: str
    lines: list[SaleLineOut]
    partyId: str | None = None
    heldAt: datetime
