from fastapi import HTTPException, status

from app.models import User
from app.schemas.sales import ReturnCreateRequest, ReturnRecordOut
from app.services import returns_service


async def create(user: User, payload: ReturnCreateRequest) -> ReturnRecordOut:
    try:
        record = await returns_service.create_return(user, payload)
    except returns_service.ReturnError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return ReturnRecordOut(
        id=str(record.id), against=record.against.invoice_number, at=record.at,
        cashierId=str(record.cashier_id), refundTotal=record.refund_total,
    )
