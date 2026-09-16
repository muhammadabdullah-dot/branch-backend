from datetime import datetime

from fastapi import HTTPException, status

from app.models import GiftVoucher
from app.schemas.gift_vouchers import (
    GiftVoucherCheckOut, GiftVoucherIssueRequest, GiftVoucherListOut, GiftVoucherOut, GiftVoucherRedeemRequest,
)
from app.services import gift_voucher_service


async def _to_out(v: GiftVoucher) -> GiftVoucherOut:
    # Ownership comes from the same resolver the till's refusal uses, so the screen can never say
    # "belongs to Ali Traders" about a voucher the till would let anyone spend — or the reverse.
    owner, unresolved = await gift_voucher_service.owner_of(v)
    return GiftVoucherOut(
        id=str(v.id), code=v.code, faceValue=v.face_value, balance=v.balance,
        issuedToName=v.issued_to_name, issuedAt=v.issued_at, expiresAt=v.expires_at, status=v.status,
        partyId=str(v.party_id) if v.party_id else None,
        partyCode=owner.code if owner and v.party_id else None,
        ownership="unresolved" if unresolved else "customer" if owner else "open",
        ownerName=owner.name if owner else v.issued_to_name if unresolved else None,
        ownerCode=owner.code if owner else None,
        paidBy=v.paid_by, issuedBy=v.issued_by_name,
    )


async def _get(code: str) -> GiftVoucher:
    voucher = await gift_voucher_service.find(code)
    if not voucher:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voucher not found")
    return voucher


async def find(code: str) -> GiftVoucherOut:
    return await _to_out(await _get(code))


async def check(code: str, party_id: str | None) -> GiftVoucherCheckOut:
    """`party_id` is the bill's customer; None is a walk-in bill."""
    voucher = await _get(code)
    refusal = await gift_voucher_service.refusal_for(voucher, party_id)
    return GiftVoucherCheckOut(voucher=await _to_out(voucher), refusal=refusal)


async def list_all(
    limit: int, offset: int, from_at: datetime | None = None, to_at: datetime | None = None,
) -> GiftVoucherListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    vouchers, total = await gift_voucher_service.list_all(limit, offset, from_at, to_at)
    return GiftVoucherListOut(items=[await _to_out(v) for v in vouchers], total=total)


async def issue(data: GiftVoucherIssueRequest, user=None) -> GiftVoucherOut:
    try:
        voucher = await gift_voucher_service.issue(data.faceValue, data.partyId, data.paidBy, data.paymentReference, user)
    except gift_voucher_service.VoucherError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return await _to_out(voucher)


async def redeem(code: str, data: GiftVoucherRedeemRequest) -> GiftVoucherOut:
    try:
        voucher = await gift_voucher_service.redeem(code, data.amount, data.invoiceNumber, data.partyId)
    except gift_voucher_service.VoucherError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return await _to_out(voucher)
