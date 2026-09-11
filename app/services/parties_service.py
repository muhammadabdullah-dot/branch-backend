from decimal import Decimal, InvalidOperation

from app.models import Party, next_value
from app.schemas.import_result import ImportRowError, ImportSummary
from app.schemas.parties import PartyCreate, PartyUpdate
from app.services.import_service import cell_bool, cell_int, cell_str, parse_rows

_FIELD_MAP = {
    "contactPerson": "contact_person",
    "sTaxRegNo": "s_tax_reg_no",
    "loyaltyNo": "loyalty_no",
    "dueDays": "due_days",
    "creditAllowed": "credit_allowed",
    "creditLimit": "credit_limit",
    "creditBalance": "credit_balance",
}


def _to_model_fields(data: dict) -> dict:
    return {_FIELD_MAP.get(k, k): v for k, v in data.items()}


async def find_by_query(q: str) -> list[Party]:
    query = q.strip().upper()
    digits = "".join(ch for ch in query if ch.isdigit())
    candidates = await Party.filter(active=True, is_walk_in=False)
    hits = []
    for p in candidates:
        if p.code.upper() == query or (p.loyalty_no or "").upper() == query:
            hits.append(p)
        elif len(digits) >= 7 and (p.phone or "").replace("-", "").endswith(digits):
            hits.append(p)
    return hits


async def list_all() -> list[Party]:
    return await Party.filter(active=True)


async def create(data: PartyCreate) -> Party:
    seq = await next_value("party_code", 3)
    code = f"CUST{seq:03d}"
    fields = _to_model_fields(data.model_dump())
    return await Party.create(code=code, is_walk_in=False, credit_balance=Decimal("0"), active=True, **fields)


async def update(party_id: str, data: PartyUpdate) -> Party | None:
    party = await Party.get_or_none(id=party_id)
    if not party:
        return None
    fields = _to_model_fields(data.model_dump(exclude_unset=True))
    for key, value in fields.items():
        setattr(party, key, value)
    await party.save()
    return party


async def import_parties(filename: str, content: bytes) -> ImportSummary:
    """Upserts by `code` when the file provides one; otherwise always creates a new party with
    an auto-generated CUST### code, same as the Customer Registry screen would."""
    rows = parse_rows(filename, content)
    created = 0
    updated = 0
    errors: list[ImportRowError] = []
    for i, row in enumerate(rows, start=2):
        try:
            name = cell_str(row, "name")
            if not name:
                raise ValueError("name is required")
            data = PartyCreate(
                name=name,
                phone=cell_str(row, "phone"),
                email=cell_str(row, "email"),
                address=cell_str(row, "address"),
                area=cell_str(row, "area"),
                contactPerson=cell_str(row, "contactPerson"),
                ntn=cell_str(row, "ntn"),
                cnic=cell_str(row, "cnic"),
                sTaxRegNo=cell_str(row, "sTaxRegNo"),
                loyaltyNo=cell_str(row, "loyaltyNo"),
                dueDays=cell_int(row, "dueDays", 0),
                creditAllowed=cell_bool(row, "creditAllowed"),
                creditLimit=Decimal(cell_str(row, "creditLimit") or "0"),
                tier=cell_str(row, "tier") or "retail",
            )
            code = cell_str(row, "code")
            existing = await Party.get_or_none(code=code) if code else None
            if existing:
                fields = _to_model_fields(data.model_dump())
                for key, value in fields.items():
                    setattr(existing, key, value)
                await existing.save()
                updated += 1
            else:
                fields = _to_model_fields(data.model_dump())
                if code:
                    await Party.create(code=code, is_walk_in=False, credit_balance=Decimal("0"), active=True, **fields)
                else:
                    seq = await next_value("party_code", 3)
                    await Party.create(
                        code=f"CUST{seq:03d}", is_walk_in=False, credit_balance=Decimal("0"), active=True, **fields
                    )
                created += 1
        except (ValueError, InvalidOperation, KeyError) as exc:
            errors.append(ImportRowError(row=i, message=str(exc)))
    return ImportSummary(created=created, updated=updated, errors=errors)
