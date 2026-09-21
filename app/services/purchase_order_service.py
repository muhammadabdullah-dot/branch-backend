from datetime import datetime, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.models import Location, Product, PurchaseOrder, PurchaseOrderLine, Supplier, User, next_value
from app.schemas.purchase_orders import PurchaseOrderCreate, PurchaseOrderLineIn, PurchaseOrderUpdate
from app.services.inventory_service import in_window

APPROVE_RESOURCE = "inventory.purchase-orders.approve"

OPEN_FOR_RECEIVING = ("approved", "partially-received")


class PurchaseOrderError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


async def _with_lines(po: PurchaseOrder) -> PurchaseOrder:
    await po.fetch_related("lines__product", "supplier", "created_by", "approved_by", "grns")
    return po


async def get(po_id: str) -> PurchaseOrder:
    po = await PurchaseOrder.get_or_none(id=po_id)
    if not po:
        raise PurchaseOrderError("Purchase order not found", status=404)
    return await _with_lines(po)


async def list_all(
    limit: int, offset: int, from_at: datetime | None, to_at: datetime | None,
    status: str | None = None, supplier_id: str | None = None,
) -> tuple[list[PurchaseOrder], int]:
    qs = in_window(PurchaseOrder.all(), "created_at", from_at, to_at)
    if status == "open":
        qs = qs.filter(status__in=OPEN_FOR_RECEIVING)
    elif status:
        qs = qs.filter(status=status)
    if supplier_id:
        qs = qs.filter(supplier_id=supplier_id)
    total = await qs.count()
    orders = await qs.order_by("-created_at").offset(offset).limit(limit)
    return [await _with_lines(po) for po in orders], total


async def _check_header(supplier_id: str, location_id: str) -> tuple[Supplier, Location]:
    supplier = await Supplier.get_or_none(id=supplier_id)
    if not supplier:
        raise PurchaseOrderError(f"Unknown supplier {supplier_id}")
    if not supplier.active:
        raise PurchaseOrderError(f"{supplier.name} is switched off. Switch it back on to order from them.")
    location = await Location.get_or_none(id=location_id)
    if not location or not location.active:
        raise PurchaseOrderError("Pick an active location for the delivery.")
    return supplier, location


async def _write_lines(po: PurchaseOrder, lines: list[PurchaseOrderLineIn]) -> None:
    if not lines:
        raise PurchaseOrderError("A purchase order needs at least one line.")
    ids = [line.productId for line in lines]
    if len(set(ids)) != len(ids):
        raise PurchaseOrderError("An Item is on the order twice. Put the whole quantity on one line.")
    products = {p.id: p for p in await Product.filter(id__in=ids)}
    missing = [i for i in ids if i not in products]
    if missing:
        raise PurchaseOrderError(f"Unknown Item {missing[0]}")
    await PurchaseOrderLine.filter(purchase_order_id=po.id).delete()
    for position, line in enumerate(lines):
        await PurchaseOrderLine.create(
            purchase_order=po, product=products[line.productId], qty=line.qty,
            unit_price=line.unitPrice, disc_percent=line.discPercent, position=position,
        )


@atomic()
async def create(user: User, data: PurchaseOrderCreate) -> PurchaseOrder:
    supplier, location = await _check_header(data.supplierId, data.locationId)
    seq = await next_value("purchase_order", 1)
    po = await PurchaseOrder.create(
        po_number=f"PO-{seq:04d}", supplier=supplier, location=location, status="draft",
        expected_at=data.expectedAt, notes=(data.notes or "").strip() or None, created_by=user,
    )
    await _write_lines(po, data.lines)
    return await _with_lines(po)


async def _load_for_change(po_id: str) -> PurchaseOrder:
    po = await PurchaseOrder.get_or_none(id=po_id)
    if not po:
        raise PurchaseOrderError("Purchase order not found", status=404)
    return po


