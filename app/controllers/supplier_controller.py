from fastapi import HTTPException, status

from app.models import Supplier, User
from app.schemas.catalog import SupplierCreate, SupplierOut, SupplierUpdate
from app.schemas.import_result import ImportSummary
from app.services import supplier_service, supplier_sync_service


def _to_out(s: Supplier, own: str | None, names: dict[str, str]) -> SupplierOut:
    origin, origin_name = supplier_sync_service.origin_of(s, own, names)
    return SupplierOut(
        id=s.id, code=s.code, name=s.name, contactPerson=s.contact_person, phone=s.phone, phone2=s.phone2,
        email=s.email, address=s.address, city=s.city, ntn=s.ntn, sTaxRegNo=s.s_tax_reg_no, cnic=s.cnic,
        dueDays=s.due_days, discountPercent=s.discount_percent, remarks=s.remarks, active=s.active,
        companyId=s.company_id, origin=origin, originName=origin_name,
    )


async def _one(s: Supplier) -> SupplierOut:
    return _to_out(s, *await supplier_sync_service.origin_labels())


async def list_all() -> list[SupplierOut]:
    own, names = await supplier_sync_service.origin_labels()
    return [_to_out(s, own, names) for s in await supplier_service.list_all()]


async def create(data: SupplierCreate, user: User) -> SupplierOut:
    try:
        return await _one(await supplier_service.create(data, user))
    except supplier_service.SupplierError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


async def update(supplier_id: str, data: SupplierUpdate, user: User) -> SupplierOut:
    try:
        return await _one(await supplier_service.update(supplier_id, data, user))
    except supplier_service.SupplierError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


async def import_file(filename: str, content: bytes, user: User) -> ImportSummary:
    return await supplier_service.import_suppliers(filename, content, user)
