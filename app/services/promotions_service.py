"""What a campaign takes off a line, and which campaign it is.

A promotion never sets the price. The till refuses any price that is not the Item's own, so what a campaign does is
give a discount, and the bill shows the Item's price with the discount beneath it. A campaign written as "this Item
is 199 while it runs" becomes the discount that reaches 199 from whatever the Item costs that day, so if the price
moves the customer still pays 199 and the bill still tells the truth about both numbers.

Two rules worth stating because they are decisions, not arithmetic:

* **A campaign replaces the Item's own discount, it does not stack with it.** An Item already marked 10% off and a
  campaign at 15% off sells at 15% off, not 23.5%. Stacking is how a shop gives away more than anyone decided to.
* **Where two campaigns cover one Item on one day, the customer gets the better of them.** The old software allows
  the overlap and says nothing about which wins; a shop asked in front of a customer would give the better one.

The floor is not this module's business: whatever comes off here still goes through the bill's own discount floor
(services/sale_rules.py), so a campaign cannot sell below what the goods cost the shop without somebody deciding to.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.core.pk_time import pk_day
from app.models import Promotion

ZERO = Decimal("0")
HUNDRED = Decimal("100")


async def for_products(product_ids: list[str], day: date | None = None) -> dict[str, list[Promotion]]:
    """Every campaign running on `day` for these Items, so a bill reads them once rather than once a line."""
    if not product_ids:
        return {}
    on = day or pk_day()
    rows = await Promotion.filter(
        product_id__in=list(set(product_ids)), active=True, starts_on__lte=on, ends_on__gte=on,
    ).order_by("-starts_on", "code")
    out: dict[str, list[Promotion]] = {}
    for promo in rows:
        out.setdefault(promo.product_id, []).append(promo)
    return out


async def running(day: date | None = None) -> list[Promotion]:
    """Every campaign running on `day`, for the till to price a bill with before it saves one.

    The till works a bill out in the browser and the cashier collects against that figure, so it has to know the same
    campaigns this module applies on the way in. Sending the day's campaigns once, rather than asking per Item as a
    bill is rung, keeps the screen honest without a round trip per line."""
    on = day or pk_day()
    return await Promotion.filter(active=True, starts_on__lte=on, ends_on__gte=on).order_by("-starts_on", "code")


def spent_out(promo: Promotion) -> bool:
    """A campaign with a limit that has been reached gives nothing more, at any branch."""
    if promo.qty_limit is not None and (promo.used_qty or ZERO) >= promo.qty_limit:
        return True
    return promo.amount_limit is not None and (promo.used_amount or ZERO) >= promo.amount_limit


def discount(promo: Promotion, unit_price: Decimal, qty: Decimal, gross: Decimal) -> Decimal:
    """What this campaign takes off a line of `qty` at `unit_price`, worth `gross` before anything comes off.

    Never more than the line is worth: a campaign that would make an Item free makes it free, not owed."""
    if qty <= 0 or gross <= 0 or spent_out(promo):
        return ZERO
    if promo.min_qty and qty < promo.min_qty:
        return ZERO
    if promo.kind == "percent":
        amount = gross * (promo.disc_percent or ZERO) / HUNDRED
    elif promo.kind == "flat":
        amount = (promo.disc_flat or ZERO) * qty
    elif promo.kind == "price" and promo.promo_price is not None:
        amount = max(unit_price - promo.promo_price, ZERO) * qty
    else:
        amount = ZERO
    return min(amount, gross)


def best(promos: list[Promotion] | None, unit_price: Decimal, qty: Decimal, gross: Decimal) -> tuple[Promotion | None, Decimal]:
    """The campaign that gives this line the most off, and how much. None when none of them applies."""
    winner, most = None, ZERO
    for promo in promos or []:
        amount = discount(promo, unit_price, qty, gross)
        if amount > most:
            winner, most = promo, amount
    return winner, most


async def record_use(uses: dict[str, tuple[Decimal, Decimal]]) -> None:
    """Add what a bill just used to each campaign's running total, so a limit means something.

    Keyed by campaign id, with the quantity sold under it and the money it gave away. Head office is told through the
    ordinary event stream; a branch's own copy is updated here so a limit reached mid-day stops at that till without
    waiting for a round trip."""
    for promo_id, (qty, amount) in uses.items():
        promo = await Promotion.get_or_none(id=promo_id)
        if promo is None:
            continue
        promo.used_qty = (promo.used_qty or ZERO) + qty
        promo.used_amount = (promo.used_amount or ZERO) + amount
        await promo.save(update_fields=["used_qty", "used_amount", "updated_at"])