@atomic()
async def update(user: User, po_id: str, data: PurchaseOrderUpdate) -> PurchaseOrder:
    po = await _load_for_change(po_id)
    if po.status != "draft":
        raise PurchaseOrderError(f"{po.po_number} is {po.status}. Only a draft can be edited.")
    # Only the person who raised a draft changes it. Otherwise an approver could rewrite someone
    # else's order and then approve their own quantities: one person deciding the spend after all.
    if str(po.created_by_id) != str(user.id):
        await po.fetch_related("created_by")
        raise PurchaseOrderError(
            f"{po.po_number} was raised by {po.created_by.name}. Only they can change the draft, or you can cancel it and raise your own."
        )
    changes = data.model_dump(exclude_unset=True)
    supplier_id = changes.get("supplierId") or po.supplier_id
    location_id = changes.get("locationId") or po.location_id
    supplier, location = await _check_header(supplier_id, location_id)
    po.supplier, po.location = supplier, location
    if "expectedAt" in changes:
        po.expected_at = data.expectedAt
    if "notes" in changes:
        po.notes = (data.notes or "").strip() or None
    await po.save()
    if data.lines is not None:
        await _write_lines(po, data.lines)
    return await _with_lines(po)


@atomic()
async def approve(user: User, po_id: str) -> PurchaseOrder:
    po = await _load_for_change(po_id)
    if po.status != "draft":
        raise PurchaseOrderError(f"{po.po_number} is already {po.status}.")
    # The same rule as stock corrections: whoever raised the order doesn't sign it off. Otherwise an
    # order approval is one person spending the branch's money on their own say-so.
    if str(po.created_by_id) == str(user.id):
        raise PurchaseOrderError(f"You raised {po.po_number}, so someone else has to approve it.")
    if not await PurchaseOrderLine.filter(purchase_order_id=po.id).exists():
        raise PurchaseOrderError("A purchase order needs at least one line.")
    # The supplier or delivery location may have been switched off since the draft was raised.
    await _check_header(str(po.supplier_id), str(po.location_id))
    po.status = "approved"
    po.approved_by = user
    po.approved_at = datetime.now(timezone.utc)
    await po.save()
    from app.services import alerts_service

    await po.fetch_related("supplier")
    await alerts_service.notify(
        "po.approved", f"{po.po_number} to {po.supplier.name} approved by {user.name}", body="Send it to the supplier; receive it against the order when it arrives.",
        link="/inventory/purchase-orders", users=[str(po.created_by_id)], audience_any=[("inventory.receiving", "W")],
        subject=("purchase-order", str(po.id)), tone="good",
    )
    return await _with_lines(po)


@atomic()
async def cancel(user: User, po_id: str) -> PurchaseOrder:
    """A draft or an untouched approved order is cancelled. One already partly received is closed:
    what arrived stays received, and the rest is no longer expected.

    A Stock Keeper works under the Inventory Manager: they can withdraw their own draft, but an order
    that has been approved, or someone else's draft, is cancelled or closed only by someone who
    approves orders."""
    from app.services.rbac_service import has_permission

    po = await _load_for_change(po_id)
    if po.status in ("received", "cancelled", "closed"):
        raise PurchaseOrderError(f"{po.po_number} is already {po.status}.")
    own_draft = po.status == "draft" and str(po.created_by_id) == str(user.id)
    if not own_draft and not await has_permission(user, APPROVE_RESOURCE, "X"):
        if po.status == "draft":
            raise PurchaseOrderError(f"{po.po_number} is someone else's draft. Only they or an Inventory Manager can cancel it.")
        action = "close" if po.status == "partially-received" else "cancel"
        raise PurchaseOrderError(f"{po.po_number} has been approved, so only an Inventory Manager can {action} it.")
    po.status = "closed" if po.status == "partially-received" else "cancelled"
    po.closed_at = datetime.now(timezone.utc)
    await po.save()
    if str(po.created_by_id) != str(user.id):
        from app.services import alerts_service

        await alerts_service.notify(
            f"po.{po.status}", f"{po.po_number} {po.status} by {user.name}", link="/inventory/purchase-orders",
            users=[str(po.created_by_id)], subject=("purchase-order", str(po.id)), tone="warning",
        )
    return await _with_lines(po)


