from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.types import Money, Percent, Qty


class SaleLineIn(BaseModel):
    productId: str
    # Always pieces. A pack or box line says so below, and the server works the pieces out from it.
    qty: Decimal
    unitPrice: Decimal
    isReturn: bool = False
    # The alternate (pack) barcode scanned for this line, if any: its pack discount applies instead of
    # the Item's own.
    aliasCode: str | None = None
    # Sold in other amounts than the stocked unit: which ("piece" or "strip" loose, "pack" or "box" together), how many,
    # and the price of one (services/sell_levels.py). The server works `qty` out from them.
    level: Literal["piece", "strip", "pack", "box"] | None = None
    levelQty: Decimal | None = None
    levelPrice: Decimal | None = None
    # A return line: the bill number its goods came from. Required on a return line.
    returnOf: str | None = Field(default=None, max_length=30)


class MemberIn(BaseModel):
    """Who the bill is for. `code` picks an existing member; otherwise `name` and `phone` find the member
    by number or sign them up. Card and online payments always need one; for anything else `join` says the
    customer chose to become a member."""
    code: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    join: bool = False


class TenderDetailIn(BaseModel):
    # Card: last 4 digits. Easypaisa / JazzCash: the paying account's number.
    reference: str | None = Field(default=None, max_length=40)
    # A transfer's transaction ID — required for a bank transfer.
    transactionId: str | None = Field(default=None, max_length=60)
    # The shop account a bank transfer went into.
    account: str | None = Field(default=None, max_length=80)
    # What POST /sales/payment-proofs returned for the customer's screenshot.
    proofId: str | None = Field(default=None, max_length=160)


class SaleCreateRequest(BaseModel):
    partyId: str | None = None
    member: MemberIn | None = None
    # Keyed by tender code, for the tenders that carry details (CARD, EASYPAISA, JAZZCASH, BANK).
    tenderDetails: dict[str, TenderDetailIn] = {}
    lines: list[SaleLineIn]
    discPercent: Decimal = Decimal("0")
    flatDisc: Decimal = Decimal("0")
    fare: Decimal = Decimal("0")
    tenders: dict[str, Decimal]
    voucherCode: str | None = None
    # Needed only for a discount above salesperson authority: the signed approval from
    # POST /sales/discount-approvals, issued for this salesperson and this bill's clientRequestId.
    discountApprovalToken: str | None = None
    # Optional client-generated idempotency key — a retried request with the same key returns
    # the already-committed sale instead of creating a second one. Omit for the old behavior.
    # Required in practice for an above-authority discount, since the approval is bound to it.
    clientRequestId: str | None = None
    # For someone finishing a bill held by the other side (a Salesperson a Pharmacist's, or the other way round): the pass
    # recalling it handed them, for this bill's clientRequestId (services/pharmacy_service.py).
    pharmacyPass: str | None = Field(default=None, max_length=4000)


class DiscountApprovalRequest(BaseModel):
    # The bill's clientRequestId. Only one sale can commit under it, so one approval covers one sale.
    billId: str = Field(min_length=1, max_length=80)
    # The most total effective discount being approved, any flat discount included.
    maxPercent: Decimal = Field(gt=0, le=100)
    # The manager signing in at the till. Left out when the salesperson holds the authority themselves.
    email: str | None = None
    password: str | None = None


class DiscountApprovalOut(BaseModel):
    token: str
    approverId: str
    approverName: str
    maxPercent: Percent
    expiresAt: datetime
    # Relative, so the till can time the approval out on its own clock rather than trusting that it
    # agrees with the server's.
    expiresInSeconds: int


