from decimal import Decimal

from app.models import Party, next_value
from app.schemas.parties import PartyCreate, PartyUpdate

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
