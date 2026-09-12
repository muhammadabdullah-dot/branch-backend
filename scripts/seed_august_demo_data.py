"""
One-off dev-data generator: adds a few more per-role users, backdates the entire product
catalog into opening stock as of 1 Aug 2026, then simulates a full month (1-31 Aug 2026) of
day-to-day branch operations — till open/cash-in/cash-out/close, sales (mixed tenders,
occasional credit/discount-override), sales returns, physical counts, adjustments, and a few
extra restock GRNs — spread across the (now-larger) roster of branch users.

Run standalone against the SQLite file directly (the FastAPI server does NOT need to be
running — in fact it should be stopped first to avoid concurrent-write surprises):

    .venv/Scripts/python.exe scripts/seed_august_demo_data.py

Idempotency: the extra users are added only if they don't already exist. The August
simulation itself is NOT idempotent — running it twice will double the month's data. There's
no "already simulated" guard because there's no reliable single signal for it; if you need to
re-run, restore branch.db from a backup taken before the first run.

Real service functions (sales_service, returns_service, till_service, inventory_service) are
called for every day-to-day operation so the actual business logic (invoice numbering,
weighted-average cost, credit-limit checks, discount-override validation) runs exactly as it
would from the real API — this script only backdates the resulting rows' timestamps
afterward, via direct queryset .update() calls (which bypass auto_now_add). The one exception
is the opening-stock GRN itself: at ~47k product lines, looping through the real GRN service
(per-line balance queries) would take far too long, so that step uses bulk_create directly.

Not simulated: gift-voucher redemption (already covered by manual testing earlier), and
"purchase returns" (returning stock to a supplier) — no such feature exists in this backend
yet (only receiving), so nothing to simulate.
"""
import asyncio
import random
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from tortoise import Tortoise

from app.core.config import TORTOISE_ORM
from app.core.security import hash_password
from app.models import (
    GRN,
    Adjustment,
    CashMovement,
    GRNLine,
    Party,
    PhysicalCount,
    Product,
    ReturnRecord,
    RoleDefaultPermission,
    SaleRecord,
    StockMovement,
    Supplier,
    TillSession,
    User,
    UserPermission,
    balance_for,
    next_value,
)
from app.schemas.inventory import AdjustmentSubmitRequest, CountSubmitRequest, GRNCreateRequest, GRNLineIn
from app.schemas.sales import ReturnCreateRequest, ReturnLineIn, SaleCreateRequest, SaleLineIn
from app.services import inventory_service, returns_service, sales_service, till_service

RNG = random.Random(20260801)
ZERO = Decimal("0")
LOCATION_ID = "loc-1"

NEW_USERS = [
    ("cashier2@branch.dmarina.pk", "cashier123", "cashier", "Bilal Ahmed"),
    ("cashier3@branch.dmarina.pk", "cashier123", "cashier", "Usman Tariq"),
    ("salesmanager2@branch.dmarina.pk", "sales123", "sales-manager", "Hina Malik"),
    ("stockkeeper2@branch.dmarina.pk", "stock123", "stock-keeper", "Kashif Iqbal"),
    ("inventorymanager2@branch.dmarina.pk", "inventory123", "inventory-manager", "Sara Khan"),
]


def day_at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def compute_net_value(lines: list[SaleLineIn], disc_percent: Decimal, flat_disc: Decimal, tax_rate_by_id: dict[str, Decimal]) -> Decimal:
    """Mirrors sales_service.create_sale's own totals math exactly, so tenders can be set
    precisely instead of guessed at."""
    def line_gross(l: SaleLineIn) -> Decimal:
        return l.qty * l.unitPrice

    gross = sum((line_gross(l) for l in lines), ZERO)
    percent_disc = gross * disc_percent / Decimal("100")
    disc_total = percent_disc + flat_disc

    def line_disc(l: SaleLineIn) -> Decimal:
        lg = line_gross(l)
        share = (flat_disc * (lg / gross)) if gross != 0 else ZERO
        return lg * disc_percent / Decimal("100") + share

    gst = sum(
        ((line_gross(l) - line_disc(l)) * tax_rate_by_id.get(l.productId, ZERO) / Decimal("100")) for l in lines
    )
    grand_total = gross - disc_total + gst
    return grand_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


async def add_users() -> None:
    print("== Adding extra users ==")
    for email, password, role_id, name in NEW_USERS:
        if await User.get_or_none(email=email.lower()):
            print(f"  skip (exists): {email}")
            continue
        user = await User.create(name=name, email=email.lower(), password_hash=hash_password(password), role_id=role_id)
        for template in await RoleDefaultPermission.filter(role_id=role_id):
            await UserPermission.create(
                user=user, resource=template.resource, can_read=template.can_read,
                can_write=template.can_write, can_execute=template.can_execute, granted_by=None,
            )
        print(f"  created: {email} ({name})")


