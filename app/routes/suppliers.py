from fastapi import APIRouter, Depends, File, UploadFile

from app.controllers import supplier_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.catalog import SupplierCreate, SupplierOut
from app.schemas.import_result import ImportSummary

router = APIRouter(prefix="/suppliers", tags=["suppliers"])

_read = require_permission("inventory.suppliers", "R")
_write = require_permission("inventory.suppliers", "W")


@router.get("", response_model=list[SupplierOut])
async def list_suppliers(user: User = Depends(_read)) -> list[SupplierOut]:
    return await supplier_controller.list_all()


@router.post("", response_model=SupplierOut)
async def create_supplier(payload: SupplierCreate, user: User = Depends(_write)) -> SupplierOut:
    return await supplier_controller.create(payload)


@router.post("/import", response_model=ImportSummary)
async def import_suppliers(file: UploadFile = File(...), user: User = Depends(_write)) -> ImportSummary:
    content = await file.read()
    return await supplier_controller.import_file(file.filename, content)