async def receive_against(po_id: str, supplier_id: str, received: dict[str, Decimal]) -> PurchaseOrder:
    """Ticks an order off with what a GRN received (paid-for units, bonus excluded). Runs inside the
    GRN's own transaction, so the order and the stock can't disagree."""
    po = await PurchaseOrder.get_or_none(id=po_id)
    if not po:
        raise PurchaseOrderError("That purchase order doesn't exist.")
    if po.status not in OPEN_FOR_RECEIVING:
        raise PurchaseOrderError(f"{po.po_number} is {po.status}, so nothing can be received against it.")
    if str(po.supplier_id) != str(supplier_id):
        raise PurchaseOrderError(f"{po.po_number} is with a different supplier.")
    lines = {str(line.product_id): line for line in await PurchaseOrderLine.filter(purchase_order_id=po.id).prefetch_related("product")}
    for product_id, qty in received.items():
        line = lines.get(product_id)
        if not line:
            product = await Product.get_or_none(id=product_id)
            raise PurchaseOrderError(f"{product.name if product else product_id} isn't on {po.po_number}. Receive it on a separate GRN.")
        remaining = line.qty - line.received_qty
        if qty > remaining:
            raise PurchaseOrderError(
                f"{po.po_number} has {remaining.normalize():f} of {line.product.name} left to receive, not {qty.normalize():f}. "
                "Put the extra down as bonus if it came free, or raise another order."
            )
    for product_id, qty in received.items():
        line = lines[product_id]
        line.received_qty = line.received_qty + qty
        await line.save(update_fields=["received_qty"])
    all_in = all(line.received_qty >= line.qty for line in lines.values())
    po.status = "received" if all_in else "partially-received"
    if all_in:
        po.closed_at = datetime.now(timezone.utc)
    await po.save()
    return po


# ── an Item nobody has in the catalog, written in on the order ──────────────────────────────────


def _by_hand_pack(unit: str, pack_unit: str | None, pack_size: int | None) -> tuple[str | None, int | None]:
    """How an Item written in by hand comes, kept the way the Item form keeps it: a pack unit (carton) and how many units
    make one, both or neither. With both, Billing can sell it by that pack and order suggestions round to whole packs."""
    pack_unit = " ".join((pack_unit or "").split()) or None
    if pack_unit is None and pack_size is None:
        return None, None
    if pack_unit is None:
        raise PurchaseOrderError(f"Say what the pack of {pack_size} {unit} is called (carton, box, case), or leave the number empty.")
    if pack_size is None:
        raise PurchaseOrderError(f"Say how many {unit} are in one {pack_unit}, or leave the pack unit empty.")
    if len(pack_unit) > 40:
        raise PurchaseOrderError("Keep the pack unit under 40 letters.")
    if pack_unit.lower() == unit.lower():
        raise PurchaseOrderError(f"The pack can't be a {unit} as well. Pick a bigger pack (carton, case), or leave the pack empty.")
    if pack_size < 2:
        raise PurchaseOrderError(f"One {pack_unit} has to hold at least 2 {unit}. Leave the pack empty if it comes one at a time.")
    return pack_unit, pack_size


@atomic()
async def add_by_hand(
    user: User | None, name: str, unit: str | None, cost: Decimal, price: Decimal | None, sku: str | None,
    pack_unit: str | None = None, pack_size: int | None = None,
) -> Product:
    """An Item the catalog doesn't have yet, written in while raising an order: a name, unit, the price paid and, if
    known, a sale price, code and the pack it comes in. It can be ordered and received at once and is marked "details to
    complete" until someone saves its Item form."""
    from app.services import catalog_service, masters_service, price_history_service

    name = " ".join((name or "").split())
    if not name:
        raise PurchaseOrderError("Write the Item's name.")
    if len(name) > 160:
        raise PurchaseOrderError("Keep the name under 160 letters.")
    if cost < 0 or (price is not None and price < 0):
        raise PurchaseOrderError("A price can't be below zero.")
    unit = " ".join((unit or "").split()) or "pc"
    if len(unit) > 20:
        raise PurchaseOrderError("Keep the unit under 20 letters.")
    pack_unit, pack_size = _by_hand_pack(unit, pack_unit, pack_size)
    # The same as the Item form: a unit or pack unit switched off in Item Lists can't go on an Item.
    try:
        await masters_service.refuse_switched_off_values("products", {"unit": unit, "pack_unit": pack_unit})
    except masters_service.MastersError as exc:
        raise PurchaseOrderError(exc.message) from exc
    same = await Product.filter(name__iexact=name).first()
    if same:
        raise PurchaseOrderError(f"The catalog already has {same.name} ({same.sku}). Search for it and pick it instead.")
    code = (sku or "").strip()
    if code:
        if len(code) > 40:
            raise PurchaseOrderError("Keep the code under 40 characters.")
        owner = await catalog_service.code_owner(code)
        if owner:
            raise PurchaseOrderError(f"Code {code} already belongs to {owner.name} ({owner.sku}).")
    else:
        code = await catalog_service._next_sku()
    missing = "sale price, " if price is None else ""
    product = await Product.create(
        id=code, sku=code, name=name, unit=unit, pack_unit=pack_unit, pack_size=pack_size,
        price=(price if price is not None else cost).quantize(Decimal("0.01")), tax_rate=Decimal("0"), avg_cost=Decimal("0"),
        needs_details=True,
        details_note=f"Written in by hand on a purchase order{f' by {user.name}' if user else ''}. Check the {missing}GST, barcode and department."[:200],
    )
    await price_history_service.save_rows([price_history_service.first_price(product, "new-item", None, user)])
    return product


