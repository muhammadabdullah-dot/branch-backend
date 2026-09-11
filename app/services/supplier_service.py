from app.models import Supplier
from app.schemas.catalog import SupplierCreate
from app.schemas.import_result import ImportRowError, ImportSummary
from app.services.import_service import cell_str, parse_rows

_FIELD_MAP = {"contactPerson": "contact_person"}


async def list_all() -> list[Supplier]:
    return await Supplier.all().order_by("name")


async def upsert(data: SupplierCreate) -> Supplier:
    fields = {_FIELD_MAP.get(k, k): v for k, v in data.model_dump().items() if k != "code"}
    supplier = await Supplier.get_or_none(code=data.code)
    if supplier:
        for key, value in fields.items():
            setattr(supplier, key, value)
        await supplier.save()
        return supplier
    return await Supplier.create(id=data.code, code=data.code, **fields)


async def import_suppliers(filename: str, content: bytes) -> ImportSummary:
    rows = parse_rows(filename, content)
    created = 0
    updated = 0
    errors: list[ImportRowError] = []
    for i, row in enumerate(rows, start=2):
        try:
            code = cell_str(row, "code")
            name = cell_str(row, "name")
            if not code or not name:
                raise ValueError("code and name are required")
            data = SupplierCreate(
                code=code, name=name,
                contactPerson=cell_str(row, "contactPerson"), phone=cell_str(row, "phone"),
            )
            existed = await Supplier.exists(code=data.code)
            await upsert(data)
            if existed:
                updated += 1
            else:
                created += 1
        except (ValueError, KeyError) as exc:
            errors.append(ImportRowError(row=i, message=str(exc)))
    return ImportSummary(created=created, updated=updated, errors=errors)
