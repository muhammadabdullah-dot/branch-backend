"""Transfer steps that need more than the shipment screens' first routes carry. See services/transfers_service.py."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.controllers import inventory_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.inventory import TransferOut
from app.schemas.requisitions import TransferDispatchFromIn
from app.services import transfers_service

router = APIRouter(prefix="/inventory/transfers", tags=["inventory"])

_send = require_permission("inventory.transfers.send", "W")


@router.post("/{transfer_id}/dispatch-from", response_model=TransferOut)
async def dispatch_from(transfer_id: str, payload: TransferDispatchFromIn, user: User = Depends(_send)) -> TransferOut:
    """Dispatch an outgoing shipment, saying which location it leaves from. Needed for one head office asked this
    branch to send, which has no location until now; for any other it can change the location before it leaves."""
    try:
        transfer = await transfers_service.dispatch_ready(user, transfer_id, payload.vehicle, payload.driver, payload.locationId)
    except transfers_service.TransferError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return await inventory_controller._one_transfer(transfer)
