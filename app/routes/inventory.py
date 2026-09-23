from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.controllers import inventory_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.inventory import (
    TransferDispatchRequest,
    TransferStepRequest,
    AdjustmentListOut,
    AdjustmentOut,
    AdjustmentSubmitRequest,
    BalanceOut,
    BatchListOut,
    CountListOut,
    CountOut,
    CountSubmitRequest,
    DisputeRequest,
    KnownBranchOut,
    GRNCreateRequest,
    GRNListOut,
    GRNOut,
    PurchaseReturnCreateRequest,
    PurchaseReturnListOut,
    PurchaseReturnOut,
    StockMovementListOut,
    StockValueOut,
    TransferOut,
    TransferReceiveRequest,
    TransferSendRequest,
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
_transfers_send = require_permission("inventory.transfers.send", "W")
_transfers_approve = require_permission("inventory.transfers.approve", "X")
_transfers_hold = require_permission("inventory.transfers.hold", "X")
_transfers_send_or_approve = require_any_permission(("inventory.transfers.send", "W"), ("inventory.transfers.approve", "X"))
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
    productId: str | None = None, locationId: str | None = None, kind: str | None = None,
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
    sort: str | None = None, order: str | None = None,
    limit: int = 2000, offset: int = 0,
    user: User = Depends(_movements_read),
) -> StockMovementListOut:
    """The stock ledger. Filtered and sorted on the server — by date, kind, product and location —
    because it holds tens of thousands of rows and a screen only ever holds a page of them.
    `sort` is one of at, qty, kind, item, code, location, by; `order` is asc or desc (default desc)."""
    return await inventory_controller.list_movements(productId, locationId, limit, offset, from_, to, kind, sort, order)


@router.post("/grn", response_model=GRNOut)
async def receive_grn(payload: GRNCreateRequest, user: User = Depends(_receive)) -> GRNOut:
    return await inventory_controller.receive_grn(user, payload)


@router.get("/grns", response_model=GRNListOut)
async def list_grns(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None, limit: int = 200, offset: int = 0, user: User = Depends(_grns_read),
) -> GRNListOut:
    return await inventory_controller.list_grns(limit, offset, from_, to)


@router.post("/purchase-returns", response_model=PurchaseReturnOut)
async def create_purchase_return(payload: PurchaseReturnCreateRequest, user: User = Depends(_purchase_returns_write)) -> PurchaseReturnOut:
    return await inventory_controller.create_purchase_return(user, payload)


@router.get("/purchase-returns", response_model=PurchaseReturnListOut)
async def list_purchase_returns(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None, limit: int = 200, offset: int = 0, user: User = Depends(_purchase_returns_read),
) -> PurchaseReturnListOut:
    return await inventory_controller.list_purchase_returns(limit, offset, from_, to)


@router.get("/batches", response_model=BatchListOut)
async def batches(
    productId: str | None = None, limit: int = 2000, offset: int = 0, user: User = Depends(_batches_read),
) -> BatchListOut:
    return await inventory_controller.list_batches(productId, limit, offset)


@router.post("/counts", response_model=CountOut)
async def submit_count(payload: CountSubmitRequest, user: User = Depends(_counts_write)) -> CountOut:
    return await inventory_controller.submit_count(user, payload)


@router.get("/counts", response_model=CountListOut)
async def list_counts(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None, limit: int = 200, offset: int = 0, user: User = Depends(_counts_list_read),
) -> CountListOut:
    return await inventory_controller.list_counts(limit, offset, from_, to)


@router.post("/counts/{count_id}/approve", response_model=CountOut)
async def approve_count(count_id: str, user: User = Depends(_counts_approve)) -> CountOut:
    return await inventory_controller.approve_count(user, count_id)


@router.post("/adjustments", response_model=AdjustmentOut)
async def submit_adjustment(payload: AdjustmentSubmitRequest, user: User = Depends(_adjustments_write)) -> AdjustmentOut:
    return await inventory_controller.submit_adjustment(user, payload)


@router.get("/adjustments", response_model=AdjustmentListOut)
async def list_adjustments(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None, limit: int = 200, offset: int = 0, user: User = Depends(_adjustments_list_read),
) -> AdjustmentListOut:
    return await inventory_controller.list_adjustments(limit, offset, from_, to)


@router.post("/adjustments/{adjustment_id}/approve", response_model=AdjustmentOut)
async def approve_adjustment(adjustment_id: str, user: User = Depends(_adjustments_approve)) -> AdjustmentOut:
    return await inventory_controller.approve_adjustment(user, adjustment_id)


