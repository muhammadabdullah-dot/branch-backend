from fastapi import APIRouter, Depends

from app.controllers import inventory_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.inventory import (
    AdjustmentListOut,
    AdjustmentOut,
    AdjustmentSubmitRequest,
    BalanceOut,
    BatchListOut,
    CountListOut,
    CountOut,
    CountSubmitRequest,
    DisputeRequest,
    GRNCreateRequest,
    GRNListOut,
    GRNOut,
    PurchaseReturnCreateRequest,
    PurchaseReturnListOut,
    PurchaseReturnOut,
    StockMovementListOut,
    StockValueOut,
    TransferOut,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])

_read = require_permission("inventory.overview", "R")
# Reports reaches for this too — a Branch Manager holds `reports` but not `inventory.*`.
_stock_value_read = require_any_permission(("inventory.overview", "R"), ("reports", "R"))
# Read-only ledger views. `reports` gets in because the Branch Manager's own Dashboard and the
# Reports screen are built on exactly this data — without it, the manager's stock-health panel
# reads "your access doesn't include stock levels" on the one screen meant to show it. Nothing
# here writes; every write above stays on its own inventory.* permission.
_movements_read = require_any_permission(("inventory.movements", "R"), ("reports", "R"))
_batches_read = require_any_permission(("inventory.batches", "R"), ("reports", "R"))
_receive = require_permission("inventory.receiving", "W")
_grns_read = require_permission("inventory.receiving", "R")
_purchase_returns_write = require_permission("inventory.purchase-returns", "W")
_purchase_returns_read = require_permission("inventory.purchase-returns", "R")
_counts_write = require_permission("inventory.counts", "W")
_counts_approve = require_permission("inventory.counts.approve", "X")
_adjustments_write = require_permission("inventory.adjustments", "W")
_adjustments_approve = require_permission("inventory.adjustments.approve", "X")
_transfers_read = require_permission("inventory.transfers", "R")
_transfers_write = require_permission("inventory.transfers", "W")
# Counts/adjustments lists are read by three different screens with no one resource in
# common: the submitter's own screen (inventory.*), the approver's Approvals Inbox
# (branch-console.approvals), and the Branch Manager's Reports (reports).
_counts_list_read = require_any_permission(
    ("inventory.counts", "R"), ("inventory.counts.approve", "X"), ("branch-console.approvals", "R"), ("reports", "R"),
)
_adjustments_list_read = require_any_permission(
    ("inventory.adjustments", "R"), ("inventory.adjustments.approve", "X"), ("branch-console.approvals", "R"), ("reports", "R"),
)


@router.get("/balance", response_model=BalanceOut)
async def balance(productId: str, locationId: str | None = None, user: User = Depends(_read)) -> BalanceOut:
    return await inventory_controller.get_balance(productId, locationId)


@router.get("/stock-value", response_model=StockValueOut)
async def stock_value(user: User = Depends(_stock_value_read)) -> StockValueOut:
    return await inventory_controller.get_stock_value()


@router.get("/movements", response_model=StockMovementListOut)
async def movements(
    productId: str | None = None, locationId: str | None = None, limit: int = 2000, offset: int = 0,
    user: User = Depends(_movements_read),
) -> StockMovementListOut:
    return await inventory_controller.list_movements(productId, locationId, limit, offset)


@router.post("/grn", response_model=GRNOut)
async def receive_grn(payload: GRNCreateRequest, user: User = Depends(_receive)) -> GRNOut:
    return await inventory_controller.receive_grn(user, payload)


@router.get("/grns", response_model=GRNListOut)
async def list_grns(limit: int = 200, offset: int = 0, user: User = Depends(_grns_read)) -> GRNListOut:
    return await inventory_controller.list_grns(limit, offset)


@router.post("/purchase-returns", response_model=PurchaseReturnOut)
async def create_purchase_return(payload: PurchaseReturnCreateRequest, user: User = Depends(_purchase_returns_write)) -> PurchaseReturnOut:
    return await inventory_controller.create_purchase_return(user, payload)


@router.get("/purchase-returns", response_model=PurchaseReturnListOut)
async def list_purchase_returns(limit: int = 200, offset: int = 0, user: User = Depends(_purchase_returns_read)) -> PurchaseReturnListOut:
    return await inventory_controller.list_purchase_returns(limit, offset)


@router.get("/batches", response_model=BatchListOut)
async def batches(
    productId: str | None = None, limit: int = 2000, offset: int = 0, user: User = Depends(_batches_read),
) -> BatchListOut:
    return await inventory_controller.list_batches(productId, limit, offset)


@router.post("/counts", response_model=CountOut)
async def submit_count(payload: CountSubmitRequest, user: User = Depends(_counts_write)) -> CountOut:
    return await inventory_controller.submit_count(user, payload)


@router.get("/counts", response_model=CountListOut)
async def list_counts(limit: int = 200, offset: int = 0, user: User = Depends(_counts_list_read)) -> CountListOut:
    return await inventory_controller.list_counts(limit, offset)


@router.post("/counts/{count_id}/approve", response_model=CountOut)
async def approve_count(count_id: str, user: User = Depends(_counts_approve)) -> CountOut:
    return await inventory_controller.approve_count(user, count_id)


@router.post("/adjustments", response_model=AdjustmentOut)
async def submit_adjustment(payload: AdjustmentSubmitRequest, user: User = Depends(_adjustments_write)) -> AdjustmentOut:
    return await inventory_controller.submit_adjustment(user, payload)


@router.get("/adjustments", response_model=AdjustmentListOut)
async def list_adjustments(limit: int = 200, offset: int = 0, user: User = Depends(_adjustments_list_read)) -> AdjustmentListOut:
    return await inventory_controller.list_adjustments(limit, offset)


@router.post("/adjustments/{adjustment_id}/approve", response_model=AdjustmentOut)
async def approve_adjustment(adjustment_id: str, user: User = Depends(_adjustments_approve)) -> AdjustmentOut:
    return await inventory_controller.approve_adjustment(user, adjustment_id)


@router.post("/adjustments/{adjustment_id}/reject", response_model=AdjustmentOut)
async def reject_adjustment(adjustment_id: str, user: User = Depends(_adjustments_approve)) -> AdjustmentOut:
    return await inventory_controller.reject_adjustment(user, adjustment_id)


@router.get("/transfers", response_model=list[TransferOut])
async def list_transfers(user: User = Depends(_transfers_read)) -> list[TransferOut]:
    return await inventory_controller.list_transfers()


@router.post("/transfers/{transfer_id}/dispute", response_model=TransferOut)
async def open_dispute(transfer_id: str, payload: DisputeRequest, user: User = Depends(_transfers_write)) -> TransferOut:
    return await inventory_controller.open_dispute(user, transfer_id, payload)
