import re
import secrets

from tortoise.transactions import atomic

from app.models import Supplier
from app.schemas.catalog import SupplierCreate, SupplierUpdate
from app.schemas.import_result import ImportRowError, ImportSummary
from app.services.import_service import cell_int, cell_str, cell_str_any, parse_rows, row_error


class SupplierError(Exception):
    def __init__(self, message: str):
        self.message = message


# API field → model column, for everything that isn't named the same on both sides.
_FIELD_MAP = {"contactPerson": "contact_person", "sTaxRegNo": "s_tax_reg_no", "dueDays": "due_days"}
_TEXT_FIELDS = ("contactPerson", "phone", "email", "address", "city", "ntn", "sTaxRegNo", "remarks")


def _columns(values: dict) -> dict:
    out = {}
    for key, value in values.items():
        if key in _TEXT_FIELDS and isinstance(value, str):
            value = value.strip() or None
        out[_FIELD_MAP.get(key, key)] = value
    return out


async def list_all() -> list[Supplier]:
    return await Supplier.all().order_by("-active", "name")


async def _next_code() -> str:
    """SUP0001, SUP0002 … continuing after the highest SUP-number already in use, whatever else the
    imported codes look like."""
    highest = 0
    for code in await Supplier.filter(code__istartswith="SUP").values_list("code", flat=True):
        match = re.fullmatch(r"SUP(\d+)", code.upper())
        if match:
            highest = max(highest, int(match.group(1)))
    code = f"SUP{highest + 1:04d}"
    while await Supplier.exists(code=code):
        highest += 1
        code = f"SUP{highest + 1:04d}"
    return code


async def _name_taken(name: str, except_id: str | None = None) -> Supplier | None:
    qs = Supplier.filter(name__iexact=name.strip())
    if except_id:
        qs = qs.exclude(id=except_id)
    return await qs.first()


@atomic()
async def create(data: SupplierCreate) -> Supplier:
    """From the form: refuses a code or a name that already exists, so a supplier isn't registered
    twice under two spellings of the same code. Imports use `upsert` instead."""
    name = data.name.strip()
    code = (data.code or "").strip().upper() or await _next_code()
    if await Supplier.exists(code=code):
        raise SupplierError(f"Supplier code {code} is already in use.")
    clash = await _name_taken(name)
    if clash:
        raise SupplierError(f"{clash.name} is already registered as {clash.code}.")
    fields = _columns(data.model_dump(exclude={"code", "name"}))
    return await Supplier.create(id=f"sup-{secrets.token_hex(4)}", code=code, name=name, active=True, **fields)


@atomic()
async def update(supplier_id: str, data: SupplierUpdate) -> Supplier:
    supplier = await Supplier.get_or_none(id=supplier_id)
    if not supplier:
        raise SupplierError("That supplier doesn't exist.")
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is not None:
        name = changes["name"].strip()
        clash = await _name_taken(name, except_id=supplier.id)
        if clash:
            raise SupplierError(f"{clash.name} is already registered as {clash.code}.")
        changes["name"] = name
    for column, value in _columns(changes).items():
        if column in ("name", "active", "due_days") and value is None:
            continue
        setattr(supplier, column, value)
    await supplier.save()
    return supplier


async def upsert(data: SupplierCreate) -> Supplier:
    """Import path: a row whose code exists updates that supplier; a new code creates one.

    An update only writes the cells the file actually filled. A price-list export with just code,
    name and city must not wipe the phone numbers and NTNs typed in on the form."""
    code = (data.code or "").strip().upper()
    sent = data.model_dump(exclude={"code"}, exclude_unset=True)
    fields = _columns(data.model_dump(exclude={"code"}))
    supplier = await Supplier.get_or_none(code=code)
    if supplier:
        for key, value in _columns(sent).items():
            if value is None:
                continue
            setattr(supplier, key, value)
        await supplier.save()
        return supplier
    return await Supplier.create(id=code, code=code, **fields)


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
            cells = {
                "contactPerson": cell_str(row, "contactPerson"), "phone": cell_str(row, "phone"),
                "email": cell_str(row, "email"), "address": cell_str(row, "address"), "city": cell_str(row, "city"),
                "ntn": cell_str(row, "ntn"), "sTaxRegNo": cell_str_any(row, "sTaxRegNo", "strn"),
                "remarks": cell_str(row, "remarks"),
            }
            if cell_str(row, "dueDays") is not None:
                cells["dueDays"] = cell_int(row, "dueDays", 0)
            data = SupplierCreate(code=code, name=name, **{k: v for k, v in cells.items() if v is not None})
            existed = await Supplier.exists(code=code.strip().upper())
            await upsert(data)
            if existed:
                updated += 1
            else:
                created += 1
        except (ValueError, KeyError) as exc:
            errors.append(ImportRowError(row=i, message=row_error(exc)))
    return ImportSummary(created=created, updated=updated, errors=errors)
