from datetime import datetime

from pydantic import BaseModel

from app.schemas.sales import SaleLineOut


class HeldBillCreate(BaseModel):
    label: str
    lines: list[SaleLineOut]
    partyId: str | None = None
    # Holding a recalled bill again: the pharmacy pass it came with and the bill it was for, so its pharmacy lines stay
    # cleared for payment (services/pharmacy_service.py).
    pharmacyPass: str | None = None
    billId: str | None = None


class HeldBillOut(BaseModel):
    id: str
    label: str
    lines: list[SaleLineOut]
    partyId: str | None = None
    heldAt: datetime


class HeldBillRecallIn(BaseModel):
    # The bill on the till it is recalled into (its POST /sales clientRequestId): a pharmacy pass is for that bill only.
    billId: str


class HeldBillRecallOut(BaseModel):
    bill: HeldBillOut
    # Present when the person recalling may not sell Pharmacy Items and the bill carries pharmacy lines pharmacy staff
    # cleared: POST /sales takes those lines, up to those quantities, with it.
    pharmacyPass: str | None = None
