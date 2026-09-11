"""Inventory domain — the ledger everything else folds from (contracts.md §6, first bullet).
Balances are never stored, only computed. GRN/Counts/Adjustments each post through the same
StockMovement table I3's Sales/Returns already write to.
"""
from datetime import datetime, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.models import (
    GRN,
    Adjustment,
    Batch,
    GRNLine,
    Location,
    OutboxEvent,
    PhysicalCount,
    Product,
    StockMovement,
    Supplier,
    User,
    balance_for,
    next_value,
)
from app.schemas.inventory import AdjustmentSubmitRequest, CountSubmitRequest, GRNCreateRequest

ZERO = Decimal("0")


class InventoryError(Exception):
    def __init__(self, message: str):
        self.message = message


async def balance(product_id: str, location_id: str | None = None) -> Decimal:
    return await balance_for(product_id, location_id)


async def list_movements(product_id: str | None = None, location_id: str | None = None) -> list[StockMovement]:
    qs = StockMovement.all()
    if product_id:
        qs = qs.filter(product_id=product_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    return await qs.order_by("-at")


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
        # Bonus quantity genuinely adds to physical stock (contracts.md §6).
        await StockMovement.create(
            product=product, location=location, kind="receive",
            qty=line.qty + line.bonusQty, origin_user=user, at=datetime.now(timezone.utc),
        )
        if line.expiry:
            await Batch.create(product=product, lot_number=None, expiry=line.expiry, received_qty=line.qty + line.bonusQty)

    await OutboxEvent.create(
        aggregate_type="GRN", aggregate_id=str(grn.id),
        payload={"grnNumber": grn.grn_number, "locationId": payload.locationId},
        origin_user_id=str(user.id),
    )
    await grn.fetch_related("lines")
    return grn


async def list_batches(product_id: str | None = None) -> list[Batch]:
    qs = Batch.all()
    if product_id:
        qs = qs.filter(product_id=product_id)
    return await qs.order_by("expiry")


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
        origin_user_id=str(user.id),
    )
    return count


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
        payload={"event": "approved", "delta": str(delta)}, origin_user_id=str(user.id),
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
        origin_user_id=str(user.id),
    )
    return adjustment


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
        payload={"event": "approved", "signedQty": str(signed_qty)}, origin_user_id=str(user.id),
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
        payload={"event": "rejected"}, origin_user_id=str(user.id),
    )
    return adjustment
