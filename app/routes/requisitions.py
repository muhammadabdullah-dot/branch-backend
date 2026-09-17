"""Stock requests: this branch asking head office for stock. See services/requisition_service.py."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.middlewares.auth import require_permission
from app.models import Transfer, User
from app.schemas.requisitions import (
    CoverOut,
    SourceOut,
    StockRequestIn,
    StockRequestLineOut,
    StockRequestOut,
    SuggestionOut,
    WithdrawIn,
)
from app.services import requisition_service
from app.services.requisition_service import RequestError

router = APIRouter(prefix="/inventory/requests", tags=["stock requests"])

_read = require_permission("inventory.requests", "R")
_write = require_permission("inventory.requests", "W")


def _fail(exc: RequestError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


async def _out(requests: list) -> list[StockRequestOut]:
    transfer_ids = [r.transfer_id for r in requests if r.transfer_id]
    shipments = {str(t.id): t for t in await Transfer.filter(id__in=transfer_ids)} if transfer_ids else {}
    out = []
    for r in requests:
        shipment = shipments.get(str(r.transfer_id)) if r.transfer_id else None
        out.append(StockRequestOut(
            id=str(r.id), number=r.number, status=r.status, origin=r.origin, sourceCode=r.source_code, sourceName=r.source_name,
            reason=r.reason, neededBy=r.needed_by, createdBy=r.created_by_name, createdAt=r.created_at, updatedAt=r.updated_at,
            sentAt=r.sent_at, sentBy=r.sent_by_name, headOfficeNumber=r.head_office_number, receivedAtHeadOffice=r.received_at_head_office,
            decidedAt=r.decided_at, decidedBy=r.decided_by_name, decisionNote=r.decision_note, transferId=r.transfer_id,
            transferNumber=r.transfer_number, transferStatus=shipment.status if shipment else None,
            transferDisputeOpen=bool(shipment and shipment.dispute_open),
            lines=[
                StockRequestLineOut(
                    productId=str(l.product_id), productName=l.product.name, productSku=l.product.sku, unit=l.product.unit,
                    qtyRequested=l.qty_requested, qtyApproved=l.qty_approved, onHand=l.on_hand, dailySales=l.daily_sales,
                )
                for l in r.lines
            ],
        ))
    return out


async def _one(request_id: str) -> StockRequestOut:
    return (await _out([await requisition_service.get(request_id)]))[0]


@router.get("", response_model=list[StockRequestOut])
async def list_requests(status_: str | None = Query(default=None, alias="status"), user: User = Depends(_read)) -> list[StockRequestOut]:
    return await _out(await requisition_service.list_requests(status_))


@router.get("/cover", response_model=list[CoverOut])
async def cover(
    productId: list[str] = Query(default=[]), ids: str | None = None, user: User = Depends(_read),
) -> list[CoverOut]:
    """What this branch holds of each Item, sells a day (last 30 days), and how many days that lasts. Items by repeated
    `productId`, or comma separated in `ids` (what the Branch App sends)."""
    found = await requisition_service.cover_for([*productId, *(i.strip() for i in (ids or "").split(",") if i.strip())])
    return [CoverOut(productId=pid, **values) for pid, values in found.items()]


@router.get("/suggestions", response_model=list[SuggestionOut])
async def suggestions(limit: int = 50, user: User = Depends(_read)) -> list[SuggestionOut]:
    """Items that run out within a week at the last 30 days' sales, and how many bring them to two weeks."""
    return [SuggestionOut(**row) for row in await requisition_service.suggestions(limit)]


@router.get("/sources", response_model=list[SourceOut])
async def sources(user: User = Depends(_read)) -> list[SourceOut]:
    """The other branches that can be asked, as head office last listed them. Head office's godown is always there."""
    return [SourceOut(code=b.code, name=b.name, city=b.city) for b in await requisition_service.sources()]


@router.get("/{request_id}", response_model=StockRequestOut)
async def one(request_id: str, user: User = Depends(_read)) -> StockRequestOut:
    try:
        return await _one(request_id)
    except RequestError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message)


@router.post("", response_model=StockRequestOut)
async def create(payload: StockRequestIn, user: User = Depends(_write)) -> StockRequestOut:
    try:
        saved = await requisition_service.save_draft(
            user, None, payload.sourceCode, payload.reason, payload.neededBy, [(l.productId, l.qty) for l in payload.lines],
        )
    except RequestError as exc:
        raise _fail(exc)
    return await _one(str(saved.id))


@router.put("/{request_id}", response_model=StockRequestOut)
async def update(request_id: str, payload: StockRequestIn, user: User = Depends(_write)) -> StockRequestOut:
    try:
        await requisition_service.save_draft(
            user, request_id, payload.sourceCode, payload.reason, payload.neededBy, [(l.productId, l.qty) for l in payload.lines],
        )
    except RequestError as exc:
        raise _fail(exc)
    return await _one(request_id)


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(request_id: str, user: User = Depends(_write)) -> Response:
    try:
        await requisition_service.delete_draft(user, request_id)
    except RequestError as exc:
        raise _fail(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{request_id}/send", response_model=StockRequestOut)
async def send(request_id: str, user: User = Depends(_write)) -> StockRequestOut:
    try:
        await requisition_service.send(user, request_id)
    except RequestError as exc:
        raise _fail(exc)
    return await _one(request_id)


@router.post("/{request_id}/withdraw", response_model=StockRequestOut)
async def withdraw(request_id: str, payload: WithdrawIn, user: User = Depends(_write)) -> StockRequestOut:
    try:
        await requisition_service.withdraw(user, request_id, payload.reason)
    except RequestError as exc:
        raise _fail(exc)
    return await _one(request_id)