class SaleLineOut(BaseModel):
    productId: str
    name: str
    sku: str
    qty: Qty
    unitPrice: Money
    isWeighed: bool
    isReturn: bool
    # Discount taken off the line (Item discount + share of the bill discount). Null on older sales.
    discAmount: Money | None = None
    # The pack barcode the line was rung up by, so a held bill recalls with its pack discount.
    aliasCode: str | None = None
    # Sold in other amounts than the stocked unit: which, how many, the price of one, and what one is ("tablet", "10 x 12").
    level: Literal["piece", "strip", "pack", "box"] | None = None
    levelQty: Qty | None = None
    levelPrice: Money | None = None
    levelDetail: str | None = None
    # A return line: the bill its goods came from.
    returnOf: str | None = None
    # One line standing in for a bill's Pharmacy Items, for someone who doesn't sell them: "Pharmacy slip P-0042 · 3 items
    # · Rs 743", never the Items (services/pharmacy_service.py fold_sale_lines). It can't be returned from here.
    folded: bool = False


class SaleTenderOut(BaseModel):
    code: str
    name: str
    amount: Money
    reference: str | None = None
    transactionId: str | None = None
    account: str | None = None
    hasProof: bool = False


class SaleSlipOut(BaseModel):
    """A pharmacy slip this bill paid, and the Pharmacist who made it."""
    number: str
    pharmacistId: str | None = None
    pharmacistName: str | None = None


class SaleRecordOut(BaseModel):
    id: str
    invoiceNumber: str
    at: datetime
    cashierId: str
    partyId: str
    partyName: str
    lines: list[SaleLineOut]
    gross: Money
    discTotal: Money
    fare: Money
    gst: Money
    grandTotal: Money
    netValue: Money
    discountOverrideBy: str | None = None
    earnedPoints: int
    memberCode: str | None = None
    memberName: str | None = None
    pointsRedeemed: int = 0
    # The member's balance now, for the receipt.
    memberPoints: int | None = None
    tenders: list[SaleTenderOut]
    received: Money
    cashBack: Money
    isCreditSale: bool
    fbrInvoiceNumber: str
    # The pharmacy slip this bill is the payment of (earlier bills could carry several). Empty on most bills.
    slips: list[SaleSlipOut] = []


class ReceiptReprintOut(BaseModel):
    """A bill made earlier, ready to print again, marked as a reprint."""
    sale: SaleRecordOut
    # Who rang it and on which till, as the first print said.
    cashierName: str | None = None
    tillLabel: str | None = None
    reprintedAt: datetime
    reprintedBy: str
    # 1 for the first reprint of this bill, 2 for the second…
    copyNumber: int


class ReturnLineIn(BaseModel):
    productId: str
    qty: Decimal
    # Ignored: the refund is priced from the bill. Kept so older screens still send a valid request.
    unitPrice: Decimal = Decimal("0")


class ReturnCreateRequest(BaseModel):
    against: str
    lines: list[ReturnLineIn]
    # CASH · CREDIT (off the customer's balance) · CARD · BANK · EASYPAISA · JAZZCASH. Empty: credit bills go
    # back off the balance, everything else as cash.
    refundMethod: str | None = None
    refundReference: str | None = None
    # Why it came back: a code from the branch's customer return reasons. Optional.
    reason: str | None = Field(default=None, max_length=40)
    # Needed with the reason Other: what happened, 5 to 20 characters.
    remark: str | None = Field(default=None, max_length=60)


class ReturnQuoteRequest(BaseModel):
    against: str
    lines: list[ReturnLineIn]


class ReturnRecordOut(BaseModel):
    id: str
    against: str
    at: datetime
    cashierId: str
    refundTotal: Money
    refundMethod: str = "CASH"
    taxTotal: Money | None = None
    reason: str | None = None
    reasonLabel: str | None = None
    # The remark typed with the reason Other, the number on the return receipt, and refund, replace or exchange.
    remark: str | None = None
    number: str | None = None
    kind: str | None = None


class PaymentProofOut(BaseModel):
    proofId: str


class NextInvoiceNumberOut(BaseModel):
    invoiceNumber: str


class SaleListOut(BaseModel):
    items: list[SaleRecordOut]
    total: int