@router.post("/adjustments/{adjustment_id}/reject", response_model=AdjustmentOut)
async def reject_adjustment(adjustment_id: str, user: User = Depends(_adjustments_approve)) -> AdjustmentOut:
    return await inventory_controller.reject_adjustment(user, adjustment_id)


@router.get("/transfers", response_model=list[TransferOut])
async def list_transfers(
    direction: str | None = Query(default=None, pattern="^(inbound|outbound)$"), user: User = Depends(_transfers_read),
) -> list[TransferOut]:
    return await inventory_controller.list_transfers(direction)


@router.get("/transfers/branches", response_model=list[KnownBranchOut])
async def known_branches(user: User = Depends(_transfers_read)) -> list[KnownBranchOut]:
    """The other branches stock can be sent to, as head office last listed them."""
    return await inventory_controller.known_branches()


@router.post("/transfers", response_model=TransferOut)
async def send_transfer(payload: TransferSendRequest, user: User = Depends(_transfers_send)) -> TransferOut:
    """Ask to send stock to another branch. It waits for approval here (unless the person asking may approve),
    then for the other branch's go-ahead, then it's dispatched."""
    return await inventory_controller.send_transfer(user, payload)


@router.post("/transfers/{transfer_id}/acknowledge", response_model=TransferOut)
async def acknowledge_transfer(transfer_id: str, payload: TransferStepRequest, user: User = Depends(_transfers_write)) -> TransferOut:
    """Say an incoming shipment can be sent. Nothing is dispatched until this branch has said so."""
    return await inventory_controller.transfer_step("acknowledge", user, transfer_id, payload.reason)


@router.post("/transfers/{transfer_id}/decline", response_model=TransferOut)
async def decline_transfer(transfer_id: str, payload: TransferStepRequest, user: User = Depends(_transfers_write)) -> TransferOut:
    """Say an incoming shipment shouldn't be sent, and why."""
    return await inventory_controller.transfer_step("decline", user, transfer_id, payload.reason)


@router.post("/transfers/{transfer_id}/dispatch", response_model=TransferOut)
async def dispatch_transfer(transfer_id: str, payload: TransferDispatchRequest, user: User = Depends(_transfers_send)) -> TransferOut:
    """The other branch agreed: the stock leaves now and head office relays it."""
    return await inventory_controller.transfer_step("dispatch", user, transfer_id, vehicle=payload.vehicle, driver=payload.driver)


@router.post("/transfers/{transfer_id}/approve", response_model=TransferOut)
async def approve_transfer(transfer_id: str, user: User = Depends(_transfers_approve)) -> TransferOut:
    """Approve an outgoing shipment that's waiting: the other branch is asked whether it can be sent."""
    return await inventory_controller.transfer_step("approve", user, transfer_id)


@router.post("/transfers/{transfer_id}/cancel", response_model=TransferOut)
async def cancel_transfer(transfer_id: str, payload: TransferStepRequest, user: User = Depends(_transfers_send_or_approve)) -> TransferOut:
    """Cancel an outgoing shipment that hasn't left yet."""
    return await inventory_controller.transfer_step("cancel", user, transfer_id, payload.reason)


@router.post("/transfers/{transfer_id}/hold", response_model=TransferOut)
async def hold_transfer(transfer_id: str, payload: TransferStepRequest, user: User = Depends(_transfers_hold)) -> TransferOut:
    """Hold an incoming shipment back from being received, with the reason. Head office is told."""
    return await inventory_controller.transfer_step("hold", user, transfer_id, payload.reason)


@router.post("/transfers/{transfer_id}/release", response_model=TransferOut)
async def release_transfer(transfer_id: str, user: User = Depends(_transfers_hold)) -> TransferOut:
    """Release a held shipment so it can be received."""
    return await inventory_controller.transfer_step("release", user, transfer_id)


@router.post("/transfers/{transfer_id}/receive", response_model=TransferOut)
async def receive_transfer(transfer_id: str, payload: TransferReceiveRequest, user: User = Depends(_transfers_write)) -> TransferOut:
    """Count what arrived into a location. Less than was sent opens a dispute; head office is told either way."""
    return await inventory_controller.receive_transfer(user, transfer_id, payload)


@router.post("/transfers/{transfer_id}/dispute", response_model=TransferOut)
async def open_dispute(transfer_id: str, payload: DisputeRequest, user: User = Depends(_transfers_write)) -> TransferOut:
    return await inventory_controller.open_dispute(user, transfer_id, payload)
