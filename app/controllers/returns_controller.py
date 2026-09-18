from datetime import datetime

from fastapi import HTTPException, status

from app.controllers.sales_controller import _sale_out
from app.models import User
from app.schemas.returns import ExchangeOut, ExchangeRequest, ReturnListItemOut, ReturnReceiptOut, ReturnWindowsIn, ReturnWindowsOut
from app.schemas.sales import ReturnCreateRequest, ReturnQuoteRequest, ReturnRecordOut
from app.services import masters_service, return_window_service, returns_service
from app.schemas.types import money_str


def _fail(exc: returns_service.ReturnError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


async def _record_out(record) -> ReturnRecordOut:
    reasons = await masters_service.reason_entries("sale-return") if record.reason else {}
    return ReturnRecordOut(
        id=str(record.id), against=record.against.invoice_number, at=record.at,
        cashierId=str(record.cashier_id), refundTotal=record.refund_total, refundMethod=record.refund_method,
        taxTotal=record.tax_total, reason=record.reason,
        reasonLabel=reasons[record.reason].name if record.reason in reasons else record.reason,
        remark=record.note if record.reason else None, number=returns_service.shown_number(record), kind=record.kind or "refund",
    )


async def create(user: User, payload: ReturnCreateRequest) -> ReturnRecordOut:
    try:
        record = await returns_service.create_return(user, payload)
    except returns_service.ReturnError as exc:
        raise _fail(exc)
    return await _record_out(record)


async def quote(payload: ReturnQuoteRequest, user: User | None = None) -> dict:
    merged: dict = {}
    for line in payload.lines:
        merged[line.productId] = merged.get(line.productId, 0) + line.qty
    try:
        if user is not None:
            await returns_service.refuse_pharmacy_for(user, payload.against, merged)
        priced = await returns_service.quote(payload.against, list(merged.items()))
    except returns_service.ReturnError as exc:
        raise _fail(exc)
    sale = priced["sale"]
    return {
        "against": sale.invoice_number, "partyName": sale.party.name, "walkIn": sale.party.is_walk_in,
        "creditCustomer": bool(sale.party.credit_allowed), "partyBalance": money_str(sale.party.credit_balance),
        "lines": [{"productId": l["product"].id, "name": l["product"].name, "qty": money_str(l["qty"]), "unitPrice": money_str(l["unitPrice"]),
                   "taxAmount": money_str(l["taxAmount"]), "value": money_str(l["value"])} for l in priced["lines"]],
        "value": money_str(priced["value"]), "taxTotal": money_str(priced["taxTotal"]),
        "refundTotal": money_str(priced["refundTotal"]), "rounding": money_str(priced["rounding"]),
        "suggestedMethod": priced["suggestedMethod"], "methods": list(returns_service.REFUND_METHODS),
    }


async def quote_exchange(user: User, payload: ExchangeRequest) -> dict:
    try:
        return await returns_service.quote_exchange(user, payload)
    except returns_service.ReturnError as exc:
        raise _fail(exc)


async def exchange(user: User, payload: ExchangeRequest) -> ExchangeOut:
    try:
        record, sale = await returns_service.create_exchange(user, payload)
        out = await returns_service.receipt(str(record.id), user)
    except returns_service.ReturnError as exc:
        raise _fail(exc)
    await sale.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by", "member")
    return ExchangeOut(receipt=ReturnReceiptOut(**out), sale=await _sale_out(sale, user))


async def receipt(return_id: str, user: User | None = None) -> ReturnReceiptOut:
    try:
        return ReturnReceiptOut(**await returns_service.receipt(return_id, user))
    except returns_service.ReturnError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message)


async def reprint(return_id: str, user: User) -> ReturnReceiptOut:
    try:
        return ReturnReceiptOut(**await returns_service.reprint(return_id, user))
    except returns_service.ReturnError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message)


async def list_returns(from_at: datetime | None, to_at: datetime | None, limit: int) -> list[ReturnListItemOut]:
    return [ReturnListItemOut(**row) for row in await returns_service.list_returns(from_at, to_at, limit)]


async def windows() -> ReturnWindowsOut:
    return ReturnWindowsOut(**await return_window_service.windows())


async def save_windows(payload: ReturnWindowsIn, user: User) -> ReturnWindowsOut:
    try:
        return ReturnWindowsOut(**await return_window_service.save_windows(payload.days, user))
    except return_window_service.ReturnWindowError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
