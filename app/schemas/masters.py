from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.types import Money


class ListEntryOut(BaseModel):
    id: str
    kind: str
    # Item lists: the exact text on the records. Reasons: the code records store.
    code: str
    name: str
    active: bool
    builtin: bool = False
    # Adjustment reasons: "add" or "remove".
    effect: str | None = None
    # Records using it now: Items, Parties, adjustments, returns.
    uses: int = 0
    # After a rename or merge: how many records were changed.
    moved: int | None = None
    updatedAt: datetime | None = None
    updatedBy: str | None = None


class ListSummaryOut(BaseModel):
    kind: str
    total: int
    switchedOff: int


class ItemListChoicesOut(BaseModel):
    """Switched-on values, by list: what the Item and Party forms offer."""

    choices: dict[str, list[str]]


class ListEntryCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=160)


class ListEntryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    active: bool | None = None
    # Renaming onto a name already on the list moves every record onto that entry.
    merge: bool = False


class ReasonCreate(BaseModel):
    kind: Literal["adjustment", "purchase-return", "sale-return"]
    name: str = Field(min_length=1, max_length=80)
    effect: Literal["add", "remove"] | None = None


class ReasonUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    active: bool | None = None
    effect: Literal["add", "remove"] | None = None


class PaymentMethodOut(BaseModel):
    code: str
    name: str
    kind: str
    active: bool
    sortOrder: int
    # What the counter asks for when this method is used, in plain words.
    rules: str
    canSwitchOff: bool


class BankAccountOut(BaseModel):
    id: str
    name: str
    bankName: str | None = None
    accountNo: str | None = None


class PaymentMethodsOut(BaseModel):
    methods: list[PaymentMethodOut]
    bankAccounts: list[BankAccountOut]


class PaymentMethodUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    active: bool | None = None
    sortOrder: int | None = Field(default=None, ge=0, le=99)


class ReceiptSettingsIn(BaseModel):
    # Printed at the top instead of the branch name, when set ("D.Marina Pharmacy and Mart").
    businessName: str | None = Field(default=None, max_length=80)
    ntn: str | None = Field(default=None, max_length=40)
    strn: str | None = Field(default=None, max_length=40)
    # A line under the address: opening hours, a website, a delivery number.
    headerNote: str | None = Field(default=None, max_length=120)
    footerMessage: str | None = Field(default=None, max_length=120)
    returnPolicy: str | None = Field(default=None, max_length=300)


class ReceiptSettingsOut(ReceiptSettingsIn):
    branchName: str | None = None
    branchAddress: str | None = None
    branchPhone: str | None = None
    updatedAt: datetime | None = None
    updatedBy: str | None = None


class VoucherRulesIn(BaseModel):
    validityDays: int = Field(default=180, ge=1, le=3650)
    minValue: Decimal = Field(default=Decimal("100"), ge=0, le=10_000_000)
    maxValue: Decimal | None = Field(default=None, gt=0, le=10_000_000)


class VoucherRulesOut(BaseModel):
    validityDays: int
    minValue: Money
    maxValue: Money | None = None
    updatedAt: datetime | None = None
    updatedBy: str | None = None


class PricingStockIn(BaseModel):
    # Taken off the sale price on wholesale bills, for Items without their own wholesale price.
    wholesaleDiscountPercent: Decimal = Field(default=Decimal("7"), ge=0, le=100)
    # An Item without its own reorder level is low stock when fewer than this are left.
    lowStockLevel: Decimal = Field(default=Decimal("20"), ge=0, le=1_000_000)


class PricingStockOut(PricingStockIn):
    updatedAt: datetime | None = None
    updatedBy: str | None = None
