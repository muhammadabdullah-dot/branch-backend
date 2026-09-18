from datetime import datetime

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.controllers import returns_controller, sales_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.sales import (
    DiscountApprovalOut,
    DiscountApprovalRequest,
    NextInvoiceNumberOut,
    PaymentProofOut,
    ReceiptReprintOut,
    ReturnCreateRequest,
    ReturnQuoteRequest,
    ReturnRecordOut,
    SaleCreateRequest,
    SaleListOut,
    SaleRecordOut,
)

router = APIRouter(tags=["sales"])

_read = require_permission("store.billing", "R")
_write = require_permission("store.billing", "W")
_returns_write = require_permission("store.returns", "W")
# Returns.tsx looks up the original invoice before refunding it: a user granted only
# store.returns (a valid, real grant under per-user RBAC) needs to be able to do that
# without also being granted store.billing.
_lookup_for_sale_or_return = require_any_permission(("store.billing", "R"), ("store.returns", "R"))
# Branch-wide sales list: read by X/Z, the Dashboard, and Reports, none of which imply
# store.billing on their own (a Sales Manager reading X/Z, or a Branch Manager reading
# their own Dashboard, may legitimately lack store.billing).
_list_read = require_any_permission(("store.xz", "R"), ("reports", "R"), ("branch-console.dashboard", "R"))
# Printing a bill again: its own tick, because a second copy of a paid bill is what a refund fraud starts from.
_reprint = require_permission("store.reprint", "X")


@router.get("/sales/next-invoice-number", response_model=NextInvoiceNumberOut)
async def next_invoice_number(user: User = Depends(_read)) -> NextInvoiceNumberOut:
    return await sales_controller.next_invoice_number()


@router.get("/sales", response_model=SaleListOut)
async def list_sales(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
    user: User = Depends(_list_read),
) -> SaleListOut:
    return await sales_controller.list_sales(from_, to, limit, offset)


@router.post("/sales", response_model=SaleRecordOut)
async def create_sale(payload: SaleCreateRequest, user: User = Depends(_write)) -> SaleRecordOut:
    return await sales_controller.create(user, payload)


# The customer's screenshot of a bank transfer, uploaded before the sale is saved; the sale refers to it.
@router.post("/sales/payment-proofs", response_model=PaymentProofOut)
async def upload_payment_proof(file: UploadFile = File(...), user: User = Depends(_write)) -> PaymentProofOut:
    return await sales_controller.upload_proof(await file.read())


@router.get("/sales/{invoice_number}/tenders/{code}/proof")
async def payment_proof(
    invoice_number: str, code: str,
    user: User = Depends(require_any_permission(("store.billing", "R"), ("store.xz", "R"), ("reports", "R"), ("branch-console.members", "R"))),
):
    return await sales_controller.proof_file(invoice_number, code)


# The salesperson asks, from the bill they're ringing up; the approver is whoever signs in here
# (or the salesperson, if they hold store.discount-override themselves). Gated on store.billing W
# like the sale it approves: the approver's authority is the service's check, not the route's.
@router.post("/sales/discount-approvals", response_model=DiscountApprovalOut)
async def request_discount_approval(
    payload: DiscountApprovalRequest, user: User = Depends(_write)
) -> DiscountApprovalOut:
    return await sales_controller.request_discount_approval(user, payload)


@router.get("/sales/{invoice_number}", response_model=SaleRecordOut)
async def get_sale(invoice_number: str, user: User = Depends(_lookup_for_sale_or_return)) -> SaleRecordOut:
    return await sales_controller.get_by_invoice(invoice_number)


# Looking first (the preview) isn't a reprint; the POST that sends it to the printer is, and the activity log records
# it with who did it. Both need the reprint tick and nothing else, so it works from Branch figures and from Returns.
@router.get("/sales/{invoice_number}/reprint", response_model=ReceiptReprintOut)
async def reprint_preview(invoice_number: str, user: User = Depends(_reprint)) -> ReceiptReprintOut:
    return await sales_controller.reprint(invoice_number, user)


@router.post("/sales/{invoice_number}/reprint", response_model=ReceiptReprintOut)
async def reprint_receipt(invoice_number: str, user: User = Depends(_reprint)) -> ReceiptReprintOut:
    return await sales_controller.reprint(invoice_number, user)


@router.post("/returns/quote")
async def quote_return(payload: ReturnQuoteRequest, user: User = Depends(_returns_write)) -> dict:
    return await returns_controller.quote(payload)


@router.post("/returns", response_model=ReturnRecordOut)
async def create_return(payload: ReturnCreateRequest, user: User = Depends(_returns_write)) -> ReturnRecordOut:
    return await returns_controller.create(user, payload)


# Sold without stock, Priced below cost, the pharmacy setting, scan history and recalling a held bill: their own file,
# brought in here so app/main.py needs no line for them.
from app.routes.billing_extras import router as _billing_extras_router  # noqa: E402

router.include_router(_billing_extras_router)
