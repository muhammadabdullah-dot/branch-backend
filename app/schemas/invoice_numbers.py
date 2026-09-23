"""Invoice numbers: how the branch numbers its bills and returns (services/invoice_numbers_service.py)."""
from datetime import datetime

from pydantic import BaseModel, Field


class InvoiceNumberingOut(BaseModel):
    billPrefix: str
    yearInNumber: bool
    digits: int
    returnPrefix: str


class InvoiceNumbersOut(InvoiceNumberingOut):
    branchCode: str
    # Whether head office has issued the branch its code yet: until then nothing here can be changed.
    registered: bool
    # The Pakistan year the next numbers carry.
    year: int
    # The running number the next bill will most likely get, the lowest one its series allows (one past the highest
    # used), and the next bill and return as they will print.
    nextNumber: int
    lowestNext: int
    nextBill: str
    nextReturn: str
    nextReturnNumber: int
    defaults: InvoiceNumberingOut
    # Whether a Branch Manager has saved the setting (otherwise the defaults stand).
    saved: bool
    updatedAt: datetime | None = None
    updatedBy: str | None = None
    # Only a Branch Manager changes it; everyone else who can see the receipt settings sees it read-only.
    canChange: bool


class InvoiceNumbersIn(BaseModel):
    billPrefix: str = Field(max_length=40)
    yearInNumber: bool = True
    digits: int | None = None
    # The next bill's running number in the bill prefix's series for this year. Left out: the series carries on where
    # it is (a new series from 1).
    nextNumber: int | None = None
    returnPrefix: str = Field(max_length=40)


class InvoiceNumbersPreviewOut(BaseModel):
    """What a proposed setting would do, and what is wrong with it: any problem refuses the save (the first is the one
    the save says)."""
    year: int
    nextBill: str | None = None
    nextReturn: str | None = None
    nextNumber: int | None = None
    lowestNext: int
    nextReturnNumber: int | None = None
    problems: list[str]
    notes: list[str]
