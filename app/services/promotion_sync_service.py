"""A campaign arriving from head office, and what it gave away going back.

A branch never writes a campaign. It applies what it was sent and reports what it sold under it, which is the same
arrangement the company supplier list already has, and the same one the old software had by accident: its campaign
tables carry no branch at all, so one list reached every till because every till read one database.

Two guards, both of which matter on a line that can be slow or out of order:

  * **A message older than what is already here is ignored.** Head office numbers every send (`rev`), so a message
    that overtakes another can never undo it. The supplier list is guarded the same way.
  * **A campaign for an Item this branch does not have is kept, not refused.** The Item list and the campaign list
    travel separately, so a campaign can arrive first. It simply gives nothing until the Item turns up, which is
    better than losing it and better than blocking the queue behind it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models import Product, Promotion

ZERO = Decimal("0")


def _decimal(value, fallback=ZERO) -> Decimal:
    if value in (None, ""):
        return fallback
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return fallback


def _maybe_decimal(value) -> Decimal | None:
    return None if value in (None, "") else _decimal(value)


async def apply_upsert(payload: dict) -> str:
    """Head office's campaign, as this branch will sell under it."""
    data = (payload or {}).get("promotion") or {}
    code = (data.get("code") or "").strip().upper()
    if not code:
        return "a campaign with no code"
    promo_id = str(data.get("id") or "").strip() or code
    rev = int(data.get("rev") or 0)

    existing = await Promotion.get_or_none(id=promo_id) or await Promotion.get_or_none(code=code)
    if existing is not None and (existing.rev or 0) > rev:
        return f"{code} ignored: this branch already has a newer one (rev {existing.rev} against {rev})"

    sku = (data.get("productSku") or "").strip()
    product = await Product.get_or_none(id=sku) or await Product.get_or_none(sku=sku)
    if product is None:
        # Kept rather than refused: the Item may be on its way. Until it lands the campaign matches no line, and
        # promotions_service only ever reads campaigns by product, so nothing sells under it by accident.
        return f"{code} held: this branch has no Item {sku} yet"

    columns = {
        "code": code, "name": (data.get("name") or code)[:160], "product": product,
        "starts_on": date.fromisoformat(data["startsOn"]), "ends_on": date.fromisoformat(data["endsOn"]),
        "kind": (data.get("kind") or "percent"),
        "disc_percent": _decimal(data.get("discPercent")), "disc_flat": _decimal(data.get("discFlat")),
        "promo_price": _maybe_decimal(data.get("promoPrice")),
        "min_qty": _decimal(data.get("minQty"), Decimal("1")), "bonus_qty": _decimal(data.get("bonusQty")),
        "qty_limit": _maybe_decimal(data.get("qtyLimit")), "amount_limit": _maybe_decimal(data.get("amountLimit")),
        "active": bool(data.get("active", True)),
        "remarks": (data.get("remarks") or None), "rev": rev,
    }
    if existing is None:
        # What it has already given stays at zero on a campaign this branch is meeting for the first time. The limit
        # is the company's, and head office knows the running total from what every branch reports.
        await Promotion.create(id=promo_id, **columns)
        return f"{code} added, {'running' if columns['active'] else 'switched off'}"
    for column, value in columns.items():
        setattr(existing, column, value)
    await existing.save()
    return f"{code} updated, {'running' if columns['active'] else 'switched off'}"