# ── suggestions: what to put on an order, by plain rules ─────────────────────────────────────────
#
# Quantity to order = what the branch sells in the cover days (from its last 30 days of sales, net of returns), or the
# Item's reorder level if that is higher, less what the branch holds and what is already on open orders, rounded up to
# whole packs. Choosing a supplier suggests the Items bought from them before (earlier orders, GRNs and the Item's
# supplier list); without a supplier, the Items running low.

SALES_WINDOW_DAYS = 30
SHORT_DAYS = 7
MAX_SUGGESTIONS = 300
ZERO = Decimal("0")
OPEN_ORDERS = ("draft", "approved", "partially-received")


def _num(v: Decimal) -> str:
    v = Decimal(v)
    if v == v.to_integral_value():
        return f"{int(v):,}"
    return f"{v.quantize(Decimal('0.1')).normalize():f}"


def rate_words(rate: Decimal, who: str = "sells") -> str:
    """How fast an Item goes, the way a person would say it."""
    if rate <= 0:
        return f"no sales in the last {SALES_WINDOW_DAYS} days"
    if rate >= 1:
        return f"{who} {_num(rate if rate >= 10 else rate.quantize(Decimal('0.1')))} a day"
    week = rate * 7
    if week >= 1:
        return f"{who} about {_num(week.quantize(Decimal('0.1')) if week < 10 else week.quantize(Decimal('1')))} a week"
    return f"{who} about {_num(max(Decimal('1'), (rate * 30).quantize(Decimal('1'))))} a month"


def round_up(need: Decimal, pack_size: int | None, weighed: bool = False) -> tuple[Decimal, str | None]:
    """Whole packs when the pack size is known, whole units otherwise (half a unit for a weighed Item)."""
    import math

    if need <= 0:
        return ZERO, None
    if pack_size and pack_size > 1:
        packs = math.ceil(need / Decimal(pack_size))
        qty = Decimal(packs * pack_size)
        return qty, (f"rounded up to {packs} full pack{'s' if packs != 1 else ''} of {pack_size}" if qty != need else None)
    if weighed:
        return Decimal(math.ceil(need * 2)) / 2, None
    return Decimal(math.ceil(need)), None


def suggest_line(rate: Decimal, held: Decimal, on_order: Decimal, reorder_level: Decimal | None, cover_days: int,
                 pack_size: int | None = None, weighed: bool = False) -> dict:
    """The suggested quantity for one Item and the working in plain words."""
    by_sales = rate * cover_days
    level = reorder_level if reorder_level and reorder_level > 0 else ZERO
    target = max(by_sales, level)
    qty, packed = round_up(target - max(held, ZERO) - on_order, pack_size, weighed)
    parts = [rate_words(rate), f"holds {_num(held)}"]
    if on_order > 0:
        parts.append(f"{_num(on_order)} already on order")
    if rate > 0:
        parts.append(f"{cover_days} days of cover needs {_num(by_sales.quantize(Decimal('1')) if by_sales >= 10 else by_sales.quantize(Decimal('0.1')))}")
    if level > by_sales:
        parts.append(f"keeps at least its reorder level of {_num(level)}")
    if qty > 0:
        parts.append(f"so order {_num(qty)}" + (f" ({packed})" if packed else ""))
    elif target <= 0:
        parts.append("so nothing is suggested")
    else:
        parts.append("enough for now")
    return {"rate": rate, "target": target, "qty": qty, "reason": ", ".join(parts)}


