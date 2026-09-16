from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException

from app.models import PurchaseOrder, User
from app.schemas.purchase_orders import (
    PurchaseOrderCreate, PurchaseOrderLineOut, PurchaseOrderListOut, PurchaseOrderOut, PurchaseOrderUpdate,
)
from app.services import purchase_order_service


async def _to_out(po: PurchaseOrder) -> PurchaseOrderOut:
    lines = sorted(po.lines, key=lambda line: line.position)
    value = Decimal("0")
    out_lines = []
    for line in lines:
        value += line.qty * line.unit_price * (Decimal("1") - line.disc_percent / Decimal("100"))
        out_lines.append(PurchaseOrderLineOut(
            productId=str(line.product_id), productName=line.product.name, productSku=line.product.sku,
            qty=line.qty, unitPrice=line.unit_price, discPercent=line.disc_percent,
            receivedQty=line.received_qty, remainingQty=max(Decimal("0"), line.qty - line.received_qty),
        ))
    return PurchaseOrderOut(
        id=str(po.id), poNumber=po.po_number, supplierId=str(po.supplier_id), supplierName=po.supplier.name,
        locationId=str(po.location_id), status=po.status, expectedAt=po.expected_at, notes=po.notes,
        createdByUserId=str(po.created_by_id), createdByName=po.created_by.name if po.created_by else None,
        approvedByUserId=str(po.approved_by_id) if po.approved_by_id else None,
        approvedByName=po.approved_by.name if po.approved_by_id and po.approved_by else None,
        createdAt=po.created_at, approvedAt=po.approved_at, closedAt=po.closed_at,
        orderValue=value.quantize(Decimal("0.01")), lines=out_lines,
        grnNumbers=sorted(g.grn_number for g in po.grns),
    )


def _raise(exc: purchase_order_service.PurchaseOrderError) -> None:
    raise HTTPException(exc.status, exc.message)


async def list_all(
    limit: int, offset: int, from_at: datetime | None, to_at: datetime | None, status: str | None, supplier_id: str | None,
) -> PurchaseOrderListOut:
    limit = min(max(limit, 1), 500)
    orders, total = await purchase_order_service.list_all(limit, max(offset, 0), from_at, to_at, status, supplier_id)
    return PurchaseOrderListOut(items=[await _to_out(po) for po in orders], total=total)


async def get(po_id: str) -> PurchaseOrderOut:
    try:
        return await _to_out(await purchase_order_service.get(po_id))
    except purchase_order_service.PurchaseOrderError as exc:
        _raise(exc)


async def create(user: User, data: PurchaseOrderCreate) -> PurchaseOrderOut:
    try:
        return await _to_out(await purchase_order_service.create(user, data))
    except purchase_order_service.PurchaseOrderError as exc:
        _raise(exc)


async def update(user: User, po_id: str, data: PurchaseOrderUpdate) -> PurchaseOrderOut:
    try:
        return await _to_out(await purchase_order_service.update(user, po_id, data))
    except purchase_order_service.PurchaseOrderError as exc:
        _raise(exc)


async def approve(user: User, po_id: str) -> PurchaseOrderOut:
    try:
        return await _to_out(await purchase_order_service.approve(user, po_id))
    except purchase_order_service.PurchaseOrderError as exc:
        _raise(exc)


async def cancel(user: User, po_id: str) -> PurchaseOrderOut:
    try:
        return await _to_out(await purchase_order_service.cancel(user, po_id))
    except purchase_order_service.PurchaseOrderError as exc:
        _raise(exc)
