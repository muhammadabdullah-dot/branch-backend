"""Inventory domain — the ledger everything else folds from (contracts.md §6, first bullet).
Balances are never stored, only computed. GRN/Counts/Adjustments each post through the same
StockMovement table I3's Sales/Returns already write to.
"""
from datetime import datetime, timezone
from decimal import Decimal

from tortoise import Tortoise
from tortoise.transactions import atomic

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
    next_value,
)
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


async def list_movements(
    product_id: str | None, location_id: str | None, limit: int, offset: int
) -> tuple[list[StockMovement], int]:
    qs = StockMovement.all()
    if product_id:
        qs = qs.filter(product_id=product_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    total = await qs.count()
    movements = await qs.order_by("-at").offset(offset).limit(limit)
    return movements, total


@atomic()
async def receive_grn(user: User, payload: GRNCreateRequest) -> GRN:
    supplier = await Supplier.get_or_none(id=payload.supplierId)
    if not supplier:
        raise InventoryError(f"Unknown supplier {payload.supplierId}")
    location = await Location.get_or_none(id=payload.locationId)
    if not location:
        raise InventoryError(f"Unknown location {payload.locationId}")
    if not payload.lines:
        raise InventoryError("A GRN needs at least one line")

    products: dict[str, Product] = {}
    for line in payload.lines:
        product = await Product.get_or_none(id=line.productId)
        if not product:
            raise InventoryError(f"Unknown product {line.productId}")
        products[line.productId] = product

    seq = await next_value("grn", 11)
    grn = await GRN.create(
        grn_number=f"GRN-{seq:04d}",
        supplier=supplier,
        party_inv_no=payload.partyInvNo,
        location=location,
        gst_mode=payload.gstMode,
        advance_tax=payload.advanceTax,
        approved=True,
        received_by=user,
    )

    for line in payload.lines:
        product = products[line.productId]
        await GRNLine.create(
            grn=grn, product=product, qty=line.qty, bonus_qty=line.bonusQty,
            unit_price=line.unitPrice, disc_percent=line.discPercent,
            expiry=line.expiry, tax_rate=line.taxRate,
        )

        # Weighted-average cost basis (contracts.md gap: no COGS/margin basis existed anywhere).
        # Bonus units are free — they dilute the average, never raise it, matching real costing.
        old_balance = await balance_for(product.id)
        incoming_qty = line.qty + line.bonusQty
        new_total_qty = old_balance + incoming_qty
        if new_total_qty > 0:
            old_cost_value = product.avg_cost * old_balance if old_balance > 0 else Decimal("0")
            product.avg_cost = (old_cost_value + line.qty * line.unitPrice) / new_total_qty
            await product.save(update_fields=["avg_cost"])

        # Bonus quantity genuinely adds to physical stock (contracts.md §6).
        await StockMovement.create(
            product=product, location=location, kind="receive",
            qty=incoming_qty, origin_user=user, at=datetime.now(timezone.utc),
        )
        if line.expiry:
            await Batch.create(product=product, lot_number=None, expiry=line.expiry, received_qty=line.qty + line.bonusQty)

    await OutboxEvent.create(
        aggregate_type="GRN", aggregate_id=str(grn.id),
        payload={"grnNumber": grn.grn_number, "locationId": payload.locationId},
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


async def list_grns(limit: int, offset: int) -> tuple[list[GRN], int]:
    """Branch-wide, not terminal-scoped — the read path Receiving.tsx's recent-GRNs
    list needs instead of each browser's own local journal."""
    qs = GRN.all()
    total = await qs.count()
    grns = await qs.order_by("-at").offset(offset).limit(limit).prefetch_related("lines")
    return grns, total


PURCHASE_RETURN_REASONS = ("damaged", "expired", "wrong-item", "overstock", "other")


@atomic()
async def create_purchase_return(user: User, payload: PurchaseReturnCreateRequest) -> PurchaseReturn:
    if payload.reason not in PURCHASE_RETURN_REASONS:
        raise InventoryError(f"Invalid reason {payload.reason}")
    supplier = await Supplier.get_or_none(id=payload.supplierId)
    if not supplier:
        raise InventoryError(f"Unknown supplier {payload.supplierId}")
    location = await Location.get_or_none(id=payload.locationId)
    if not location:
        raise InventoryError(f"Unknown location {payload.locationId}")
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

    seq = await next_value("purchase_return", 1)
    ret = await PurchaseReturn.create(
        return_number=f"PR-{seq:04d}", supplier=supplier, location=location, grn=grn,
        reason=payload.reason, notes=payload.notes or None, submitted_by=user,
    )

    for line in payload.lines:
        product = products[line.productId]
        await PurchaseReturnLine.create(
            purchase_return=ret, product=product, qty=line.qty, unit_price=line.unitPrice,
        )
        # Stock going out to the supplier — same ledger, negative qty, mirroring how a sale's
        # own "sell" movement is posted. No stock-on-hand check: the rest of this system (sales
        # included) already allows a balance to go negative rather than blocking on it.
        await StockMovement.create(
            product=product, location=location, kind="purchase-return",
            qty=-line.qty, origin_user=user, at=datetime.now(timezone.utc),
        )

    await OutboxEvent.create(
        aggregate_type="PurchaseReturn", aggregate_id=str(ret.id),
        payload={"returnNumber": ret.return_number, "supplierId": payload.supplierId},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    await ret.fetch_related("lines")
    return ret


async def list_purchase_returns(limit: int, offset: int) -> tuple[list[PurchaseReturn], int]:
    """Branch-wide — the read path a Purchase Returns screen needs, same shape as GRNs."""
    qs = PurchaseReturn.all()
    total = await qs.count()
    returns = await qs.order_by("-at").offset(offset).limit(limit).prefetch_related("lines")
    return returns, total


@atomic()
async def submit_count(user: User, payload: CountSubmitRequest) -> PhysicalCount:
    product = await Product.get_or_none(id=payload.productId)
    if not product:
        raise InventoryError(f"Unknown product {payload.productId}")
    location = await Location.get_or_none(id=payload.locationId)
    if not location:
        raise InventoryError(f"Unknown location {payload.locationId}")
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


async def list_counts(limit: int, offset: int) -> tuple[list[PhysicalCount], int]:
    """Branch-wide, not terminal-scoped — the read path Counts.tsx, the Approvals Inbox and
    Reports' Staff & Work need instead of each browser's own local journal."""
    qs = PhysicalCount.all()
    total = await qs.count()
    counts = await qs.order_by("-at").offset(offset).limit(limit)
    return counts, total


@atomic()
async def approve_count(user: User, count_id: str) -> PhysicalCount:
    count = await PhysicalCount.get_or_none(id=count_id)
    if not count or count.status != "pending":
        raise InventoryError("Count not found or not pending")
    delta = count.counted_qty - count.system_qty
    if delta != 0:
        await StockMovement.create(
            product_id=count.product_id, location_id=count.location_id,
            kind="count-correction", qty=delta, origin_user=user, at=datetime.now(timezone.utc),
        )
    count.status = "approved"
    count.approved_by = user
    await count.save()
    await OutboxEvent.create(
        aggregate_type="PhysicalCount", aggregate_id=str(count.id),
        payload={"event": "approved", "delta": str(delta)}, origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    return count


@atomic()
async def submit_adjustment(user: User, payload: AdjustmentSubmitRequest) -> Adjustment:
    if payload.reason not in ("damage", "expiry", "found"):
        raise InventoryError(f"Invalid reason {payload.reason}")
    product = await Product.get_or_none(id=payload.productId)
    if not product:
        raise InventoryError(f"Unknown product {payload.productId}")
    location = await Location.get_or_none(id=payload.locationId)
    if not location:
        raise InventoryError(f"Unknown location {payload.locationId}")
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


async def list_adjustments(limit: int, offset: int) -> tuple[list[Adjustment], int]:
    """Branch-wide, not terminal-scoped — the read path Adjustments.tsx, the Approvals Inbox
    and Reports' Staff & Work need instead of each browser's own local journal."""
    qs = Adjustment.all()
    total = await qs.count()
    adjustments = await qs.order_by("-at").offset(offset).limit(limit)
    return adjustments, total


@atomic()
async def approve_adjustment(user: User, adjustment_id: str) -> Adjustment:
    adjustment = await Adjustment.get_or_none(id=adjustment_id)
    if not adjustment or adjustment.status != "pending":
        raise InventoryError("Adjustment not found or not pending")
    signed_qty = adjustment.magnitude if adjustment.reason == "found" else -adjustment.magnitude
    await StockMovement.create(
        product_id=adjustment.product_id, location_id=adjustment.location_id,
        kind="adjust", qty=signed_qty, reason=adjustment.reason, origin_user=user, at=datetime.now(timezone.utc),
    )
    adjustment.status = "approved"
    adjustment.decided_by = user
    await adjustment.save()
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
    adjustment.status = "rejected"
    adjustment.decided_by = user
    await adjustment.save()
    await OutboxEvent.create(
        aggregate_type="Adjustment", aggregate_id=str(adjustment.id),
        payload={"event": "rejected"}, origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    return adjustment