def _d(v) -> Decimal:
    try:
        return Decimal(str(v)) if v not in (None, "") else ZERO
    except ArithmeticError:
        return ZERO


def _later(a, b) -> bool:
    return str(a or "") > str(b or "")


async def _sql(sql: str, params: list | None = None) -> list[dict]:
    from tortoise import Tortoise

    return await Tortoise.get_connection("default").execute_query_dict(sql, params or [])


async def _sales_rates() -> dict[str, Decimal]:
    """Units a day each Item sells here: the last 30 days' sales less returns, over the days the branch has traded in
    that time (fewer than 30 for a branch that opened recently)."""
    from datetime import timedelta

    from app.core.pk_time import day_start, pk_day, today_pk

    first = await _sql("SELECT MIN(at) AS first FROM sale_records")
    if not first or not first[0]["first"]:
        return {}
    try:
        opened = pk_day(datetime.fromisoformat(str(first[0]["first"]).replace("Z", "+00:00")))
    except ValueError:
        opened = today_pk() - timedelta(days=SALES_WINDOW_DAYS)
    start_day = max(today_pk() - timedelta(days=SALES_WINDOW_DAYS - 1), opened)
    days = max((today_pk() - start_day).days + 1, 1)
    since = day_start(start_day).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sold: dict[str, Decimal] = {}
    for r in await _sql(
        "SELECT sl.product_id, SUM(CASE WHEN sl.is_return THEN -CAST(sl.qty AS REAL) ELSE CAST(sl.qty AS REAL) END) AS qty "
        "FROM sale_lines sl JOIN sale_records sr ON sr.id = sl.sale_id WHERE sr.at >= ? GROUP BY sl.product_id", [since],
    ):
        sold[str(r["product_id"])] = _d(r["qty"])
    for r in await _sql(
        "SELECT rl.product_id, SUM(CAST(rl.qty AS REAL)) AS qty FROM return_lines rl JOIN return_records rr ON rr.id = rl.return_record_id "
        "WHERE rr.at >= ? GROUP BY rl.product_id", [since],
    ):
        pid = str(r["product_id"])
        sold[pid] = sold.get(pid, ZERO) - _d(r["qty"])
    return {pid: (q / days) for pid, q in sold.items() if q > 0}


