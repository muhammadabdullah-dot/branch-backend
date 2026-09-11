from fastapi import HTTPException, status

from app.models import GRN, Adjustment, Batch, PhysicalCount, StockMovement, Transfer, User
from app.schemas.inventory import (
    AdjustmentOut,
    AdjustmentSubmitRequest,
    BalanceOut,
    BatchOut,
    CountOut,
    CountSubmitRequest,
    DisputeRequest,
    GRNCreateRequest,
    GRNLineOut,
    GRNOut,
    StockMovementOut,
    TransferLineOut,
    TransferOut,
)
from app.services import inventory_service, transfers_service


async def get_balance(product_id: str, location_id: str | None) -> BalanceOut:
    bal = await inventory_service.balance(product_id, location_id)
    return BalanceOut(productId=product_id, locationId=location_id, balance=bal)


def _movement_out(m: StockMovement) -> StockMovementOut:
    return StockMovementOut(
        id=str(m.id), productId=str(m.product_id), locationId=str(m.location_id),
        kind=m.kind, qty=m.qty, reason=m.reason,
        originUserId=str(m.origin_user_id) if m.origin_user_id else None, at=m.at,
    )


async def list_movements(product_id: str | None, location_id: str | None) -> list[StockMovementOut]:
    movements = await inventory_service.list_movements(product_id, location_id)
    return [_movement_out(m) for m in movements]


def _grn_out(grn: GRN) -> GRNOut:
    return GRNOut(
        id=str(grn.id), grnNumber=grn.grn_number, supplierId=str(grn.supplier_id),
        partyInvNo=grn.party_inv_no, locationId=str(grn.location_id), gstMode=grn.gst_mode,
        advanceTax=grn.advance_tax, approved=grn.approved, receivedByUserId=str(grn.received_by_id),
        at=grn.at,
        lines=[
            GRNLineOut(
                productId=str(l.product_id), qty=l.qty, bonusQty=l.bonus_qty, unitPrice=l.unit_price,
                discPercent=l.disc_percent, expiry=l.expiry, taxRate=l.tax_rate,
            )
            for l in grn.lines
        ],
    )


async def receive_grn(user: User, payload: GRNCreateRequest) -> GRNOut:
    try:
        grn = await inventory_service.receive_grn(user, payload)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _grn_out(grn)


def _batch_out(b: Batch) -> BatchOut:
    return BatchOut(id=str(b.id), productId=str(b.product_id), lotNumber=b.lot_number, expiry=b.expiry, receivedQty=b.received_qty)


async def list_batches(product_id: str | None) -> list[BatchOut]:
    return [_batch_out(b) for b in await inventory_service.list_batches(product_id)]


def _count_out(c: PhysicalCount) -> CountOut:
    return CountOut(
        id=str(c.id), productId=str(c.product_id), locationId=str(c.location_id),
        systemQty=c.system_qty, countedQty=c.counted_qty, status=c.status,
        countedByUserId=str(c.counted_by_id), approvedByUserId=str(c.approved_by_id) if c.approved_by_id else None,
        at=c.at,
    )


async def submit_count(user: User, payload: CountSubmitRequest) -> CountOut:
    try:
        count = await inventory_service.submit_count(user, payload)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _count_out(count)


async def approve_count(user: User, count_id: str) -> CountOut:
    try:
        count = await inventory_service.approve_count(user, count_id)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _count_out(count)


def _adjustment_out(a: Adjustment) -> AdjustmentOut:
    return AdjustmentOut(
        id=str(a.id), productId=str(a.product_id), locationId=str(a.location_id), reason=a.reason,
        magnitude=a.magnitude, notes=a.notes, status=a.status, submittedByUserId=str(a.submitted_by_id),
        decidedByUserId=str(a.decided_by_id) if a.decided_by_id else None, at=a.at,
    )


async def submit_adjustment(user: User, payload: AdjustmentSubmitRequest) -> AdjustmentOut:
    try:
        adjustment = await inventory_service.submit_adjustment(user, payload)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _adjustment_out(adjustment)


async def approve_adjustment(user: User, adjustment_id: str) -> AdjustmentOut:
    try:
        adjustment = await inventory_service.approve_adjustment(user, adjustment_id)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _adjustment_out(adjustment)


async def reject_adjustment(user: User, adjustment_id: str) -> AdjustmentOut:
    try:
        adjustment = await inventory_service.reject_adjustment(user, adjustment_id)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _adjustment_out(adjustment)


def _transfer_out(t: Transfer) -> TransferOut:
    return TransferOut(
        id=str(t.id), fromWarehouse=t.from_warehouse, status=t.status,
        lines=[TransferLineOut(productId=str(l.product_id), qtySent=l.qty_sent, qtyReceived=l.qty_received) for l in t.lines],
        vehicle=t.vehicle, driver=t.driver, requestedAt=t.requested_at, dispatchedAt=t.dispatched_at,
        receivedAt=t.received_at, disputeOpen=t.dispute_open, disputeNote=t.dispute_note,
    )


async def list_transfers() -> list[TransferOut]:
    return [_transfer_out(t) for t in await transfers_service.list_all()]


async def open_dispute(user: User, transfer_id: str, payload: DisputeRequest) -> TransferOut:
    try:
        transfer = await transfers_service.open_dispute(user, transfer_id, payload.note)
    except transfers_service.TransferError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _transfer_out(transfer)
