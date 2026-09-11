from app.models import Supplier
from app.schemas.catalog import SupplierCreate, SupplierOut
from app.schemas.import_result import ImportSummary
from app.services import supplier_service


def _to_out(s: Supplier) -> SupplierOut:
    return SupplierOut(id=s.id, code=s.code, name=s.name, contactPerson=s.contact_person, phone=s.phone)


async def list_all() -> list[SupplierOut]:
    return [_to_out(s) for s in await supplier_service.list_all()]


async def create(data: SupplierCreate) -> SupplierOut:
    return _to_out(await supplier_service.upsert(data))


async def import_file(filename: str, content: bytes) -> ImportSummary:
    return await supplier_service.import_suppliers(filename, content)
