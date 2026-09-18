from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.sales import SaleLineIn, SaleLineOut, TenderDetailIn
from app.schemas.types import Money


class HeldBillCreate(BaseModel):
    label: str
    lines: list[SaleLineOut]
    partyId: str | None = None
    # Holding a recalled bill again: the pass it came with and the bill it was for, so the lines the other side put on it
    # stay cleared for payment (services/pharmacy_service.py).
    pharmacyPass: str | None = None
    billId: str | None = None


class HeldBillOut(BaseModel):
    id: str
    label: str
    lines: list[SaleLineOut]
    partyId: str | None = None
    heldAt: datetime


class HeldBillRecallIn(BaseModel):
    # The bill on the till it is recalled into (its POST /sales clientRequestId): a pass is for that bill only.
    billId: str


class HeldBillRecallOut(BaseModel):
    bill: HeldBillOut
    # Present when the bill carries cleared lines the person recalling may not sell themselves (Pharmacy Items for a
    # Salesperson, other Items for a Pharmacist): POST /sales takes those lines, up to those quantities, with it.
    pharmacyPass: str | None = None


# ── Pharmacy slips (services/slips_service.py) ──────────────────────────────────────────────────

class SlipCreateIn(BaseModel):
    # What the Pharmacist rang up. Prices are the Items' own: the server prices every line again.
    lines: list[SaleLineIn] = Field(max_length=200)
    # The bill on the Pharmacist's screen (its clientRequestId): printing it twice makes one slip.
    billId: str | None = Field(default=None, max_length=80)


class SlipTenderOut(BaseModel):
    """How a slip was paid: a method and its amount, and the card's last 4 digits or the online payment's reference."""
    code: str
    name: str
    amount: Money
    reference: str | None = None


class SlipOut(BaseModel):
    id: str
    number: str
    # open (waiting at the cash counter), paid or cancelled. One brought onto a bill the earlier way reads as open.
    status: str
    # The Items on the slip, for the Pharmacist and the Branch Manager. Empty for anyone who doesn't sell Pharmacy Items:
    # at the cash counter a slip is an amount to collect, and `itemCount` is all they are told.
    lines: list[SaleLineOut]
    itemCount: int
    gross: Money
    discTotal: Money
    gst: Money
    # What the customer pays for it at the cash counter, to the rupee.
    total: Money
    madeById: str | None = None
    madeByName: str | None = None
    madeAt: datetime
    # Paid: the bill the payment made, when, who took it and how.
    paidInvoice: str | None = None
    paidAt: datetime | None = None
    paidByName: str | None = None
    paidTenders: list[SlipTenderOut] = []
    change: Money | None = None
    # Cancelled: by whom, when and why.
    cancelledByName: str | None = None
    cancelledAt: datetime | None = None
    reason: str | None = None
    # Why it can't be paid, in the cashier's words, when it isn't open.
    refusal: str | None = None


class SlipPayIn(BaseModel):
    # Cash, card and online only (CASH, CARD, EASYPAISA, JAZZCASH, BANK), keyed by code.
    tenders: dict[str, Decimal]
    # What card and online payments carry, as on POST /sales.
    tenderDetails: dict[str, TenderDetailIn] = {}
    # The payment's own key, minted when the dialog opens: sending it again hands back the same payment, never a second.
    clientRequestId: str = Field(min_length=8, max_length=80)


class SlipPaidOut(BaseModel):
    """What the cashier is told once a slip is paid: never its Items."""
    number: str
    invoiceNumber: str
    # What the slip came to, what was handed over, and the change to give back.
    amount: Money
    received: Money
    change: Money
    paidAt: datetime
    paidByName: str | None = None
    madeByName: str | None = None
    itemCount: int
    tenders: list[SlipTenderOut]


class SlipCancelIn(BaseModel):
    # Checked in plain words by the server (services/slips_service.py cancel).
    reason: str = Field(default="", max_length=200)
