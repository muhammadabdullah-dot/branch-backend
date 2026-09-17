from fastapi import HTTPException, status

from app.models import User
from app.schemas.sales import ReturnCreateRequest, ReturnQuoteRequest, ReturnRecordOut
from app.services import masters_service, returns_service
from app.schemas.types import money_str


async def create(user: User, payload: ReturnCreateRequest) -> ReturnRecordOut:
    try:
        record = await returns_service.create_return(user, payload)
    except returns_service.ReturnError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    reasons = await masters_service.reason_entries("sale-return") if record.reason else {}
    return ReturnRecordOut(
        id=str(record.id), against=record.against.invoice_number, at=record.at,
        cashierId=str(record.cashier_id), refundTotal=record.refund_total, refundMethod=record.refund_method,
        taxTotal=record.tax_total, reason=record.reason,
        reasonLabel=reasons[record.reason].name if record.reason in reasons else record.reason,
    )


async def quote(payload: ReturnQuoteRequest) -> dict:
    merged: dict = {}
    for line in payload.lines:
        merged[line.productId] = merged.get(line.productId, 0) + line.qty
    try:
        priced = await returns_service.quote(payload.against, list(merged.items()))
    except returns_service.ReturnError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
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
