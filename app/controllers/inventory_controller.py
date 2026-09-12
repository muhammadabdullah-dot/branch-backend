from fastapi import HTTPException, status

from app.models import GRN, Adjustment, Batch, PhysicalCount, Product, PurchaseReturn, StockMovement, Transfer, User
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
    StockValueOut,
    TransferLineOut,
    TransferOut,
)
from app.services import inventory_service, transfers_service

# Well under SQLite's parameter cap, so a full 5000-row movements page still resolves in a
# handful of queries rather than blowing the IN(...) limit.
_NAME_CHUNK = 400


async def _names_for(product_ids: set[str]) -> dict[str, Product]:
    """One bulk lookup per response instead of a join per row — and the reason every payload
    below can carry a real item name. Without it the frontend has to resolve ids against its own
    cached page of the catalog, which at 47k products means most rows render as a bare code."""
    ids = [i for i in product_ids if i]
    found: dict[str, Product] = {}
    for start in range(0, len(ids), _NAME_CHUNK):
        for p in await Product.filter(id__in=ids[start:start + _NAME_CHUNK]).only("id", "name", "sku"):
            found[str(p.id)] = p
    return found


def _name_of(names: dict[str, Product], product_id: str) -> tuple[str | None, str | None]:
    p = names.get(product_id)
    return (p.name, p.sku) if p else (None, None)


async def get_balance(product_id: str, location_id: str | None) -> BalanceOut:
    bal = await inventory_service.balance(product_id, location_id)
    return BalanceOut(productId=product_id, locationId=location_id, balance=bal)


async def get_stock_value() -> StockValueOut:
    return StockValueOut(**await inventory_service.stock_value())


def _movement_out(m: StockMovement, names: dict[str, Product]) -> StockMovementOut:
    product_id = str(m.product_id)
    name, sku = _name_of(names, product_id)
    return StockMovementOut(
        id=str(m.id), productId=product_id, productName=name, productSku=sku,
        locationId=str(m.location_id), kind=m.kind, qty=m.qty, reason=m.reason,
        originUserId=str(m.origin_user_id) if m.origin_user_id else None, at=m.at,
    )


async def list_movements(product_id: str | None, location_id: str | None, limit: int, offset: int) -> StockMovementListOut:
    limit = min(max(limit, 1), 5000)
    offset = max(offset, 0)
    movements, total = await inventory_service.list_movements(product_id, location_id, limit, offset)
    names = await _names_for({str(m.product_id) for m in movements})
    return StockMovementListOut(items=[_movement_out(m, names) for m in movements], total=total)


def _grn_out(grn: GRN, names: dict[str, Product]) -> GRNOut:
    lines = []
    for l in grn.lines:
        product_id = str(l.product_id)
        name, sku = _name_of(names, product_id)
        lines.append(GRNLineOut(
            productId=product_id, productName=name, productSku=sku,
            qty=l.qty, bonusQty=l.bonus_qty, unitPrice=l.unit_price,
            discPercent=l.disc_percent, expiry=l.expiry, taxRate=l.tax_rate,
        ))
    return GRNOut(
        id=str(grn.id), grnNumber=grn.grn_number, supplierId=str(grn.supplier_id),
        partyInvNo=grn.party_inv_no, locationId=str(grn.location_id), gstMode=grn.gst_mode,
        advanceTax=grn.advance_tax, approved=grn.approved, receivedByUserId=str(grn.received_by_id),
        at=grn.at, lines=lines,
    )


def _grn_product_ids(grns: list[GRN]) -> set[str]:
    return {str(l.product_id) for g in grns for l in g.lines}


async def receive_grn(user: User, payload: GRNCreateRequest) -> GRNOut:
    try:
        grn = await inventory_service.receive_grn(user, payload)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _grn_out(grn, await _names_for(_grn_product_ids([grn])))


async def list_grns(limit: int, offset: int) -> GRNListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    grns, total = await inventory_service.list_grns(limit, offset)
    names = await _names_for(_grn_product_ids(grns))
    return GRNListOut(items=[_grn_out(g, names) for g in grns], total=total)


def _purchase_return_out(r: PurchaseReturn, names: dict[str, Product]) -> PurchaseReturnOut:
    lines = []
    for l in r.lines:
        product_id = str(l.product_id)
        name, sku = _name_of(names, product_id)
        lines.append(PurchaseReturnLineOut(
            productId=product_id, productName=name, productSku=sku, qty=l.qty, unitPrice=l.unit_price,
        ))
    return PurchaseReturnOut(
        id=str(r.id), returnNumber=r.return_number, supplierId=str(r.supplier_id),
        locationId=str(r.location_id), grnId=str(r.grn_id) if r.grn_id else None,
        reason=r.reason, notes=r.notes, submittedByUserId=str(r.submitted_by_id), at=r.at, lines=lines,
    )


def _purchase_return_product_ids(returns: list[PurchaseReturn]) -> set[str]:
    return {str(l.product_id) for r in returns for l in r.lines}


async def create_purchase_return(user: User, payload: PurchaseReturnCreateRequest) -> PurchaseReturnOut:
    try:
        ret = await inventory_service.create_purchase_return(user, payload)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _purchase_return_out(ret, await _names_for(_purchase_return_product_ids([ret])))


