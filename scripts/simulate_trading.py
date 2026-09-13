"""Generate a stretch of realistic branch trading, backdated.

    python -m scripts.simulate_trading --from 2026-09-01 --to 2026-09-12
    python -m scripts.simulate_trading --from 2026-09-01 --to 2026-09-12 --yes

Why a script and not the API: every write here has to land on a *past* date, and `POST /sales`
quite correctly stamps `now`. So this mirrors what `sales_service.create_sale` and the receiving
flow actually write — sale, lines, tenders, stock movements, outbox event — rather than inventing a
shortcut that would leave the ledger inconsistent with itself. If this file and those services ever
disagree, this file is the one that is wrong.

What it produces, per trading day:

* **Purchases.** A goods-received note from a supplier on some days, with batch rows and positive
  stock movements. Stock has to come in before it can go out, and a branch whose stock only ever
  falls is not a branch anyone recognises.
* **Retail sales.** Walk-in customers, small baskets, cash and card, spread across shop hours with
  a lunchtime lull and an evening peak.
* **Wholesale sales.** Trade customers buying in bulk at a keener price, often on credit — which is
  the whole reason `credit_balance` and credit limits exist.
* **A till session** opened in the morning and closed at night, with a small drawer variance on
  some days, because a drawer that balances to the paisa every single day is a fixture, not a shop.

Everything is seeded from a fixed random seed, so the same arguments produce the same trading
twice. A simulation you cannot reproduce is not much use when a figure looks wrong.
"""
from __future__ import annotations

import argparse
import asyncio
import random
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from tortoise import Tortoise
from tortoise.transactions import in_transaction

from app.core.config import TORTOISE_ORM
from app.models import (
    GRN,
    Batch,
    GRNLine,
    Location,
    OutboxEvent,
    Party,
    Product,
    SaleLine,
    SaleRecord,
    SaleTender,
    StockMovement,
    Supplier,
    TillSession,
    User,
    next_value,
)
from app.services.sales_service import invoice_prefix

ZERO = Decimal("0")
# Branch trading days are stored shifted so that stored-UTC + 5 is the branch's own wall clock.
# Writing 04:00 stored means 09:00 in the shop, which is what every hour-of-day report expects.
PKT_OFFSET = 5

# Shop hours, local. The weights are the shape of a Pakistani grocery day: a morning build,
# a lull while people eat, and the real peak after work.
HOUR_WEIGHTS = {
    9: 4, 10: 7, 11: 8, 12: 6, 13: 4, 14: 3,
    15: 4, 16: 6, 17: 9, 18: 12, 19: 11, 20: 6,
}

RETAIL_TENDERS = ("CASH", "CASH", "CASH", "CARD", "EASYPAISA", "JAZZCASH")
DISCOUNT_LIMIT_PERCENT = Decimal("5")


def _q(v: Decimal, places: str = "0.01") -> Decimal:
    return v.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _at(day: date, local_hour: int, rng: random.Random) -> datetime:
    """A moment on that trading day, in the shape the branch stores."""
    return datetime(
        day.year, day.month, day.day,
        (local_hour - PKT_OFFSET) % 24, rng.randrange(0, 60), rng.randrange(0, 60),
        tzinfo=timezone.utc,
    )


