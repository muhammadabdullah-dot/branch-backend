from fastapi import HTTPException, status

from app.models import GiftVoucher
from app.schemas.gift_vouchers import GiftVoucherIssueRequest, GiftVoucherListOut, GiftVoucherOut, GiftVoucherRedeemRequest
from app.services import gift_voucher_service


def _to_out(v: GiftVoucher) -> GiftVoucherOut:
    return GiftVoucherOut(
        id=str(v.id), code=v.code, faceValue=v.face_value, balance=v.balance,
        issuedToName=v.issued_to_name, issuedAt=v.issued_at, expiresAt=v.expires_at, status=v.status,
    )


async def find(code: str) -> GiftVoucherOut:
    voucher = await gift_voucher_service.find(code)
    if not voucher:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voucher not found")
    return _to_out(voucher)


async def list_all(limit: int, offset: int) -> GiftVoucherListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    vouchers, total = await gift_voucher_service.list_all(limit, offset)
    return GiftVoucherListOut(items=[_to_out(v) for v in vouchers], total=total)


async def issue(data: GiftVoucherIssueRequest) -> GiftVoucherOut:
    return _to_out(await gift_voucher_service.issue(data.faceValue, data.issuedToName))


async def redeem(code: str, data: GiftVoucherRedeemRequest) -> GiftVoucherOut:
    try:
        voucher = await gift_voucher_service.redeem(code, data.amount, data.invoiceNumber)
    except gift_voucher_service.VoucherError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _to_out(voucher)
