from decimal import Decimal

from pydantic import BaseModel

from app.schemas.types import Money


class PartyOut(BaseModel):
    id: str
    code: str
    name: str
    isWalkIn: bool
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    area: str | None = None
    contactPerson: str | None = None
    ntn: str | None = None
    cnic: str | None = None
    sTaxRegNo: str | None = None
    loyaltyNo: str | None = None
    dueDays: int
    creditAllowed: bool
    creditLimit: Money
    creditBalance: Money
    tier: str
    active: bool


class PartyCreate(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    area: str | None = None
    contactPerson: str | None = None
    ntn: str | None = None
    cnic: str | None = None
    sTaxRegNo: str | None = None
    loyaltyNo: str | None = None
    dueDays: int = 0
    creditAllowed: bool = False
    creditLimit: Decimal = Decimal("0")
    tier: str = "retail"


class PartyUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    area: str | None = None
    contactPerson: str | None = None
    ntn: str | None = None
    cnic: str | None = None
    sTaxRegNo: str | None = None
    loyaltyNo: str | None = None
    dueDays: int | None = None
    creditAllowed: bool | None = None
    creditLimit: Decimal | None = None
    creditBalance: Decimal | None = None
    tier: str | None = None
    active: bool | None = None
