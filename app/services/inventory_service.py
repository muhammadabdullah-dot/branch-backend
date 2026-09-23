"""Inventory domain — the ledger everything else folds from (contracts.md §6, first bullet).
Balances are never stored, only computed. GRN/Counts/Adjustments each post through the same
StockMovement table I3's Sales/Returns already write to.
"""
from datetime import datetime, timezone
from decimal import Decimal

from tortoise import Tortoise
from tortoise.transactions import atomic

from app.core.ordering import by_column
from app.core.device_context import get_device_id
from app.models import (
    GRN,
    Adjustment,
    Batch,
    GRNLine,
    Location,
    OutboxEvent,
    PhysicalCount,
    Product,
    PurchaseReturn,
    PurchaseReturnLine,
    StockMovement,
    Supplier,
    User,
    balance_for,
)
from app.services import numbering_service, price_history_service
from app.schemas.inventory import (
    AdjustmentSubmitRequest,
    CountSubmitRequest,
    GRNCreateRequest,
    PurchaseReturnCreateRequest,
)

ZERO = Decimal("0")


class InventoryError(Exception):
    def __init__(self, message: str):
        self.message = message


def _line_gross(line) -> Decimal:
    return line.qty * line.unitPrice * (Decimal("1") - line.discPercent / Decimal("100"))


def _line_net_cost(line) -> Decimal:
    """What the branch pays for a GRN line: price less its % discount, less the flat discount, plus
    charges. Never below zero."""
    return max(Decimal("0"), _line_gross(line) - line.flatDisc + line.misc)


async def _active_supplier(supplier_id: str) -> Supplier:
    supplier = await Supplier.get_or_none(id=supplier_id)
    if not supplier:
        raise InventoryError(f"Unknown supplier {supplier_id}")
    if not supplier.active:
        raise InventoryError(f"{supplier.name} is switched off. Switch it back on under Inventory → Suppliers to buy from them again.")
    return supplier


async def _active_location(location_id: str) -> Location:
    """New stock postings go only to a location that's switched on. History at a switched-off one
    stays readable; nothing new lands there."""
    location = await Location.get_or_none(id=location_id)
    if not location:
        raise InventoryError(f"Unknown location {location_id}")
    if not location.active:
        raise InventoryError(f"{location.name} is switched off. Pick another location, or switch it back on under Inventory → Locations.")
    return location


async def balance(product_id: str, location_id: str | None = None) -> Decimal:
    return await balance_for(product_id, location_id)


_STOCK_VALUE_SQL = """
SELECT COUNT(*) AS lines,
       COALESCE(SUM(b.qty), 0) AS total_qty,
       COALESCE(SUM(b.qty * p.price), 0) AS value_at_sale,
       COALESCE(SUM(b.qty * COALESCE(p.avg_cost, 0)), 0) AS value_at_cost
FROM (
    SELECT product_id, location_id, SUM(qty) AS qty
    FROM stock_movements
    GROUP BY product_id, location_id
    HAVING SUM(qty) != 0
) b
JOIN products p ON p.id = b.product_id
"""


async def stock_value() -> dict[str, Decimal | int]:
    """Branch-wide stock valuation, folded in SQL across the whole ledger and the whole catalog.

    It has to be computed here: valuation needs every Item's price, and no client holds the
    catalog — it runs to tens of thousands of rows and is only ever fetched a page at a time.
    A client summing what it happens to have cached reports a fraction of the real figure and
    gives no hint that it did."""
    rows = await Tortoise.get_connection("default").execute_query_dict(_STOCK_VALUE_SQL)
    row = rows[0] if rows else {}
    return {
        "lines": int(row.get("lines") or 0),
        "totalQty": Decimal(str(row.get("total_qty") or 0)),
        "valueAtSale": Decimal(str(row.get("value_at_sale") or 0)),
        "valueAtCost": Decimal(str(row.get("value_at_cost") or 0)),
    }


def in_window(qs, field: str, from_at: datetime | None, to_at: datetime | None):
    """Narrow a queryset to a date window, on the server.

    On the server rather than in the browser, and that is the whole point: a ledger like movements
    holds tens of thousands of rows and the screens only ever fetch a page of them. Filtering that
    page for "last month" would quietly return nothing for any month older than the page — a wrong
    answer that looks exactly like a quiet month. Bounds arrive as instants (the app resolves the
    shop's local day into them), and both ends are inclusive.
    """
    if from_at:
        qs = qs.filter(**{f"{field}__gte": from_at})
    if to_at:
        qs = qs.filter(**{f"{field}__lte": to_at})
    return qs


