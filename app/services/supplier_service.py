import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.models import Supplier, User
from app.schemas.catalog import SupplierCreate, SupplierUpdate
from app.schemas.import_result import ImportRowError, ImportSummary
from app.services import supplier_sync_service
from app.services.import_service import cell_decimal, cell_int, cell_str, cell_str_any, parse_rows, row_error


class SupplierError(Exception):
    def __init__(self, message: str):
        self.message = message


# API field → model column, for everything that isn't named the same on both sides.
_FIELD_MAP = {"contactPerson": "contact_person", "sTaxRegNo": "s_tax_reg_no", "dueDays": "due_days", "discountPercent": "discount_percent"}
_TEXT_FIELDS = ("contactPerson", "phone", "phone2", "email", "address", "city", "ntn", "sTaxRegNo", "cnic", "remarks")
_API_NAME = {column: key for key, column in supplier_sync_service.SHARED.items()}

# Words that don't tell one supplier from another: "M/S Shan Foods (Pvt) Ltd" is "Shan Foods".
_NOISE_WORDS = {"pvt", "private", "ltd", "limited", "smc", "llc", "inc", "the", "co", "company"}


def normal_name(name: str | None) -> str:
    """A supplier's name as it is compared: case, punctuation, "&" and company-form words don't count."""
    text = (name or "").lower().replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", text)
    if words[:2] == ["m", "s"]:
        words = words[2:]
    kept = [w for w in words if w not in _NOISE_WORDS]
    return " ".join(kept or words)


def _columns(values: dict) -> dict:
    out = {}
    for key, value in values.items():
        if key in _TEXT_FIELDS and isinstance(value, str):
            value = value.strip() or None
        out[_FIELD_MAP.get(key, key)] = value
    return out


async def list_all() -> list[Supplier]:
    return await Supplier.all().order_by("-active", "name")


async def next_code() -> str:
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
    """The supplier already registered under this name, spelt however it was typed then."""
    wanted = normal_name(name)
    for supplier in await Supplier.all().only("id", "code", "name"):
        if supplier.id != except_id and normal_name(supplier.name) == wanted:
            return supplier
    return None


def _same(a, b) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return Decimal(str(a or 0)) == Decimal(str(b or 0))
    return a == b


@atomic()
async def create(data: SupplierCreate, user: User | None = None) -> Supplier:
    """From the form: refuses a code or a name that already exists, so a supplier isn't registered
    twice under two spellings of the same code. Imports use `upsert` instead."""
    name = data.name.strip()
    code = (data.code or "").strip().upper() or await next_code()
    if await Supplier.exists(code=code):
        raise SupplierError(f"Supplier code {code} is already in use.")
    clash = await _name_taken(name)
    if clash:
        raise SupplierError(f"{clash.name} is already registered as {clash.code}.")
    fields = _columns(data.model_dump(exclude={"code", "name"}))
    supplier = await Supplier.create(
        id=f"sup-{secrets.token_hex(4)}", code=code, name=name, active=True, origin=await supplier_sync_service.own_code(),
        updated_at=datetime.now(timezone.utc), **fields,
    )
    await _after_change(supplier, None, user)
    return supplier


async def _after_change(supplier: Supplier, changed: list[str] | None, user: User | None) -> None:
    """Head office hears of it, and the ledger account keeps the supplier's name."""
    from app.services import accounts_chart_service

    await supplier_sync_service.announce(supplier, changed, user)
    await accounts_chart_service.supplier_account(supplier)


async def _write(supplier: Supplier, columns: dict, user: User | None) -> Supplier:
    changed = [_API_NAME[c] for c, v in columns.items() if c in _API_NAME and not _same(getattr(supplier, c), v)]
    if not changed:
        return supplier
    for column, value in columns.items():
        setattr(supplier, column, value)
    supplier.updated_at = datetime.now(timezone.utc)
    await supplier.save()
    await _after_change(supplier, changed, user)
    return supplier


@atomic()
async def update(supplier_id: str, data: SupplierUpdate, user: User | None = None) -> Supplier:
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
    columns = {
        column: value for column, value in _columns(changes).items()
        if not (column in ("name", "active", "due_days", "discount_percent") and value is None)
    }
    return await _write(supplier, columns, user)


@atomic()
async def upsert(data: SupplierCreate, user: User | None = None) -> Supplier:
    """Import path: a row whose code exists updates that supplier; a new code creates one.

    An update only writes the cells the file actually filled. A price-list export with just code,
    name and city must not wipe the phone numbers and NTNs typed in on the form."""
    code = (data.code or "").strip().upper()
    sent = data.model_dump(exclude={"code"}, exclude_unset=True)
    fields = _columns(data.model_dump(exclude={"code"}))
    supplier = await Supplier.get_or_none(code=code)
    if supplier:
        return await _write(supplier, {k: v for k, v in _columns(sent).items() if v is not None}, user)
    supplier = await Supplier.create(
        id=code, code=code, origin=await supplier_sync_service.own_code(), updated_at=datetime.now(timezone.utc), **fields,
    )
    await _after_change(supplier, None, user)
    return supplier


async def import_suppliers(filename: str, content: bytes, user: User | None = None) -> ImportSummary:
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
                "phone2": cell_str_any(row, "phone2", "otherPhone", "mobile"),
                "email": cell_str(row, "email"), "address": cell_str(row, "address"), "city": cell_str(row, "city"),
                "ntn": cell_str(row, "ntn"), "sTaxRegNo": cell_str_any(row, "sTaxRegNo", "strn"),
                "cnic": cell_str(row, "cnic"), "remarks": cell_str(row, "remarks"),
            }
            if cell_str(row, "dueDays") is not None:
                cells["dueDays"] = cell_int(row, "dueDays", 0)
            discount = cell_decimal(row, "discountPercent")
            if discount is None:
                discount = cell_decimal(row, "discount")
            if discount is not None:
                cells["discountPercent"] = discount
            data = SupplierCreate(code=code, name=name, **{k: v for k, v in cells.items() if v is not None})
            existed = await Supplier.exists(code=code.strip().upper())
            await upsert(data, user)
            if existed:
                updated += 1
            else:
                created += 1
        except (ValueError, KeyError) as exc:
            errors.append(ImportRowError(row=i, message=row_error(exc)))
    return ImportSummary(created=created, updated=updated, errors=errors)
