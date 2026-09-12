from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.controllers import returns_controller, sales_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.sales import (
    NextInvoiceNumberOut,
    ReturnCreateRequest,
    ReturnRecordOut,
    SaleCreateRequest,
    SaleListOut,
    SaleRecordOut,
)

router = APIRouter(tags=["sales"])

_read = require_permission("store.billing", "R")
_write = require_permission("store.billing", "W")
_returns_write = require_permission("store.returns", "W")
# Returns.tsx looks up the original invoice before refunding it — a user granted only
# store.returns (a valid, real grant under per-user RBAC) needs to be able to do that
# without also being granted store.billing.
_lookup_for_sale_or_return = require_any_permission(("store.billing", "R"), ("store.returns", "R"))
# Branch-wide sales list — read by X/Z, the Dashboard, and Reports, none of which imply
# store.billing on their own (a Sales Manager reading X/Z, or a Branch Manager reading
# their own Dashboard, may legitimately lack store.billing).
_list_read = require_any_permission(("store.xz", "R"), ("reports", "R"), ("branch-console.dashboard", "R"))


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


@router.get("/sales/{invoice_number}", response_model=SaleRecordOut)
async def get_sale(invoice_number: str, user: User = Depends(_lookup_for_sale_or_return)) -> SaleRecordOut:
    return await sales_controller.get_by_invoice(invoice_number)


@router.post("/returns", response_model=ReturnRecordOut)
async def create_return(payload: ReturnCreateRequest, user: User = Depends(_returns_write)) -> ReturnRecordOut:
    return await returns_controller.create(user, payload)