async def close_any_open_till(operator: User) -> None:
    """Safety net: an open till left over from earlier real testing would make Day 1's
    open_till() fail immediately."""
    till = await TillSession.get_or_none(status="open")
    if not till:
        return
    print(f"  closing leftover open till {till.session_number} before simulation starts")
    try:
        await till_service.close_till(operator, {"1000": 0})
    except till_service.TillError as exc:
        print(f"  couldn't close leftover till cleanly ({exc.message}); forcing status directly")
        await TillSession.filter(id=till.id).update(status="closed", closed_at=datetime.now(timezone.utc))


async def bulk_opening_stock(received_by: User) -> None:
    print("== Bulk opening stock (1 Aug 2026) ==")
    conn = Tortoise.get_connection("default")
    await conn.execute_query("UPDATE products SET avg_cost = ROUND(price * 0.8, 2) WHERE avg_cost = 0 OR avg_cost IS NULL")

    supplier = await Supplier.first()
    target = day_at(1, 8, 0)
    grn_seq = await next_value("grn", 11)
    grn = await GRN.create(
        grn_number=f"GRN-{grn_seq:04d}", supplier=supplier, party_inv_no="OPENING-AUG-2026",
        location_id=LOCATION_ID, gst_mode="normal", advance_tax=ZERO, approved=True, received_by=received_by,
    )
    await GRN.filter(id=grn.id).update(at=target)

    products = await Product.all().values("id", "price", "avg_cost", "tax_rate")
    print(f"  products to stock: {len(products)}")

    BATCH = 2000
    lines: list[GRNLine] = []
    movements: list[StockMovement] = []
    for p in products:
        qty = Decimal(RNG.randint(15, 200))
        cost = p["avg_cost"] if p["avg_cost"] else (p["price"] * Decimal("0.8"))
        lines.append(GRNLine(
            grn_id=grn.id, product_id=p["id"], qty=qty, bonus_qty=ZERO, unit_price=cost,
            disc_percent=ZERO, expiry=None, tax_rate=p["tax_rate"],
        ))
        movements.append(StockMovement(
            product_id=p["id"], location_id=LOCATION_ID, kind="receive", qty=qty,
            origin_user_id=received_by.id, at=target,
        ))
        if len(lines) >= BATCH:
            await GRNLine.bulk_create(lines)
            await StockMovement.bulk_create(movements)
            lines, movements = [], []
    if lines:
        await GRNLine.bulk_create(lines)
        await StockMovement.bulk_create(movements)
    print(f"  GRN {grn.grn_number} — {len(products)} lines received, dated {target.isoformat()}")


async def backdate_stock_movements(since: datetime, target: datetime) -> None:
    await StockMovement.filter(at__gte=since).update(at=target)