async def list_purchase_returns(limit: int, offset: int) -> PurchaseReturnListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    returns, total = await inventory_service.list_purchase_returns(limit, offset)
    names = await _names_for(_purchase_return_product_ids(returns))
    return PurchaseReturnListOut(items=[_purchase_return_out(r, names) for r in returns], total=total)


def _batch_out(b: Batch, names: dict[str, Product]) -> BatchOut:
    product_id = str(b.product_id)
    name, sku = _name_of(names, product_id)
    return BatchOut(
        id=str(b.id), productId=product_id, productName=name, productSku=sku,
        lotNumber=b.lot_number, expiry=b.expiry, receivedQty=b.received_qty,
    )


async def list_batches(product_id: str | None, limit: int, offset: int) -> BatchListOut:
    limit = min(max(limit, 1), 5000)
    offset = max(offset, 0)
    batches, total = await inventory_service.list_batches(product_id, limit, offset)
    names = await _names_for({str(b.product_id) for b in batches})
    return BatchListOut(items=[_batch_out(b, names) for b in batches], total=total)


def _count_out(c: PhysicalCount, names: dict[str, Product]) -> CountOut:
    product_id = str(c.product_id)
    name, sku = _name_of(names, product_id)
    return CountOut(
        id=str(c.id), productId=product_id, productName=name, productSku=sku,
        locationId=str(c.location_id), systemQty=c.system_qty, countedQty=c.counted_qty, status=c.status,
        countedByUserId=str(c.counted_by_id), approvedByUserId=str(c.approved_by_id) if c.approved_by_id else None,
        at=c.at,
    )


async def submit_count(user: User, payload: CountSubmitRequest) -> CountOut:
    try:
        count = await inventory_service.submit_count(user, payload)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _count_out(count, await _names_for({str(count.product_id)}))


async def list_counts(limit: int, offset: int) -> CountListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    counts, total = await inventory_service.list_counts(limit, offset)
    names = await _names_for({str(c.product_id) for c in counts})
    return CountListOut(items=[_count_out(c, names) for c in counts], total=total)


async def approve_count(user: User, count_id: str) -> CountOut:
    try:
        count = await inventory_service.approve_count(user, count_id)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _count_out(count, await _names_for({str(count.product_id)}))


def _adjustment_out(a: Adjustment, names: dict[str, Product]) -> AdjustmentOut:
    product_id = str(a.product_id)
    name, sku = _name_of(names, product_id)
    return AdjustmentOut(
        id=str(a.id), productId=product_id, productName=name, productSku=sku,
        locationId=str(a.location_id), reason=a.reason,
        magnitude=a.magnitude, notes=a.notes, status=a.status, submittedByUserId=str(a.submitted_by_id),
        decidedByUserId=str(a.decided_by_id) if a.decided_by_id else None, at=a.at,
    )


async def submit_adjustment(user: User, payload: AdjustmentSubmitRequest) -> AdjustmentOut:
    try:
        adjustment = await inventory_service.submit_adjustment(user, payload)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _adjustment_out(adjustment, await _names_for({str(adjustment.product_id)}))


async def list_adjustments(limit: int, offset: int) -> AdjustmentListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    adjustments, total = await inventory_service.list_adjustments(limit, offset)
    names = await _names_for({str(a.product_id) for a in adjustments})
    return AdjustmentListOut(items=[_adjustment_out(a, names) for a in adjustments], total=total)


async def approve_adjustment(user: User, adjustment_id: str) -> AdjustmentOut:
    try:
        adjustment = await inventory_service.approve_adjustment(user, adjustment_id)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _adjustment_out(adjustment, await _names_for({str(adjustment.product_id)}))


async def reject_adjustment(user: User, adjustment_id: str) -> AdjustmentOut:
    try:
        adjustment = await inventory_service.reject_adjustment(user, adjustment_id)
    except inventory_service.InventoryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _adjustment_out(adjustment, await _names_for({str(adjustment.product_id)}))


def _transfer_out(t: Transfer, names: dict[str, Product]) -> TransferOut:
    lines = []
    for l in t.lines:
        product_id = str(l.product_id)
        name, sku = _name_of(names, product_id)
        lines.append(TransferLineOut(
            productId=product_id, productName=name, productSku=sku,
            qtySent=l.qty_sent, qtyReceived=l.qty_received,
        ))
    return TransferOut(
        id=str(t.id), fromWarehouse=t.from_warehouse, status=t.status, lines=lines,
        vehicle=t.vehicle, driver=t.driver, requestedAt=t.requested_at, dispatchedAt=t.dispatched_at,
        receivedAt=t.received_at, disputeOpen=t.dispute_open, disputeNote=t.dispute_note,
    )


async def list_transfers() -> list[TransferOut]:
    transfers = await transfers_service.list_all()
    names = await _names_for({str(l.product_id) for t in transfers for l in t.lines})
    return [_transfer_out(t, names) for t in transfers]


async def open_dispute(user: User, transfer_id: str, payload: DisputeRequest) -> TransferOut:
    try:
        transfer = await transfers_service.open_dispute(user, transfer_id, payload.note)
    except transfers_service.TransferError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _transfer_out(transfer, await _names_for({str(l.product_id) for l in transfer.lines}))
