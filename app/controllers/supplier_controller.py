from fastapi import HTTPException, status

from app.models import Supplier
from app.schemas.catalog import SupplierCreate, SupplierOut, SupplierUpdate
from app.schemas.import_result import ImportSummary
from app.services import supplier_service


def _to_out(s: Supplier) -> SupplierOut:
    return SupplierOut(
        id=s.id, code=s.code, name=s.name, contactPerson=s.contact_person, phone=s.phone,
        email=s.email, address=s.address, city=s.city, ntn=s.ntn, sTaxRegNo=s.s_tax_reg_no,
        dueDays=s.due_days, remarks=s.remarks, active=s.active,
    )


async def list_all() -> list[SupplierOut]:
    return [_to_out(s) for s in await supplier_service.list_all()]


async def create(data: SupplierCreate) -> SupplierOut:
    try:
        return _to_out(await supplier_service.create(data))
    except supplier_service.SupplierError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


async def update(supplier_id: str, data: SupplierUpdate) -> SupplierOut:
    try:
        return _to_out(await supplier_service.update(supplier_id, data))
    except supplier_service.SupplierError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


async def import_file(filename: str, content: bytes) -> ImportSummary:
    return await supplier_service.import_suppliers(filename, content)
