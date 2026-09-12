from fastapi import HTTPException, status

from app.models import GRN, Adjustment, Batch, PhysicalCount, PurchaseReturn, StockMovement, Transfer, User
from app.schemas.inventory import (
    AdjustmentListOut,
    AdjustmentOut,
    AdjustmentSubmitRequest,
    BalanceOut,
    BatchListOut,
    BatchOut,
    CountListOut,
    CountOut,
    CountSubmitRequest,
    DisputeRequest,
    GRNCreateRequest,
    GRNLineOut,
    GRNListOut,
    GRNOut,
    PurchaseReturnCreateRequest,
    PurchaseReturnLineOut,
    PurchaseReturnListOut,
    PurchaseReturnOut,
    StockMovementListOut,
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


async def list_movements(product_id: str | None, location_id: str | None, limit: int, offset: int) -> StockMovementListOut:
    limit = min(max(limit, 1), 5000)
    offset = max(offset, 0)
    movements, total = await inventory_service.list_movements(product_id, location_id, limit, offset)
    return StockMovementListOut(items=[_movement_out(m) for m in movements], total=total)


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


async def list_grns(limit: int, offset: int) -> GRNListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    grns, total = await inventory_service.list_grns(limit, offset)
    return GRNListOut(items=[_grn_out(g) for g in grns], total=total)


def _purchase_return_out(r: PurchaseReturn) -> PurchaseReturnOut:
    return PurchaseReturnOut(
        id=str(r.id), returnNumber=r.return_number, supplierId=str(r.supplier_id),
        locationId=str(r.location_id), grnId=str(r.grn_id) if r.grn_id else None,
        reason=r.reason, notes=r.notes, submittedByUserId=str(r.submitted_by_id), at=r.at,
        lines=[PurchaseReturnLineOut(productId=str(l.product_id), qty=l.qty, unitPrice=l.unit_price) for l in r.lines],
    )


async def create_purchase_return(user: User, payload: PurchaseReturnCreateRequest) -> PurchaseReturnOut:
    try:
        ret = await inventory_service.create_purchase_return(user, payload)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _purchase_return_out(ret)


async def list_purchase_returns(limit: int, offset: int) -> PurchaseReturnListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    returns, total = await inventory_service.list_purchase_returns(limit, offset)
    return PurchaseReturnListOut(items=[_purchase_return_out(r) for r in returns], total=total)


def _batch_out(b: Batch) -> BatchOut:
    return BatchOut(id=str(b.id), productId=str(b.product_id), lotNumber=b.lot_number, expiry=b.expiry, receivedQty=b.received_qty)


async def list_batches(product_id: str | None, limit: int, offset: int) -> BatchListOut:
    limit = min(max(limit, 1), 5000)
    offset = max(offset, 0)
    batches, total = await inventory_service.list_batches(product_id, limit, offset)
    return BatchListOut(items=[_batch_out(b) for b in batches], total=total)


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


async def list_counts(limit: int, offset: int) -> CountListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    counts, total = await inventory_service.list_counts(limit, offset)
    return CountListOut(items=[_count_out(c) for c in counts], total=total)


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


async def list_adjustments(limit: int, offset: int) -> AdjustmentListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    adjustments, total = await inventory_service.list_adjustments(limit, offset)
    return AdjustmentListOut(items=[_adjustment_out(a) for a in adjustments], total=total)


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
