"""Reverse a sale that was paid with someone else's gift voucher.

    python -m scripts.reverse_voucher_sale MT-2026-000760 --by branchmanager@branch.dmarina.pk
    python -m scripts.reverse_voucher_sale MT-2026-000760 --by branchmanager@branch.dmarina.pk --yes

Before the voucher ownership check existed, a customer's voucher could be spent on another
customer's bill. This undoes one such sale completely through `returns_service.reverse_voucher_sale`:
the stock goes back on the shelf, the amount goes back onto the voucher, and a return refunded to the
voucher (not cash) is recorded against the invoice, so the till and the cash figures don't move.

Without --yes it only shows what would happen. Stop the branch server and back up branch.db first.
"""
from __future__ import annotations

import argparse
import asyncio

from tortoise import Tortoise

from app.core.config import TORTOISE_ORM
from app.models import SaleRecord, User, VoucherRedemption
from app.services import gift_voucher_service, returns_service


async def main(invoice: str, by: str, yes: bool) -> None:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        sale = await SaleRecord.get_or_none(invoice_number=invoice.upper()).prefetch_related("party", "tenders", "lines__product")
        if not sale:
            raise SystemExit(f"No invoice {invoice}")
        user = await User.get_or_none(email=by.lower())
        if not user:
            raise SystemExit(f"No user {by}")
        redemption = await VoucherRedemption.filter(invoice_number=sale.invoice_number).prefetch_related("voucher").first()
        voucher = redemption.voucher if redemption else None
        owner, _ = await gift_voucher_service.owner_of(voucher) if voucher else (None, None)
        print(f"Invoice   {sale.invoice_number}  {sale.at:%d %b %Y %H:%M} UTC  customer {sale.party.name} ({sale.party.code})")
        for line in sale.lines:
            print(f"  line    {line.qty.normalize():f} x {line.product.name} ({line.product.sku}) @ Rs {line.unit_price}")
        print(f"  paid    " + ", ".join(f"{t.code} Rs {t.amount}" for t in sale.tenders))
        if voucher:
            print(f"Voucher   {voucher.code}  belongs to {owner.name + ' (' + owner.code + ')' if owner else 'nobody (open)'}  balance now Rs {voucher.balance}")
        print(f"Recorded by {user.name}")
        if not yes:
            print("\nDry run — nothing changed. Add --yes to reverse it.")
            return
        reason = (
            f"Voucher {voucher.code} belongs to {owner.name if owner else 'another customer'} and was spent on "
            f"{sale.party.name}'s bill before the ownership check existed. Sale reversed: stock back on the shelf, "
            f"Rs {redemption.amount} back on the voucher."
        )
        record = await returns_service.reverse_voucher_sale(sale.invoice_number, user, reason)
        await voucher.refresh_from_db()
        print(f"\nReversed. Return {record.id} refunded Rs {record.refund_total} to {voucher.code}; its balance is now Rs {voucher.balance}.")
    except returns_service.ReturnError as exc:
        raise SystemExit(exc.message)
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("invoice")
    parser.add_argument("--by", required=True, help="email of the person the reversal is recorded against")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.invoice, args.by, args.yes))
