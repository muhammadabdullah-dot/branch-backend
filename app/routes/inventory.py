from fastapi import APIRouter, Depends

from app.controllers import inventory_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.inventory import (
    AdjustmentOut,
    AdjustmentSubmitRequest,
    BalanceOut,
    BatchOut,
    CountOut,
    CountSubmitRequest,
    DisputeRequest,
    GRNCreateRequest,
    GRNOut,
    StockMovementOut,
    TransferOut,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])

_read = require_permission("inventory.overview", "R")
_receive = require_permission("inventory.receiving", "W")
_counts_write = require_permission("inventory.counts", "W")
_counts_approve = require_permission("inventory.counts.approve", "X")
_adjustments_write = require_permission("inventory.adjustments", "W")
_adjustments_approve = require_permission("inventory.adjustments.approve", "X")
_transfers_read = require_permission("inventory.transfers", "R")
_transfers_write = require_permission("inventory.transfers", "W")


@router.get("/balance", response_model=BalanceOut)
async def balance(productId: str, locationId: str | None = None, user: User = Depends(_read)) -> BalanceOut:
    return await inventory_controller.get_balance(productId, locationId)


@router.get("/movements", response_model=list[StockMovementOut])
async def movements(productId: str | None = None, locationId: str | None = None, user: User = Depends(_read)) -> list[StockMovementOut]:
    return await inventory_controller.list_movements(productId, locationId)


@router.post("/grn", response_model=GRNOut)
async def receive_grn(payload: GRNCreateRequest, user: User = Depends(_receive)) -> GRNOut:
    return await inventory_controller.receive_grn(user, payload)


@router.get("/batches", response_model=list[BatchOut])
async def batches(productId: str | None = None, user: User = Depends(_read)) -> list[BatchOut]:
    return await inventory_controller.list_batches(productId)


@router.post("/counts", response_model=CountOut)
async def submit_count(payload: CountSubmitRequest, user: User = Depends(_counts_write)) -> CountOut:
    return await inventory_controller.submit_count(user, payload)


@router.post("/counts/{count_id}/approve", response_model=CountOut)
async def approve_count(count_id: str, user: User = Depends(_counts_approve)) -> CountOut:
    return await inventory_controller.approve_count(user, count_id)


@router.post("/adjustments", response_model=AdjustmentOut)
async def submit_adjustment(payload: AdjustmentSubmitRequest, user: User = Depends(_adjustments_write)) -> AdjustmentOut:
    return await inventory_controller.submit_adjustment(user, payload)


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
