import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.models import GiftVoucher, VoucherRedemption


class VoucherError(Exception):
    def __init__(self, message: str):
        self.message = message


async def find(code: str) -> GiftVoucher | None:
    return await GiftVoucher.get_or_none(code=code.strip().upper())


@atomic()
async def issue(face_value: Decimal, issued_to_name: str | None) -> GiftVoucher:
    code = f"GV-{random.randint(10000, 99999)}"
    return await GiftVoucher.create(
        code=code,
        face_value=face_value,
        balance=face_value,
        issued_to_name=issued_to_name,
        expires_at=datetime.now(timezone.utc) + timedelta(days=180),
    )


@atomic()
async def redeem(code: str, amount: Decimal, invoice_number: str) -> GiftVoucher:
    voucher = await GiftVoucher.get_or_none(code=code.strip().upper(), status="active")
    if not voucher or voucher.balance < amount:
        raise VoucherError("Voucher not found, inactive, or insufficient balance")
    voucher.balance -= amount
    if voucher.balance <= 0:
        voucher.status = "redeemed"
    await voucher.save()
    await VoucherRedemption.create(voucher=voucher, invoice_number=invoice_number, amount=amount)
    return voucher
