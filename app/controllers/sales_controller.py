from datetime import datetime

from fastapi import HTTPException, status

from app.models import PaymentMethod, SaleRecord, User
from app.schemas.sales import (
    NextInvoiceNumberOut,
    SaleCreateRequest,
    SaleLineOut,
    SaleListOut,
    SaleRecordOut,
    SaleTenderOut,
)
from app.services import sales_service


async def _sale_out(sale: SaleRecord) -> SaleRecordOut:
    method_names = {m.code: m.name for m in await PaymentMethod.all()}
    return SaleRecordOut(
        id=str(sale.id), invoiceNumber=sale.invoice_number, at=sale.at,
        cashierId=str(sale.cashier_id), partyId=str(sale.party_id), partyName=sale.party.name,
        lines=[
            SaleLineOut(
                productId=str(l.product_id), name=l.product.name, sku=l.product.sku,
                qty=l.qty, unitPrice=l.unit_price, isWeighed=l.product.is_weighed, isReturn=l.is_return,
            )
            for l in sale.lines
        ],
        gross=sale.gross, discTotal=sale.disc_total, fare=sale.fare, gst=sale.gst,
        grandTotal=sale.grand_total, netValue=sale.net_value,
        discountOverrideBy=(sale.discount_override_by.name if sale.discount_override_by else None),
        earnedPoints=sale.earned_points,
        tenders=[SaleTenderOut(code=t.code, name=method_names.get(t.code, t.code), amount=t.amount) for t in sale.tenders],
        received=sale.received, cashBack=sale.cash_back, isCreditSale=sale.is_credit_sale,
        fbrInvoiceNumber=sale.fbr_invoice_number,
    )


async def next_invoice_number() -> NextInvoiceNumberOut:
    return NextInvoiceNumberOut(invoiceNumber=await sales_service.peek_next_invoice_number())


async def create(user: User, payload: SaleCreateRequest) -> SaleRecordOut:
    try:
        sale = await sales_service.create_sale(user, payload)
    except sales_service.SaleError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return await _sale_out(sale)


async def get_by_invoice(invoice_number: str) -> SaleRecordOut:
    sale = await sales_service.find_by_invoice(invoice_number)
    if not sale:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found")
    return await _sale_out(sale)


async def list_sales(from_at: datetime | None, to_at: datetime | None, limit: int, offset: int) -> SaleListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    sales, total = await sales_service.list_sales(from_at, to_at, limit, offset)
    return SaleListOut(items=[await _sale_out(s) for s in sales], total=total)