class Simulator:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.sales = 0
        self.wholesale = 0
        self.lines = 0
        self.grns = 0
        self.received_units = Decimal("0")
        self.sold_units = Decimal("0")
        self.revenue = ZERO
        self.tills = 0

    # ── purchases ──────────────────────────────────────────────────────────────────────────────
    async def receive_stock(self, day: date, supplier, location, staff, products: list[Product]) -> None:
        """A delivery at the dock. Positive stock movements, because this is stock arriving."""
        at = _at(day, 9, self.rng)
        seq = await next_value("grn", 11)
        grn = await GRN.create(
            grn_number=f"GRN-{seq:04d}", supplier=supplier, location=location,
            received_by=staff, approved=True, gst_mode="normal", advance_tax=ZERO,
            party_inv_no=f"INV-{self.rng.randrange(10000, 99999)}",
        )
        await GRN.filter(id=grn.id).update(at=at)
        for product in self.rng.sample(products, k=min(len(products), self.rng.randrange(3, 7))):
            qty = Decimal(self.rng.randrange(24, 240))
            bonus = Decimal(self.rng.choice([0, 0, 0, 6, 12]))
            cost = _q(Decimal(str(product.avg_cost or product.price or 100)) * Decimal("0.98"))
            expiry = None
            if self.rng.random() < 0.35:
                expiry = datetime.combine(day + timedelta(days=self.rng.randrange(60, 400)),
                                          datetime.min.time(), tzinfo=timezone.utc)
            await GRNLine.create(
                grn=grn, product=product, qty=qty, bonus_qty=bonus, unit_price=cost,
                disc_percent=ZERO, expiry=expiry, tax_rate=product.tax_rate,
            )
            if expiry:
                await Batch.create(
                    product=product, lot_number=f"LOT-{self.rng.randrange(1000, 9999)}",
                    expiry=expiry, received_qty=qty + bonus,
                )
            # Bonus units are free stock, but they are still stock — they move onto the shelf and
            # they sell. Leaving them out of the ledger is how a count comes up short.
            await StockMovement.create(
                product=product, location=location, kind="receive", qty=qty + bonus,
                origin_user=staff, at=at, reason=grn.grn_number,
            )
            self.received_units += qty + bonus
        event = await OutboxEvent.create(
            aggregate_type="GRN", aggregate_id=str(grn.id),
            payload={"grnNumber": grn.grn_number, "locationId": location.id},
            origin_user_id=str(staff.id),
        )
        await OutboxEvent.filter(id=event.id).update(created_at=at)
        self.grns += 1

    # ── sales ──────────────────────────────────────────────────────────────────────────────────
    async def make_sale(self, day: date, hour: int, seller: User, party: Party,
                        location, products: list[Product], wholesale: bool) -> None:
        at = _at(day, hour, self.rng)
        basket = self.rng.randrange(4, 12) if wholesale else self.rng.randrange(1, 6)
        chosen = self.rng.sample(products, k=min(basket, len(products)))

        lines: list[tuple[Product, Decimal, Decimal]] = []
        gross = ZERO
        for product in chosen:
            price = Decimal(str(product.price or 100))
            if wholesale:
                # Trade price: bulk quantities at a keener rate. This is the whole point of having
                # two customer kinds — it shows up as lower margin on higher volume.
                qty = Decimal(self.rng.randrange(6, 60))
                price = _q(price * Decimal(str(self.rng.uniform(0.86, 0.94))))
            else:
                qty = Decimal(self.rng.randrange(1, 4))
            lines.append((product, qty, price))
            gross += qty * price

        # Discounts stay under the 5% self-serve ceiling: anything above needs a manager override,
        # and manufacturing fake overrides would put noise in the one report that exists to catch
        # real ones.
        disc_pct = Decimal(str(self.rng.choice([0, 0, 0, 1, 2, 3]))) if not wholesale else ZERO
        disc_total = _q(gross * disc_pct / Decimal("100"))
        gst = ZERO
        for product, qty, price in lines:
            line_gross = qty * price
            share = disc_total * (line_gross / gross) if gross else ZERO
            gst += (line_gross - share) * Decimal(str(product.tax_rate or 0)) / Decimal("100")
        gst = _q(gst)
        grand_total = gross - disc_total + gst
        net_value = grand_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        # Wholesale often goes on the book; retail never does.
        on_credit = wholesale and party.credit_allowed and self.rng.random() < 0.55
        if on_credit and party.credit_balance + net_value > party.credit_limit:
            on_credit = False  # respect the same headroom the real service enforces
        tender_code = "CREDIT" if on_credit else self.rng.choice(RETAIL_TENDERS)
        received = net_value

        seq = await next_value("invoice", 143)
        invoice_number = f"{await invoice_prefix()}-{day.year}-{seq:06d}"

        if on_credit:
            party.credit_balance = party.credit_balance + net_value
            await party.save(update_fields=["credit_balance"])

        sale = await SaleRecord.create(
            invoice_number=invoice_number, cashier=seller, party=party,
            gross=_q(gross), disc_total=disc_total, fare=ZERO, gst=gst,
            grand_total=_q(grand_total), net_value=net_value,
            earned_points=0, received=received, cash_back=ZERO,
            is_credit_sale=on_credit, fbr_invoice_number=f"7000-{invoice_number[-8:]}",
            client_request_id=f"sim-{invoice_number}",
        )
        await SaleRecord.filter(id=sale.id).update(at=at)
        for product, qty, price in lines:
            await SaleLine.create(sale=sale, product=product, qty=qty, unit_price=price, is_return=False)
            await StockMovement.create(
                product=product, location=location, kind="sell", qty=-qty,
                origin_user=seller, at=at,
            )
            self.sold_units += qty
            self.lines += 1
        await SaleTender.create(sale=sale, code=tender_code, amount=net_value)
        event = await OutboxEvent.create(
            aggregate_type="SaleRecord", aggregate_id=str(sale.id),
            payload={"invoiceNumber": invoice_number, "netValue": str(net_value), "partyId": str(party.id)},
            origin_user_id=str(seller.id),
        )
        await OutboxEvent.filter(id=event.id).update(created_at=at)
        self.sales += 1
        self.wholesale += 1 if wholesale else 0
        self.revenue += net_value

    # ── the drawer ─────────────────────────────────────────────────────────────────────────────
    async def run_till(self, day: date, seller: User, cash_taken: Decimal) -> None:
        seq = await next_value("till_session", 41)
        opening = Decimal("1000")
        # Most days balance. Some don't — a drawer that reconciles to the paisa every single day
        # is a fixture, not a shop, and Till Variance exists to catch the days it doesn't.
        variance = ZERO
        if self.rng.random() < 0.3:
            variance = Decimal(str(self.rng.choice([-120, -50, -20, 20, 45, 90])))
        net_cash = opening + cash_taken
        await TillSession.create(
            session_number=f"TS-{seq:04d}", opened_by=seller,
            opened_at=_at(day, 9, self.rng), closed_at=_at(day, 21, self.rng),
            opening_float=opening, opening_denominations='{"1000":1}', status="closed",
            net_cash=net_cash, counted_cash=net_cash + variance, variance=variance,
        )
        self.tills += 1


