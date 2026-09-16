from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.types import Money

PriceTier = Literal["retail", "wholesale"]


class PartyOut(BaseModel):
    id: str
    code: str
    name: str
    isWalkIn: bool
    phone: str | None = None
    telephone: str | None = None
    fax: str | None = None
    email: str | None = None
    address: str | None = None
    address2: str | None = None
    city: str | None = None
    area: str | None = None
    subArea: str | None = None
    category: str | None = None
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
    hasPicture: bool = False
    active: bool


class PartyContactIn(BaseModel):
    cellNo: str | None = Field(default=None, max_length=30)
    contactPerson: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=180)
    officeAddress: str | None = Field(default=None, max_length=255)
    resAddress: str | None = Field(default=None, max_length=255)
    remarks: str | None = Field(default=None, max_length=255)


class PartyContactOut(PartyContactIn):
    id: str


class PartyDetailOut(PartyOut):
    contacts: list[PartyContactOut] = []


class _PartyFields(BaseModel):
    phone: str | None = Field(default=None, max_length=30)
    telephone: str | None = Field(default=None, max_length=30)
    fax: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=180)
    address: str | None = Field(default=None, max_length=255)
    address2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=80)
    area: str | None = Field(default=None, max_length=120)
    subArea: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    contactPerson: str | None = Field(default=None, max_length=120)
    ntn: str | None = Field(default=None, max_length=40)
    cnic: str | None = Field(default=None, max_length=40)
    sTaxRegNo: str | None = Field(default=None, max_length=40)
    loyaltyNo: str | None = Field(default=None, max_length=40)


class PartyCreate(_PartyFields):
    name: str = Field(min_length=1, max_length=160)
    dueDays: int = Field(default=0, ge=0, le=365)
    creditAllowed: bool = False
    creditLimit: Decimal = Field(default=Decimal("0"), ge=0)
    tier: PriceTier = "retail"


class PartyUpdate(_PartyFields):
    """Only fields sent change. There is deliberately no `creditBalance`: what a customer owes moves
    with their credit sales, and a box on a form that overwrites it would let anyone wipe a debt."""
    name: str | None = Field(default=None, min_length=1, max_length=160)
    dueDays: int | None = Field(default=None, ge=0, le=365)
    creditAllowed: bool | None = None
    creditLimit: Decimal | None = Field(default=None, ge=0)
    tier: PriceTier | None = None
    active: bool | None = None