async def do_till_day(day: int, operator: User, sample_products: list[Product], party_ali: Party, sales_managers: list[User]) -> None:
    open_at = day_at(day, 9, 0)
    close_at = day_at(day, 20, 30)

    opening_float = Decimal(RNG.choice([5000, 6000, 8000, 10000]))
    try:
        await till_service.open_till(operator, {"1000": int(opening_float // 1000)}, "")
    except till_service.TillError as exc:
        print(f"  [day {day}] till open failed: {exc.message}")
        return
    till = await TillSession.get_or_none(status="open")
    if not till:
        return
    await TillSession.filter(id=till.id).update(opened_at=open_at)

    tax_rate_by_id = {p.id: p.tax_rate for p in sample_products}
    n_sales = RNG.randint(6, 18)
    day_sales: list[SaleRecord] = []
    for i in range(n_sales):
        n_lines = RNG.randint(1, 4)
        lines: list[SaleLineIn] = []
        for _ in range(n_lines):
            p = RNG.choice(sample_products)
            qty = Decimal(str(round(RNG.uniform(0.2, 2.5), 3))) if p.is_weighed else Decimal(RNG.randint(1, 4))
            lines.append(SaleLineIn(productId=p.id, qty=qty, unitPrice=p.price, isReturn=False))

        disc_percent = Decimal("0")
        flat_disc = ZERO
        override_by = None
        roll = RNG.random()
        if roll < 0.10:
            flat_disc = Decimal(RNG.choice([50, 100, 150]))
        if roll < 0.04:
            disc_percent = Decimal(RNG.choice([8, 10]))
            other_sm = [u for u in sales_managers if u.id != operator.id]
            override_by = str((other_sm[0] if other_sm else sales_managers[0]).id)

        net_value = compute_net_value(lines, disc_percent, flat_disc, tax_rate_by_id)

        party_id = None
        tenders: dict[str, Decimal] = {}
        if RNG.random() < 0.06:
            fresh_party = await Party.get(id=party_ali.id)
            headroom = fresh_party.credit_limit - fresh_party.credit_balance
            if headroom > net_value:
                party_id = str(fresh_party.id)
                tenders["CREDIT"] = net_value
        if not tenders:
            if RNG.random() < 0.15:
                cash_part = (net_value * Decimal("0.6")).quantize(Decimal("1"))
                tenders["CASH"] = cash_part
                tenders["CARD"] = max(ZERO, net_value - cash_part)
            else:
                tenders["CASH"] = net_value

        payload = SaleCreateRequest(
            partyId=party_id, lines=lines, discPercent=disc_percent, flatDisc=flat_disc, fare=ZERO,
            tenders=tenders, voucherCode=None, discountOverrideByUserId=override_by,
            clientRequestId=f"aug-sim-{day}-{i}",
        )
        t0 = datetime.now(timezone.utc)
        try:
            sale = await sales_service.create_sale(operator, payload)
        except sales_service.SaleError as exc:
            print(f"  [day {day}] sale skipped: {exc.message}")
            continue
        sale_at = day_at(day, RNG.randint(9, 20), RNG.randint(0, 59))
        await SaleRecord.filter(id=sale.id).update(at=sale_at)
        await backdate_stock_movements(t0, sale_at)
        day_sales.append(sale)

    for sale in day_sales:
        if RNG.random() < 0.12:
            await sale.fetch_related("lines")
            real_lines = [l for l in sale.lines if not l.is_return]
            if not real_lines:
                continue
            line = RNG.choice(real_lines)
            return_qty = line.qty if line.qty <= 1 else (line.qty / 2).quantize(Decimal("0.001"))
            t0 = datetime.now(timezone.utc)
            try:
                ret = await returns_service.create_return(
                    operator,
                    ReturnCreateRequest(against=sale.invoice_number, lines=[
                        ReturnLineIn(productId=str(line.product_id), qty=return_qty, unitPrice=line.unit_price)
                    ]),
                )
            except returns_service.ReturnError:
                continue
            return_at = day_at(day, RNG.randint(10, 20), RNG.randint(0, 59))
            await ReturnRecord.filter(id=ret.id).update(at=return_at)
            await backdate_stock_movements(t0, return_at)

    for _ in range(RNG.randint(1, 2)):
        kind = RNG.choice(["in", "out"])
        t0 = datetime.now(timezone.utc)
        try:
            movement = await till_service.record_movement(
                operator, kind, {"1000": RNG.randint(1, 3)}, "petty cash" if kind == "out" else "float top-up"
            )
        except till_service.TillError:
            movement = None
        if movement:
            mv_at = day_at(day, RNG.randint(11, 19), RNG.randint(0, 59))
            await CashMovement.filter(id=movement.id).update(at=mv_at)

    preview = await till_service.preview_close()
    counted = preview["netCash"] + Decimal(RNG.choice([-50, -20, 0, 0, 0, 20, 40]))
    counted = max(ZERO, counted)
    counted_denoms = {"1000": int(counted // 1000), "100": int((counted % 1000) // 100)}
    try:
        result = await till_service.close_till(operator, counted_denoms)
        closed_till = await TillSession.get_or_none(session_number=result["sessionNumber"])
        if closed_till:
            await TillSession.filter(id=closed_till.id).update(closed_at=close_at)
    except till_service.TillError as exc:
        print(f"  [day {day}] till close failed: {exc.message}")

    print(f"  [day {day}] operator={operator.name}: {len(day_sales)} sales")


async def do_stock_admin(day: int, submitter: User, approver: User, sample_products: list[Product]) -> None:
    p = RNG.choice(sample_products)
    system_qty = await balance_for(p.id, LOCATION_ID)
    counted_qty = max(ZERO, system_qty + Decimal(RNG.randint(-5, 5)))
    t0 = datetime.now(timezone.utc)
    try:
        count = await inventory_service.submit_count(
            submitter, CountSubmitRequest(productId=p.id, locationId=LOCATION_ID, countedQty=counted_qty)
        )
        count_at = day_at(day, 10, RNG.randint(0, 59))
        await PhysicalCount.filter(id=count.id).update(at=count_at)
        await inventory_service.approve_count(approver, str(count.id))
        await backdate_stock_movements(t0, count_at)
    except inventory_service.InventoryError as exc:
        print(f"  [day {day}] count skipped: {exc.message}")

    reason = RNG.choice(["damage", "expiry", "found"])
    magnitude = Decimal(RNG.randint(1, 5))
    p2 = RNG.choice(sample_products)
    t0 = datetime.now(timezone.utc)
    try:
        adj = await inventory_service.submit_adjustment(
            submitter, AdjustmentSubmitRequest(productId=p2.id, locationId=LOCATION_ID, reason=reason, magnitude=magnitude, notes="Aug routine check")
        )
        adj_at = day_at(day, 10, RNG.randint(0, 59))
        await Adjustment.filter(id=adj.id).update(at=adj_at)
        await inventory_service.approve_adjustment(approver, str(adj.id))
        await backdate_stock_movements(t0, adj_at)
    except inventory_service.InventoryError as exc:
        print(f"  [day {day}] adjustment skipped: {exc.message}")


async def do_restock_grn(day: int, submitter: User, sample_products: list[Product]) -> None:
    suppliers = await Supplier.all()
    supplier = RNG.choice(suppliers)
    picks = RNG.sample(sample_products, k=min(6, len(sample_products)))
    lines = [
        GRNLineIn(
            productId=p.id, qty=Decimal(RNG.randint(10, 60)), bonusQty=Decimal(RNG.choice([0, 0, 5])),
            unitPrice=(p.avg_cost or (p.price * Decimal("0.8"))), discPercent=ZERO, expiry=None, taxRate=p.tax_rate,
        )
        for p in picks
    ]
    t0 = datetime.now(timezone.utc)
    try:
        grn = await inventory_service.receive_grn(
            submitter, GRNCreateRequest(supplierId=supplier.id, partyInvNo=f"AUG-{day:02d}", locationId=LOCATION_ID, gstMode="normal", advanceTax=ZERO, lines=lines)
        )
    except inventory_service.InventoryError as exc:
        print(f"  [day {day}] restock GRN skipped: {exc.message}")
        return
    grn_at = day_at(day, 8, RNG.randint(0, 59))
    await GRN.filter(id=grn.id).update(at=grn_at)
    await backdate_stock_movements(t0, grn_at)


async def main() -> None:
    await Tortoise.init(config=TORTOISE_ORM)

    await add_users()

    stock_keeper = await User.get(email="stockkeeper@branch.dmarina.pk")
    stock_keeper2 = await User.get(email="stockkeeper2@branch.dmarina.pk")
    inv_manager = await User.get(email="inventorymanager@branch.dmarina.pk")
    inv_manager2 = await User.get(email="inventorymanager2@branch.dmarina.pk")
    cashier1 = await User.get(email="cashier@branch.dmarina.pk")
    cashier2 = await User.get(email="cashier2@branch.dmarina.pk")
    cashier3 = await User.get(email="cashier3@branch.dmarina.pk")
    sm1 = await User.get(email="salesmanager@branch.dmarina.pk")
    sm2 = await User.get(email="salesmanager2@branch.dmarina.pk")
    sales_managers = [sm1, sm2]
    operators = [cashier1, cashier2, cashier3, sm1, sm2]
    stock_submitters = [stock_keeper, stock_keeper2]
    stock_approvers = [inv_manager, inv_manager2]

    party_ali = await Party.get(code="ALI001")

    await close_any_open_till(cashier1)
    await bulk_opening_stock(stock_keeper)

    sample_products = await Product.filter(active=True).limit(2000)
    print(f"== Sale-able product pool: {len(sample_products)} ==")

    print("== Simulating 1-31 Aug 2026 ==")
    for day in range(1, 32):
        operator = operators[(day - 1) % len(operators)]
        await do_till_day(day, operator, sample_products, party_ali, sales_managers)

        if day % 5 == 0:
            submitter = stock_submitters[(day // 5) % len(stock_submitters)]
            approver = stock_approvers[(day // 5) % len(stock_approvers)]
            await do_stock_admin(day, submitter, approver, sample_products)

        if day % 6 == 0:
            submitter = stock_submitters[(day // 6) % len(stock_submitters)]
            await do_restock_grn(day, submitter, sample_products)

    print("== Done ==")
    total_sales = await SaleRecord.filter(at__gte=day_at(1, 0), at__lt=day_at(31, 23, 59)).count()
    total_returns = await ReturnRecord.filter(at__gte=day_at(1, 0), at__lt=day_at(31, 23, 59)).count()
    total_till = await TillSession.filter(opened_at__gte=day_at(1, 0), opened_at__lt=day_at(31, 23, 59)).count()
    print(f"  August sales: {total_sales}")
    print(f"  August returns: {total_returns}")
    print(f"  August till sessions: {total_till}")

    await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