async def simulate(start: date, end: date, apply: bool, seed: int) -> None:
    rng = random.Random(seed)
    sim = Simulator(rng)

    products = await Product.filter(active=True).limit(400)
    if not products:
        raise SystemExit("No active products in this branch — nothing to sell.")
    location = await Location.get_or_none(id="loc-1") or await Location.first()
    sellers = await User.filter(role_id__in=["cashier", "sales-manager"], active=True)
    stock_staff = await User.filter(role_id__in=["stock-keeper", "inventory-manager"], active=True)
    retail_parties = await Party.filter(is_walk_in=True) or await Party.filter(tier="retail")
    trade_parties = await Party.filter(tier="wholesale", active=True)
    suppliers = await Supplier.all()

    if not sellers or not retail_parties:
        raise SystemExit("Need at least one active salesperson and a walk-in party.")

    days = (end - start).days + 1
    print(f"\n  Simulating {days} trading day(s): {start} to {end}")
    print(f"    salespeople: {len(sellers)} | trade customers: {len(trade_parties)} | suppliers: {len(suppliers)}")
    if not apply:
        print("\n  Dry run — nothing written. Re-run with --yes to apply.\n")
        return

    async with in_transaction():
        for offset in range(days):
            day = start + timedelta(days=offset)
            # Friday is the quiet half-day in much of Pakistani retail; Sunday is the big one.
            weekday = day.weekday()
            volume = {4: 0.6, 6: 1.35}.get(weekday, 1.0)

            if suppliers and stock_staff and rng.random() < 0.45:
                await sim.receive_stock(day, rng.choice(suppliers), location, rng.choice(stock_staff), products)

            cash_taken = ZERO
            hours = list(HOUR_WEIGHTS)
            weights = [HOUR_WEIGHTS[h] for h in hours]
            retail_count = int(rng.randrange(14, 26) * volume)
            for _ in range(retail_count):
                hour = rng.choices(hours, weights=weights, k=1)[0]
                before = sim.revenue
                await sim.make_sale(day, hour, rng.choice(sellers), rng.choice(retail_parties),
                                    location, products, wholesale=False)
                cash_taken += sim.revenue - before

            if trade_parties:
                for _ in range(rng.randrange(1, 4)):
                    hour = rng.choice([10, 11, 12, 16, 17])
                    await sim.make_sale(day, hour, rng.choice(sellers), rng.choice(trade_parties),
                                        location, products, wholesale=True)

            await sim.run_till(day, rng.choice(sellers), cash_taken)
            print(f"    {day}  {retail_count} retail + wholesale, till closed")

    print(f"\n  Written:")
    print(f"    {sim.sales} sales ({sim.wholesale} wholesale), {sim.lines} lines, Rs {sim.revenue:,.0f}")
    print(f"    {sim.grns} goods-received note(s), {sim.received_units:,.0f} units in, {sim.sold_units:,.0f} units out")
    print(f"    {sim.tills} till session(s) closed")
    print(f"    {sim.sales + sim.grns} outbox event(s) queued for the next sync\n")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="start", required=True, help="First trading day, YYYY-MM-DD")
    ap.add_argument("--to", dest="end", required=True, help="Last trading day, YYYY-MM-DD")
    ap.add_argument("--seed", type=int, default=20260913, help="Random seed, so a run is reproducible")
    ap.add_argument("--yes", action="store_true", help="Actually write it")
    args = ap.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("--to is before --from")

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        await simulate(start, end, args.yes, args.seed)
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