# The columns a ledger can be sorted by, mapped to what the database orders on. An allow-list, not a
# passthrough: the sort key arrives from the browser, and handing it to `order_by` unchecked would
# let a request order by any field on any related table.
MOVEMENT_SORTS = {
    "at": "at",
    "qty": "qty",
    "kind": "kind",
    "item": "product__name",
    "code": "product__sku",
    # By the name the column shows, not the location's id.
    "location": "location__name",
    "by": "origin_user__name",
}


async def list_movements(
    product_id: str | None, location_id: str | None, limit: int, offset: int,
    from_at: datetime | None = None, to_at: datetime | None = None, kind: str | None = None,
    sort: str | None = None, order: str | None = None,
) -> tuple[list[StockMovement], int]:
    """Sorted on the server, like the filters. The ledger is tens of thousands of rows and a screen
    holds fifteen of them, so sorting in the browser would only reorder the fifteen — "largest
    quantity first" would really mean "largest quantity on this page", which is not the question."""
    qs = in_window(StockMovement.all(), "at", from_at, to_at)
    if kind:
        qs = qs.filter(kind=kind)
    if product_id:
        qs = qs.filter(product_id=product_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    total = await qs.count()
    field = MOVEMENT_SORTS.get(sort or "at", "at")
    direction = "" if order == "asc" else "-"
    # `at` as a tiebreaker: rows with the same quantity or kind still come back in a stable order,
    # so paging forward never shows a row twice or skips one.
    qs, keys = by_column(qs, field, direction, {"product__sku": "code", "product__name": "text"}.get(field))
    ordering = keys + ([] if field == "at" else ["-at"])
    movements = await qs.order_by(*ordering).offset(offset).limit(limit)
    return movements, total


@atomic()
async def receive_grn(user: User, payload: GRNCreateRequest) -> GRN:
    supplier = await _active_supplier(payload.supplierId)
    location = await _active_location(payload.locationId)
    if not payload.lines:
        raise InventoryError("A GRN needs at least one line")

    products: dict[str, Product] = {}
    for line in payload.lines:
        product = await Product.get_or_none(id=line.productId)
        if not product:
            raise InventoryError(f"Unknown product {line.productId}")
        if line.qty < 0 or line.bonusQty < 0 or line.unitPrice < 0 or line.flatDisc < 0 or line.misc < 0:
            raise InventoryError(f"{product.name}: quantities, prices, discounts and charges can't be negative.")
        if line.qty + line.bonusQty <= 0:
            raise InventoryError(f"{product.name}: nothing received on this line.")
        if line.flatDisc > _line_gross(line):
            raise InventoryError(f"{product.name}: the flat discount is more than the line is worth.")
        for label, price in (("sale", line.newSalePrice), ("retail", line.newRetailPrice)):
            if price is not None and price < 0:
                raise InventoryError(f"{product.name}: the new {label} price can't be negative.")
        products[line.productId] = product

    grn = await GRN.create(
        grn_number=await numbering_service.next_number("grn", GRN, "grn_number", "GRN-", 4),
        supplier=supplier,
        party_inv_no=payload.partyInvNo,
        location=location,
        gst_mode=payload.gstMode,
        advance_tax=payload.advanceTax,
        approved=True,
        received_by=user,
        purchase_order_id=payload.purchaseOrderId,
    )

    if payload.purchaseOrderId:
        # Imported here, not at the top: purchase_order_service imports this module for `in_window`.
        from app.services import purchase_order_service
        received: dict[str, Decimal] = {}
        for line in payload.lines:
            received[line.productId] = received.get(line.productId, Decimal("0")) + line.qty
        try:
            await purchase_order_service.receive_against(payload.purchaseOrderId, str(supplier.id), received)
        except purchase_order_service.PurchaseOrderError as exc:
            raise InventoryError(exc.message) from exc

    gross_total = disc_total = net_total = tax_total = Decimal("0")
    for line in payload.lines:
        product = products[line.productId]
        # Cost, and any new price the delivery brings, go into the Item's price history against this GRN.
        price_before = price_history_service.snapshot(product)
        line_net = _line_net_cost(line)
        gross_total += line.qty * line.unitPrice
        disc_total += line.qty * line.unitPrice - _line_gross(line) + line.flatDisc
        net_total += line_net
        tax_total += line_net * line.taxRate / Decimal("100")
        await GRNLine.create(
            grn=grn, product=product, qty=line.qty, bonus_qty=line.bonusQty,
            unit_price=line.unitPrice, disc_percent=line.discPercent,
            flat_disc=line.flatDisc, misc=line.misc,
            expiry=line.expiry, tax_rate=line.taxRate,
            new_sale_price=line.newSalePrice, new_retail_price=line.newRetailPrice,
        )

        # Weighted-average cost basis (contracts.md gap: no COGS/margin basis existed anywhere).
        # Bonus units are free — they dilute the average, never raise it, matching real costing.
        # The line's cost is what was actually paid for it: less its % and flat discounts, plus
        # any charges on it.
        old_balance = await balance_for(product.id)
        incoming_qty = line.qty + line.bonusQty
        if incoming_qty > 0:
            if old_balance > 0:
                product.avg_cost = (product.avg_cost * old_balance + _line_net_cost(line)) / (old_balance + incoming_qty)
            else:
                # Stock at or below zero has no cost left to average with: what was sold before it was
                # received has already gone (a sale past zero, say, whose cost of sale was taken at the average then).
                # The delivery first makes up what is missing and what is left is this delivery's stock, at its own cost. Averaging over the negative figure divided this delivery's
                # cost by too few units (Rs 120 stock came out at Rs 444); its own cost per unit is right.
                product.avg_cost = _line_net_cost(line) / incoming_qty
            await product.save(update_fields=["avg_cost"])

        # A delivery that arrives with a new price updates the Item there and then, and the change is
        # logged like any other so its shelf tags can be reprinted.
        if line.newSalePrice is not None or line.newRetailPrice is not None:
            old_price, old_rpp = product.price, product.rpp
            if line.newSalePrice is not None:
                product.price = line.newSalePrice
            if line.newRetailPrice is not None:
                product.rpp = line.newRetailPrice
            if product.price != old_price or product.rpp != old_rpp:
                await product.save(update_fields=["price", "rpp"])
        await price_history_service.record(product, price_before, "receiving", grn.grn_number, user)

        # Bonus quantity genuinely adds to physical stock (contracts.md §6). The GRN number goes on the movement so
        # stock origin shows it came from a supplier, and its cost per unit so the books value it.
        await StockMovement.create(
            product=product, location=location, kind="receive",
            qty=incoming_qty, reason=grn.grn_number[:20], origin_user=user, at=datetime.now(timezone.utc),
            unit_cost=(line_net / incoming_qty) if incoming_qty > 0 else None,
        )
        if line.expiry:
            await Batch.create(product=product, lot_number=None, expiry=line.expiry, received_qty=line.qty + line.bonusQty)

    from datetime import timedelta

    from app.services.accounts_reports_service import shop_day

    cent = Decimal("0.01")
    grn.gross_total = gross_total.quantize(cent)
    grn.disc_total = disc_total.quantize(cent)
    grn.tax_total = tax_total.quantize(cent)
    # What's owed: goods after discounts and charges, their GST, and the advance tax.
    grn.net_total = (net_total + tax_total + payload.advanceTax).quantize(cent)
    grn.due_date = shop_day() + timedelta(days=supplier.due_days or 0)
    await grn.save(update_fields=["gross_total", "disc_total", "tax_total", "net_total", "due_date"])

    await OutboxEvent.create(
        aggregate_type="GRN", aggregate_id=str(grn.id),
        payload={"grnNumber": grn.grn_number, "locationId": payload.locationId, "supplierCode": supplier.code, "netTotal": str(grn.net_total)},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    await grn.fetch_related("lines")
    return grn


async def list_batches(product_id: str | None, limit: int, offset: int) -> tuple[list[Batch], int]:
    qs = Batch.all()
    if product_id:
        qs = qs.filter(product_id=product_id)
    total = await qs.count()
    batches = await qs.order_by("expiry").offset(offset).limit(limit)
    return batches, total


async def list_grns(
    limit: int, offset: int, from_at: datetime | None = None, to_at: datetime | None = None,
) -> tuple[list[GRN], int]:
    """Branch-wide, not terminal-scoped — the read path Receiving.tsx's recent-GRNs
    list needs instead of each browser's own local journal."""
    qs = in_window(GRN.all(), "at", from_at, to_at)
    total = await qs.count()
    grns = await qs.order_by("-at").offset(offset).limit(limit).prefetch_related("lines")
    return grns, total


async def _reason(kind: str, code: str):
    """Reasons are the branch's own list (Branch Console > Lists and Settings > Reasons)."""
    from app.services import masters_service

    try:
        return await masters_service.require_reason(kind, code)
    except masters_service.MastersError as exc:
        raise InventoryError(exc.message) from exc


@atomic()
async def create_purchase_return(user: User, payload: PurchaseReturnCreateRequest) -> PurchaseReturn:
    await _reason("purchase-return", payload.reason)
    # Returns still go back to a switched-off supplier: stopping buying from someone is exactly when
    # their leftover stock gets sent back.
    supplier = await Supplier.get_or_none(id=payload.supplierId)
    if not supplier:
        raise InventoryError(f"Unknown supplier {payload.supplierId}")
    location = await _active_location(payload.locationId)
    grn = None
    if payload.grnId:
        grn = await GRN.get_or_none(id=payload.grnId)
        if not grn:
            raise InventoryError(f"Unknown GRN {payload.grnId}")
    if not payload.lines:
        raise InventoryError("A purchase return needs at least one line")

    products: dict[str, Product] = {}
    for line in payload.lines:
        product = await Product.get_or_none(id=line.productId)
        if not product:
            raise InventoryError(f"Unknown product {line.productId}")
        products[line.productId] = product

    ret = await PurchaseReturn.create(
        return_number=await numbering_service.next_number("purchase_return", PurchaseReturn, "return_number", "PR-", 4), supplier=supplier, location=location, grn=grn,
        reason=payload.reason, notes=payload.notes or None, submitted_by=user,
    )

    grn_rates: dict[str, Decimal] = {}
    if grn:
        for gl in await GRNLine.filter(grn=grn):
            grn_rates.setdefault(str(gl.product_id), gl.tax_rate)
    value = tax = Decimal("0")
    for line in payload.lines:
        product = products[line.productId]
        rate = grn_rates.get(str(product.id), product.tax_rate or Decimal("0"))
        value += line.qty * line.unitPrice
        tax += line.qty * line.unitPrice * rate / Decimal("100")
        await PurchaseReturnLine.create(
            purchase_return=ret, product=product, qty=line.qty, unit_price=line.unitPrice, tax_rate=rate, unit_cost=product.avg_cost,
        )
        # Stock going out to the supplier: same ledger, negative qty, like a sale. Only what the location holds can go back.
        from app.services import stock_guard

        await stock_guard.require_held(product, location, line.qty, "this return to the supplier")
        await StockMovement.create(
            product=product, location=location, kind="purchase-return",
            qty=-line.qty, reason=ret.return_number[:20], origin_user=user, at=datetime.now(timezone.utc), unit_cost=product.avg_cost,
        )
    ret.total = (value + tax).quantize(Decimal("0.01"))
    ret.tax_total = tax.quantize(Decimal("0.01"))
    await ret.save(update_fields=["total", "tax_total"])

    await OutboxEvent.create(
        aggregate_type="PurchaseReturn", aggregate_id=str(ret.id),
        payload={"returnNumber": ret.return_number, "supplierId": payload.supplierId},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    await ret.fetch_related("lines")
    return ret


async def list_purchase_returns(
    limit: int, offset: int, from_at: datetime | None = None, to_at: datetime | None = None,
) -> tuple[list[PurchaseReturn], int]:
    """Branch-wide — the read path a Purchase Returns screen needs, same shape as GRNs."""
    qs = in_window(PurchaseReturn.all(), "at", from_at, to_at)
    total = await qs.count()
    returns = await qs.order_by("-at").offset(offset).limit(limit).prefetch_related("lines")
    return returns, total


@atomic()
async def submit_count(user: User, payload: CountSubmitRequest) -> PhysicalCount:
    product = await Product.get_or_none(id=payload.productId)
    if not product:
        raise InventoryError(f"Unknown product {payload.productId}")
    location = await _active_location(payload.locationId)
    system_qty = await balance_for(payload.productId, payload.locationId)
    count = await PhysicalCount.create(
        product=product, location=location, system_qty=system_qty,
        counted_qty=payload.countedQty, status="pending", counted_by=user,
    )
    await OutboxEvent.create(
        aggregate_type="PhysicalCount", aggregate_id=str(count.id),
        payload={"productId": payload.productId, "locationId": payload.locationId},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    return count


async def list_counts(
    limit: int, offset: int, from_at: datetime | None = None, to_at: datetime | None = None,
) -> tuple[list[PhysicalCount], int]:
    """Branch-wide, not terminal-scoped — the read path Counts.tsx, the Approvals Inbox and
    Reports' Staff & Work need instead of each browser's own local journal."""
    qs = in_window(PhysicalCount.all(), "at", from_at, to_at)
    total = await qs.count()
    counts = await qs.order_by("-at").offset(offset).limit(limit)
    return counts, total


def _refuse_own_work(decider: User, submitter_id, what: str) -> None:
    """Nobody signs off their own stock correction — whatever their role.

    The role templates keep approval away from Stock Keepers, but a template is only a starting
    grant: a Branch Manager can tick the box for anyone, and an Inventory Manager can submit a count
    as well as approve one. So the rule is enforced on the decision itself, not on who happens to
    hold the permission. If the person who reports a loss can also approve it, the report is
    worthless — that is the whole control, and it has to survive a permissions screen.
    """
    if submitter_id is not None and str(submitter_id) == str(decider.id):
        raise InventoryError(
            f"You submitted this {what}, so someone else has to decide it. "
            "A stock correction is only a control if a second person signs it off."
        )


async def _tell_submitter(user_id, kind: str, title: str, body: str | None, link: str, tone: str) -> None:
    from app.services import alerts_service

    await alerts_service.notify(kind, title, body=body, link=link, users=[str(user_id)], tone=tone)


@atomic()
async def approve_count(user: User, count_id: str) -> PhysicalCount:
    count = await PhysicalCount.get_or_none(id=count_id)
    if not count or count.status != "pending":
        raise InventoryError("Count not found or not pending")
    _refuse_own_work(user, count.counted_by_id, "count")
    delta = count.counted_qty - count.system_qty
    if delta != 0:
        product = await Product.get(id=count.product_id)
        if delta < 0:
            # Stock may have been sold or moved since the count was made; the difference must still leave something.
            from app.services import stock_guard

            held = await stock_guard.held_at(str(count.product_id), str(count.location_id))
            if held + delta < 0:
                raise InventoryError(
                    f"{product.name} has gone down to {stock_guard.qty_text(held)} since this count was made, so taking off its "
                    f"difference of {stock_guard.qty_text(-delta)} would leave less than nothing. Reject it and count again."
                )
        await StockMovement.create(
            product_id=count.product_id, location_id=count.location_id,
            kind="count-correction", qty=delta, origin_user=user, at=datetime.now(timezone.utc), unit_cost=product.avg_cost,
        )
    count.status = "approved"
    count.approved_by = user
    await count.save()
    await _tell_submitter(count.counted_by_id, "count.approved", f"Your stock count was approved by {user.name}",
                          f"The stock was corrected by {delta.normalize():f}." if delta else "It matched the system, so nothing changed.", "/inventory/counts", "good")
    await OutboxEvent.create(
        aggregate_type="PhysicalCount", aggregate_id=str(count.id),
        payload={"event": "approved", "delta": str(delta)}, origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    return count


@atomic()
async def submit_adjustment(user: User, payload: AdjustmentSubmitRequest) -> Adjustment:
    await _reason("adjustment", payload.reason)
    product = await Product.get_or_none(id=payload.productId)
    if not product:
        raise InventoryError(f"Unknown product {payload.productId}")
    location = await _active_location(payload.locationId)
    # An adjustment that takes stock off can't take more than the location holds, now or when it is approved.
    from app.models import ListEntry

    entry = await ListEntry.get_or_none(kind="adjustment", code=payload.reason)
    if not (entry.effect == "add" if entry else payload.reason == "found"):
        from app.services import stock_guard

        try:
            await stock_guard.require_held(product, location, payload.magnitude, "this adjustment")
        except stock_guard.StockShortError as exc:
            raise InventoryError(exc.message) from exc
    adjustment = await Adjustment.create(
        product=product, location=location, reason=payload.reason,
        magnitude=payload.magnitude, notes=payload.notes or None,
        status="pending", submitted_by=user,
    )
    await OutboxEvent.create(
        aggregate_type="Adjustment", aggregate_id=str(adjustment.id),
        payload={"productId": payload.productId, "reason": payload.reason},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    return adjustment


async def list_adjustments(
    limit: int, offset: int, from_at: datetime | None = None, to_at: datetime | None = None,
) -> tuple[list[Adjustment], int]:
    """Branch-wide, not terminal-scoped — the read path Adjustments.tsx, the Approvals Inbox
    and Reports' Staff & Work need instead of each browser's own local journal."""
    qs = in_window(Adjustment.all(), "at", from_at, to_at)
    total = await qs.count()
    adjustments = await qs.order_by("-at").offset(offset).limit(limit)
    return adjustments, total


@atomic()
async def approve_adjustment(user: User, adjustment_id: str) -> Adjustment:
    adjustment = await Adjustment.get_or_none(id=adjustment_id)
    if not adjustment or adjustment.status != "pending":
        raise InventoryError("Adjustment not found or not pending")
    _refuse_own_work(user, adjustment.submitted_by_id, "adjustment")
    # A reason that adds stock (found) puts it back; every other reason takes it off. A reason switched off since the
    # adjustment was submitted still decides it the same way.
    from app.models import ListEntry

    entry = await ListEntry.get_or_none(kind="adjustment", code=adjustment.reason)
    adds = entry.effect == "add" if entry else adjustment.reason == "found"
    signed_qty = adjustment.magnitude if adds else -adjustment.magnitude
    product = await Product.get(id=adjustment.product_id)
    if not adds:
        from app.models import Location
        from app.services import stock_guard

        location = await Location.get(id=adjustment.location_id)
        try:
            await stock_guard.require_held(product, location, adjustment.magnitude, "this adjustment")
        except stock_guard.StockShortError as exc:
            raise InventoryError(exc.message + " Reject it, or count the shelf first.") from exc
    await StockMovement.create(
        product_id=adjustment.product_id, location_id=adjustment.location_id,
        kind="adjust", qty=signed_qty, reason=adjustment.reason, origin_user=user, at=datetime.now(timezone.utc), unit_cost=product.avg_cost,
    )
    adjustment.status = "approved"
    adjustment.decided_by = user
    await adjustment.save()
    label = (entry.name if entry else adjustment.reason).lower()
    await _tell_submitter(adjustment.submitted_by_id, "adjustment.approved", f"Your {label} adjustment was approved by {user.name}",
                          None, "/inventory/adjustments", "good")
    await OutboxEvent.create(
        aggregate_type="Adjustment", aggregate_id=str(adjustment.id),
        payload={"event": "approved", "signedQty": str(signed_qty)}, origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    return adjustment


@atomic()
async def reject_adjustment(user: User, adjustment_id: str) -> Adjustment:
    adjustment = await Adjustment.get_or_none(id=adjustment_id)
    if not adjustment or adjustment.status != "pending":
        raise InventoryError("Adjustment not found or not pending")
    # Rejecting is a decision too. Letting someone reject their own submission would let them
    # quietly withdraw a loss report after it had been seen.
    _refuse_own_work(user, adjustment.submitted_by_id, "adjustment")
    adjustment.status = "rejected"
    adjustment.decided_by = user
    await adjustment.save()
    from app.models import ListEntry

    entry = await ListEntry.get_or_none(kind="adjustment", code=adjustment.reason)
    label = (entry.name if entry else adjustment.reason).lower()
    await _tell_submitter(adjustment.submitted_by_id, "adjustment.rejected", f"Your {label} adjustment was rejected by {user.name}",
                          "Nothing changed in stock. Talk to them if it was right.", "/inventory/adjustments", "bad")
    await OutboxEvent.create(
        aggregate_type="Adjustment", aggregate_id=str(adjustment.id),
        payload={"event": "rejected"}, origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    return adjustment
