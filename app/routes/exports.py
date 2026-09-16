from typing import Literal

from fastapi import APIRouter, Depends, Response

from app.middlewares.auth import require_any_permission
from app.models import BranchIdentity, User
from app.services import export_service

router = APIRouter(prefix="/exports", tags=["exports"])

_items_read = require_any_permission(("inventory.catalog", "R"), ("reports", "R"))
_parties_read = require_any_permission(("branch-console.customers", "R"), ("reports", "R"))


async def _branch_label() -> str:
    identity = await BranchIdentity.first()
    return (getattr(identity, "code", None) or "branch").lower()


async def _send(name: str, fmt: str) -> Response:
    content, media_type, filename = await export_service.build(name, fmt, await _branch_label())
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/items")
async def export_items(format: Literal["xlsx", "csv"] = "xlsx", user: User = Depends(_items_read)) -> Response:
    """The whole Item master, alternate barcodes included."""
    return await _send("items", format)


@router.get("/parties")
async def export_parties(format: Literal["xlsx", "csv"] = "xlsx", user: User = Depends(_parties_read)) -> Response:
    """Every customer-Party, switched-off ones included (walk-in excluded)."""
    return await _send("parties", format)
