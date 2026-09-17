from fastapi import APIRouter, Depends, File, UploadFile

from app.controllers import supplier_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.catalog import SupplierCreate, SupplierOut, SupplierUpdate
from app.schemas.import_result import ImportSummary

router = APIRouter(prefix="/suppliers", tags=["suppliers"])

_read = require_permission("inventory.suppliers", "R")
_write = require_permission("inventory.suppliers", "W")


@router.get("", response_model=list[SupplierOut])
async def list_suppliers(user: User = Depends(_read)) -> list[SupplierOut]:
    return await supplier_controller.list_all()


@router.post("", response_model=SupplierOut)
async def create_supplier(payload: SupplierCreate, user: User = Depends(_write)) -> SupplierOut:
    """Register one supplier. Leave `code` out to have one assigned (SUP0001 …). Refuses a code or
    name already on file — bulk updates go through /import. Head office adds it to the company list."""
    return await supplier_controller.create(payload, user)


@router.patch("/{supplier_id}", response_model=SupplierOut)
async def update_supplier(supplier_id: str, payload: SupplierUpdate, user: User = Depends(_write)) -> SupplierOut:
    """Edit details, or switch a supplier off (`active: false`) so Receiving stops offering it. The change goes to
    head office and on to every branch."""
    return await supplier_controller.update(supplier_id, payload, user)


@router.post("/import", response_model=ImportSummary)
async def import_suppliers(file: UploadFile = File(...), user: User = Depends(_write)) -> ImportSummary:
    content = await file.read()
    return await supplier_controller.import_file(file.filename, content, user)


_queued_existing = False


async def _join_the_company_list() -> None:
    """Once per database (a `counters` row): this branch's suppliers go to head office to be matched to the company list.

    Started from this router so it needs no line in app/main.py. FastAPI runs a router's startup handlers after the
    database is open, and in this version runs them twice, so the flag keeps it to once per start."""
    global _queued_existing
    if _queued_existing:
        return
    _queued_existing = True
    from app.core import logs
    from app.services import supplier_sync_service

    try:
        queued = await supplier_sync_service.queue_existing()
    except Exception as exc:  # noqa: BLE001 — a supplier list problem must not stop the branch opening
        logs.log.error("suppliers: couldn't queue this branch's suppliers for the company list", exc_info=exc)
        return
    if queued:
        print(f"  suppliers: {queued} supplier(s) queued for head office's company list", flush=True)


router.add_event_handler("startup", _join_the_company_list)