async def suggestions(supplier_id: str | None, cover_days: int = 30, exclude_order_id: str | None = None) -> dict:
    """With a supplier: the Items bought from them before, each with a suggested quantity and why. Without one: the
    Items running low (below their reorder level, or the branch's usual low stock level for one that sells, or lasting
    under a week at the rate it sells)."""
    from app.services import masters_service

    cover_days = min(max(int(cover_days or 30), 1), 365)
    supplier = None
    why: dict[str, str] = {}
    last_cost: dict[str, tuple[str, Decimal]] = {}
    if supplier_id:
        supplier = await Supplier.get_or_none(id=supplier_id)
        if not supplier:
            raise PurchaseOrderError("That supplier doesn't exist.", 404)
        for r in await _sql("SELECT gl.product_id, gl.unit_price, g.at FROM grn_lines gl JOIN grns g ON g.id = gl.grn_id WHERE g.supplier_id = ?", [supplier_id]):
            pid = str(r["product_id"])
            why[pid] = "received from them"
            if pid not in last_cost or _later(r["at"], last_cost[pid][0]):
                last_cost[pid] = (str(r["at"] or ""), _d(r["unit_price"]))
        ordered: dict[str, tuple[str, Decimal]] = {}
        for r in await _sql(
            "SELECT l.product_id, l.unit_price, p.created_at AS at FROM purchase_order_lines l JOIN purchase_orders p ON p.id = l.purchase_order_id "
            "WHERE p.supplier_id = ? AND p.status != 'cancelled'", [supplier_id],
        ):
            pid = str(r["product_id"])
            why.setdefault(pid, "ordered from them")
            if pid not in ordered or _later(r["at"], ordered[pid][0]):
                ordered[pid] = (str(r["at"] or ""), _d(r["unit_price"]))
        for pid, seen in ordered.items():
            last_cost.setdefault(pid, seen)
        for r in await _sql("SELECT product_id FROM product_suppliers WHERE supplier_id = ?", [supplier_id]):
            why.setdefault(str(r["product_id"]), "on the Item's supplier list")
    products = await _sql(
        "SELECT id, sku, name, unit, pack_size, is_weighed, reorder_level, avg_cost, needs_details FROM products WHERE active = 1"
    )
    if supplier_id:
        products = [p for p in products if str(p["id"]) in why]
    held = {str(r["product_id"]): _d(r["qty"]) for r in await _sql("SELECT product_id, SUM(CAST(qty AS REAL)) AS qty FROM stock_movements GROUP BY product_id")}
    on_order: dict[str, Decimal] = {}
    for r in await _sql(
        "SELECT l.product_id, l.qty, l.received_qty FROM purchase_order_lines l JOIN purchase_orders p ON p.id = l.purchase_order_id "
        f"WHERE p.status IN ({','.join('?' * len(OPEN_ORDERS))})" + (" AND p.id != ?" if exclude_order_id else ""),
        [*OPEN_ORDERS, *([exclude_order_id] if exclude_order_id else [])],
    ):
        pid = str(r["product_id"])
        on_order[pid] = on_order.get(pid, ZERO) + max(_d(r["qty"]) - _d(r["received_qty"]), ZERO)
    rates = await _sales_rates()
    usual = await masters_service.low_stock_level() if not supplier_id else ZERO

    lines = []
    for p in products:
        pid = str(p["id"])
        h, o, r = held.get(pid, ZERO), on_order.get(pid, ZERO), rates.get(pid, ZERO)
        level = _d(p["reorder_level"]) if p["reorder_level"] is not None else None
        if not supplier_id:
            covered = h + o
            low = (level is not None and level > 0 and covered < level) or (r > 0 and covered < r * SHORT_DAYS) \
                or (r > 0 and level is None and covered < usual)
            if not low:
                continue
        s = suggest_line(r, h, o, level, cover_days, p["pack_size"], bool(p["is_weighed"]))
        if not supplier_id and s["qty"] <= 0:
            continue
        lines.append({
            "productId": pid, "sku": p["sku"], "name": p["name"], "unit": p["unit"], "packSize": p["pack_size"],
            "ratePerDay": s["rate"], "held": h, "onOrder": o, "reorderLevel": level, "suggestedQty": s["qty"],
            "unitCost": last_cost[pid][1] if pid in last_cost else _d(p["avg_cost"]), "reason": s["reason"],
            "why": why.get(pid, "running low"), "needsDetails": bool(p["needs_details"]),
        })
    lines.sort(key=lambda l: (l["suggestedQty"] <= 0, -l["ratePerDay"], l["name"]))
    shown = lines[:MAX_SUGGESTIONS]
    if not supplier_id and shown:
        # Without a supplier: the last price paid and who it was bought from, for the Items shown.
        ids = [l["productId"] for l in shown]
        latest: dict[str, dict] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in await _sql(
                "SELECT gl.product_id, gl.unit_price, g.at, s.name AS supplier FROM grn_lines gl JOIN grns g ON g.id = gl.grn_id "
                f"JOIN suppliers s ON s.id = g.supplier_id WHERE gl.product_id IN ({','.join('?' * len(chunk))})", chunk,
            ):
                pid = str(r["product_id"])
                if pid not in latest or _later(r["at"], latest[pid]["at"]):
                    latest[pid] = r
        for l in shown:
            last = latest.get(l["productId"])
            if last:
                l["unitCost"] = _d(last["unit_price"])
                l["why"] = f"running low, last bought from {last['supplier']}"
    return {
        "supplierId": supplier_id, "supplierName": supplier.name if supplier else None, "coverDays": cover_days,
        "salesDays": SALES_WINDOW_DAYS, "count": len(lines), "lines": shown,
        "rule": (f"Suggested quantity is what this branch sells in {cover_days} days (from its last {SALES_WINDOW_DAYS} days of sales, "
                 "less returns), or the Item's reorder level if that is higher, less what the branch holds and what is already on "
                 "open orders, rounded up to whole packs where the pack size is known."),
    }
