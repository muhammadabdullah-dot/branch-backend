from datetime import datetime

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.controllers import purchase_order_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.inventory import GRNParseOut
from app.schemas.purchase_orders import PurchaseOrderCreate, PurchaseOrderListOut, PurchaseOrderOut, PurchaseOrderUpdate
from app.services import grn_import_service

router = APIRouter(prefix="/inventory", tags=["purchase-orders"])

# Receiving reads open orders to fill a GRN from one, so the dock doesn't need the order screen itself.
_read = require_any_permission(("inventory.purchase-orders", "R"), ("inventory.receiving", "R"))
_write = require_permission("inventory.purchase-orders", "W")
_approve = require_permission("inventory.purchase-orders.approve", "X")
_receive = require_permission("inventory.receiving", "W")


@router.get("/purchase-orders", response_model=PurchaseOrderListOut)
async def list_orders(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
    status: str | None = None, supplierId: str | None = None,
    limit: int = 200, offset: int = 0, user: User = Depends(_read),
) -> PurchaseOrderListOut:
    """`status` is draft, approved, partially-received, received, cancelled, closed — or `open` for
    anything that can still be received against."""
    return await purchase_order_controller.list_all(limit, offset, from_, to, status, supplierId)


@router.post("/purchase-orders", response_model=PurchaseOrderOut)
async def create_order(payload: PurchaseOrderCreate, user: User = Depends(_write)) -> PurchaseOrderOut:
    """Raises a draft. Someone other than the person who raised it approves it."""
    return await purchase_order_controller.create(user, payload)


@router.get("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
async def get_order(po_id: str, user: User = Depends(_read)) -> PurchaseOrderOut:
    return await purchase_order_controller.get(po_id)


@router.patch("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
async def update_order(po_id: str, payload: PurchaseOrderUpdate, user: User = Depends(_write)) -> PurchaseOrderOut:
    return await purchase_order_controller.update(user, po_id, payload)


@router.post("/purchase-orders/{po_id}/approve", response_model=PurchaseOrderOut)
async def approve_order(po_id: str, user: User = Depends(_approve)) -> PurchaseOrderOut:
    return await purchase_order_controller.approve(user, po_id)


@router.post("/purchase-orders/{po_id}/cancel", response_model=PurchaseOrderOut)
async def cancel_order(po_id: str, user: User = Depends(_write)) -> PurchaseOrderOut:
    """Cancels a draft or an untouched approved order; closes one that's partly received."""
    return await purchase_order_controller.cancel(user, po_id)


@router.post("/grn/parse-lines", response_model=GRNParseOut)
async def parse_grn_lines(file: UploadFile = File(...), user: User = Depends(_receive)) -> GRNParseOut:
    """Reads a supplier's CSV/Excel into draft Receiving lines. Nothing is received until the GRN is posted."""
    return await grn_import_service.parse_lines(file.filename, await file.read())
