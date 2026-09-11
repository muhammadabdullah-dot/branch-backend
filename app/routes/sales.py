from fastapi import APIRouter, Depends

from app.controllers import returns_controller, sales_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.sales import NextInvoiceNumberOut, ReturnCreateRequest, ReturnRecordOut, SaleCreateRequest, SaleRecordOut

router = APIRouter(tags=["sales"])

_read = require_permission("store.billing", "R")
_write = require_permission("store.billing", "W")
_returns_write = require_permission("store.returns", "W")


@router.get("/sales/next-invoice-number", response_model=NextInvoiceNumberOut)
async def next_invoice_number(user: User = Depends(_read)) -> NextInvoiceNumberOut:
    return await sales_controller.next_invoice_number()


@router.post("/sales", response_model=SaleRecordOut)
async def create_sale(payload: SaleCreateRequest, user: User = Depends(_write)) -> SaleRecordOut:
    return await sales_controller.create(user, payload)


@router.get("/sales/{invoice_number}", response_model=SaleRecordOut)
async def get_sale(invoice_number: str, user: User = Depends(_read)) -> SaleRecordOut:
    return await sales_controller.get_by_invoice(invoice_number)


@router.post("/returns", response_model=ReturnRecordOut)
async def create_return(payload: ReturnCreateRequest, user: User = Depends(_returns_write)) -> ReturnRecordOut:
    return await returns_controller.create(user, payload)
