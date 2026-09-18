"""No discount can sell at a loss, and how much of a bill's profit a person may give away.

The owner's rule, company-wide: no discount, however it is given, may take what the shop gets for its goods below what
they cost it. Each Item has a floor: its average purchase cost (`Product.avg_cost`, which leaves out the sales tax
paid on it) plus that sales tax, at the Item's own GST rate on the same base receiving uses (the line's cost after its
discounts and with its charges, which is what the average is made of). What a line fetches is compared with its floor
the same way, tax included: the line after its discounts plus the GST the customer pays on it. With the same rate on
both sides that is the same as comparing the line before tax with the Item's cost before tax, which is how the sums
below are done.

  a. A line sold at the Item's own price (its sale price, or its wholesale price on a wholesale bill) that is already at
     or below its floor still sells at that price, but takes no discount at all: not the Item's own, not a share of the
     bill's. Those Items are on the Priced below cost list for their prices to be fixed.
  b. Any other line never goes below its floor. The Item's own discount stops at the floor; a price that is not the
     Item's own and is below the floor is refused.
  c. A bill discount (bill %, flat, special) is shared out over the lines in proportion to the profit each has left
     above its floor, so no line is taken below its floor and the most bill discount a bill can take is that profit,
     added up. Lines with Lock Discount, returns and lines priced below cost take none of it.
  d. No bill comes to less than nothing.

A bill discount comes off what the bill sells, never off what it takes back: a return on an exchange is credited at
its own value, so a customer swapping one Item for another isn't blocked by a discount that was only meant for the new
Item. Floors are about what is sold; returned goods have no floor.

A person's discount limit is measured on that profit, not on the price: the most bill discount the floors allow is
100%, so a 10% limit gives away a tenth of the profit left in the bill after the Items' own discounts.

Pure arithmetic on Decimals, so the till (pages/store/Billing.tsx) can do exactly the same sums for its preview.
"""
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")
# A paisa either way: the till rounds what it offers down to a whole rupee or a tenth of a percent.
TOLERANCE = Decimal("0.01")
# A replacement for goods that came back (Returns, Replace) is rung at what the customer paid for them, which is a swap,
# not a discount: set around that bill (`token = replacing.set(True)` ... `replacing.reset(token)`), a price that isn't
# the Item's own is taken as it is, with no discount. Only server code can set it.
replacing: ContextVar[bool] = ContextVar("replacing", default=False)


def unit_floor(avg_cost: Decimal | None, tax_rate: Decimal | None) -> Decimal:
    """What one unit cost the shop, the sales tax paid on it included."""
    cost = Decimal(avg_cost or 0)
    return cost + cost * Decimal(tax_rate or 0) / HUNDRED


def with_tax(value: Decimal, tax_rate: Decimal | None) -> Decimal:
    return value + value * Decimal(tax_rate or 0) / HUNDRED


@dataclass
class LineIn:
    name: str
    is_return: bool
    # What the line is worth before any discount (always positive), and the discount set on the Item (or its pack) for it.
    gross: Decimal
    item_disc: Decimal
    tax_rate: Decimal
    # Stocked units, whatever the line was rung as (5 tablets of a 200-tablet box is 0.025): cost is per unit.
    qty: Decimal
    avg_cost: Decimal
    locked: bool
    # Rung at one of the Item's own prices (sale, wholesale, pack or box), not a price typed or kept from elsewhere.
    own_price: bool = True


@dataclass
class BillFigures:
    # The value of what the bill discount can come off, which a bill % is taken of.
    discountable: Decimal
    bill_disc: Decimal
    # Each line's Item discount after the floor stopped it, and its whole discount (that plus its share of the bill's).
    item_discs: list[Decimal]
    line_discs: list[Decimal]
    # The profit left above the floors on the lines a bill discount can come off: the most bill discount there can be.
    room: Decimal
    # Lines sold at their own price that is at or below their floor: they take no discount.
    below_cost: list[bool]
    # Lines at a price that isn't the Item's own and below the floor: (name, fetches with tax, floor with tax).
    refused: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)

    @property
    def max_bill_disc(self) -> Decimal:
        return max(ZERO, self.room)

    def authority_percent(self) -> Decimal:
        """The bill discount as a share of the profit the floors leave: what a person's discount limit is held to."""
        if self.bill_disc <= 0:
            return ZERO
        if self.room <= 0:
            return HUNDRED * 100
        return self.bill_disc / self.room * HUNDRED


def figures(lines: list[LineIn], disc_percent: Decimal, flat_disc: Decimal) -> BillFigures:
    item_discs: list[Decimal] = []
    rooms: list[Decimal] = []
    below: list[bool] = []
    refused: list[tuple[str, Decimal, Decimal]] = []
    discountable = room = ZERO
    for line in lines:
        if line.is_return:
            item_discs.append(line.item_disc)
            rooms.append(ZERO)
            below.append(False)
            continue
        cost = line.qty * Decimal(line.avg_cost or 0)
        if line.gross <= cost + TOLERANCE:
            own = line.own_price or replacing.get()
            if not own:
                refused.append((line.name, with_tax(line.gross, line.tax_rate), with_tax(cost, line.tax_rate)))
            item_discs.append(ZERO)
            rooms.append(ZERO)
            below.append(own)
            continue
        item_disc = min(line.item_disc, line.gross - cost)
        value = line.gross - item_disc
        item_discs.append(item_disc)
        below.append(False)
        if line.locked:
            rooms.append(ZERO)
            continue
        rooms.append(value - cost)
        discountable += value
        room += value - cost
    bill_disc = (discountable * disc_percent / HUNDRED + flat_disc) if discountable > 0 else ZERO
    line_discs = [d + (bill_disc * r / room if room > 0 else ZERO) for d, r in zip(item_discs, rooms)]
    return BillFigures(
        discountable=discountable, bill_disc=bill_disc, item_discs=item_discs, line_discs=line_discs, room=room,
        below_cost=below, refused=refused,
    )


def rs(value: Decimal) -> str:
    """Rupees the way a person reads them on the till: Rs 1,234 (Rs 1,234.50 when there are paisa)."""
    value = Decimal(value).quantize(Decimal("0.01"))
    text = f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,.2f}"
    return f"Rs {text}"
