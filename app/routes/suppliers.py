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
    name already on file — bulk updates go through /import."""
    return await supplier_controller.create(payload)


@router.patch("/{supplier_id}", response_model=SupplierOut)
async def update_supplier(supplier_id: str, payload: SupplierUpdate, user: User = Depends(_write)) -> SupplierOut:
    """Edit details, or switch a supplier off (`active: false`) so Receiving stops offering it."""
    return await supplier_controller.update(supplier_id, payload)


@router.post("/import", response_model=ImportSummary)
async def import_suppliers(file: UploadFile = File(...), user: User = Depends(_write)) -> ImportSummary:
    content = await file.read()
    return await supplier_controller.import_file(file.filename, content)
