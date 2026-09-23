from decimal import Decimal, InvalidOperation

from tortoise.expressions import Q
from tortoise.transactions import atomic

from app.models import Party, PartyContact
# Customer codes start at CUST001 on a new system and carry on after the highest one on a system with data.
from app.services.numbering_service import next_number
from app.schemas.import_result import ImportRowError, ImportSummary
from app.schemas.parties import PartyContactIn, PartyCreate, PartyUpdate
from app.services import media_service
from app.services.import_service import cell_bool, cell_decimal, cell_int, cell_str, cell_str_any, parse_rows, row_error


class PartyError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


_FIELD_MAP = {
    "contactPerson": "contact_person",
    "sTaxRegNo": "s_tax_reg_no",
    "loyaltyNo": "loyalty_no",
    "dueDays": "due_days",
    "creditAllowed": "credit_allowed",
    "creditLimit": "credit_limit",
    "subArea": "sub_area",
}
_TEXT_FIELDS = {
    "name", "phone", "telephone", "fax", "email", "address", "address2", "city", "area", "subArea",
    "category", "contactPerson", "ntn", "cnic", "sTaxRegNo", "loyaltyNo",
}


def _to_model_fields(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if key in _TEXT_FIELDS and isinstance(value, str):
            value = value.strip() or None
        out[_FIELD_MAP.get(key, key)] = value
    return out


SEARCH_LIMIT = 50
LIST_LIMIT = 500


async def find_by_query(q: str, include_inactive: bool = False) -> list[Party]:
    """Party code, loyalty number, phone, or name.

    Name matching matters: someone attaching a customer at the counter usually knows the name,
    not the six-character code — without it, typing "Ali Traders" found nothing at all. And the
    filtering happens in the database rather than by pulling every active Party into Python and
    looping, which was a full table scan on each lookup.

    Exact code/loyalty hits sort first, because Billing takes the first result as *the* match.
    Billing never asks for switched-off Parties; the registry does, so they can be switched back on.
    """
    query = q.strip()
    if not query:
        return []
    digits = "".join(ch for ch in query if ch.isdigit())
    predicate = Q(code__iexact=query) | Q(loyalty_no__iexact=query) | Q(name__icontains=query)
    if len(digits) >= 7:
        # Match on the last 7 digits so a stored "0300-1234567" is found by either the bare
        # number or the full dialled form, whichever the staff typed.
        predicate |= Q(phone__endswith=digits[-7:]) | Q(telephone__endswith=digits[-7:])
    base = Q(is_walk_in=False) if include_inactive else Q(active=True, is_walk_in=False)
    hits = await Party.filter(base & predicate).limit(SEARCH_LIMIT)
    upper = query.upper()
    hits.sort(key=lambda p: 0 if p.code.upper() == upper or (p.loyalty_no or "").upper() == upper else 1)
    return hits


async def list_all(include_inactive: bool = False) -> list[Party]:
    """Bounded — a real branch's Party master is not something a screen should ever pull whole.
    Callers that need to find one Party pass `q` (see find_by_query) rather than paging this."""
    qs = Party.all() if include_inactive else Party.filter(active=True)
    return await qs.order_by("-active", "name").limit(LIST_LIMIT)


async def create(data: PartyCreate) -> Party:
    code = await next_number("party_code", Party, "code", "CUST", 3)
    fields = _to_model_fields(data.model_dump())
    await _refuse_switched_off_group(fields)
    return await Party.create(code=code, is_walk_in=False, credit_balance=Decimal("0"), active=True, **fields)


async def _refuse_switched_off_group(fields: dict, party: Party | None = None) -> None:
    """A customer group switched off in Item Lists can't be given to a customer from the form."""
    from app.services import masters_service

    try:
        await masters_service.refuse_switched_off_values("parties", fields, {"category": party.category} if party else None)
    except masters_service.MastersError as exc:
        raise PartyError(exc.message) from exc


async def _editable(party_id: str) -> Party:
    party = await Party.get_or_none(id=party_id)
    if not party:
        raise PartyError("Party not found", status=404)
    if party.is_walk_in:
        # The walk-in Party stands for every customer who didn't give a name. Renaming it, giving it
        # credit or switching it off would change every anonymous sale at once.
        raise PartyError("The walk-in Party can't be edited.")
    return party


async def update(party_id: str, data: PartyUpdate) -> Party:
    party = await _editable(party_id)
    changes = _to_model_fields(data.model_dump(exclude_unset=True))
    await _refuse_switched_off_group(changes, party)
    for key, value in changes.items():
        if key in ("name", "due_days", "credit_allowed", "credit_limit", "tier", "active") and value is None:
            continue
        setattr(party, key, value)
    await party.save()
    return party


async def detail(party_id: str) -> tuple[Party, list[PartyContact]]:
    party = await Party.get_or_none(id=party_id)
    if not party:
        raise PartyError("Party not found", status=404)
    return party, await PartyContact.filter(party_id=party.id).order_by("position")


@atomic()
async def replace_contacts(party_id: str, contacts: list[PartyContactIn]) -> list[PartyContact]:
    """The grid on the form is saved as a whole: what's on screen is what's stored. Rows with
    nothing filled in are dropped rather than kept as empty lines."""
    party = await _editable(party_id)
    await PartyContact.filter(party_id=party.id).delete()
    saved = []
    for position, contact in enumerate(contacts):
        values = {
            "cell_no": contact.cellNo, "contact_person": contact.contactPerson, "email": contact.email,
            "office_address": contact.officeAddress, "res_address": contact.resAddress, "remarks": contact.remarks,
        }
        values = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in values.items()}
        if not any(values.values()):
            continue
        saved.append(await PartyContact.create(party=party, position=position, **values))
    return saved


async def set_picture(party_id: str, content: bytes) -> Party:
    party = await _editable(party_id)
    try:
        party.picture = media_service.save_picture("parties", str(party.id), content, replacing=party.picture)
    except media_service.MediaError as exc:
        raise PartyError(exc.message)
    await party.save(update_fields=["picture"])
    return party


async def remove_picture(party_id: str) -> Party:
    party = await _editable(party_id)
    media_service.remove_picture(party.picture)
    party.picture = None
    await party.save(update_fields=["picture"])
    return party


# Import column → API field, for every optional text column a file may carry.
_IMPORT_TEXT = (
    "phone", "telephone", "fax", "email", "address", "address2", "city", "area", "subArea", "category",
    "contactPerson", "ntn", "cnic", "sTaxRegNo", "loyaltyNo",
)


async def import_parties(filename: str, content: bytes) -> ImportSummary:
    """Upserts by `code` when the file provides one; otherwise always creates a new party with
    an auto-generated CUST### code, same as the Customer Registry screen would.

    An update writes only the cells the file filled, so a file of names and cities doesn't blank
    the phone numbers, NTNs and credit limits already on record."""
    rows = parse_rows(filename, content)
    created = 0
    updated = 0
    errors: list[ImportRowError] = []
    for i, row in enumerate(rows, start=2):
        try:
            # "Party name", "Cell no", "Price tier" and "Balance limit" are how the registry's own export
            # heads those columns, so an exported file imports straight back.
            name = cell_str_any(row, "name", "partyName")
            if not name:
                raise ValueError("name is required")
            cells: dict = {key: cell_str(row, key) for key in _IMPORT_TEXT}
            cells["phone"] = cell_str_any(row, "phone", "cellNo")
            if cell_str(row, "dueDays") is not None:
                cells["dueDays"] = cell_int(row, "dueDays", 0)
            if cell_str(row, "creditAllowed") is not None:
                cells["creditAllowed"] = cell_bool(row, "creditAllowed")
            limit_key = "creditLimit" if cell_str(row, "creditLimit") is not None else "balanceLimit"
            if cell_str(row, limit_key) is not None:
                cells["creditLimit"] = cell_decimal(row, limit_key)
            tier = cell_str_any(row, "tier", "priceTier")
            if tier is not None:
                cells["tier"] = tier.lower()
            provided = {k: v for k, v in cells.items() if v is not None}
            data = PartyCreate(name=name, **provided)

            # Party codes are upper case everywhere they're shown; "ali001" in a file means ALI001.
            code = (cell_str(row, "code") or "").upper() or None
            existing = await Party.get_or_none(code=code) if code else None
            if existing:
                if existing.is_walk_in:
                    raise ValueError(f"{code} is the walk-in Party and can't be imported over")
                for key, value in _to_model_fields({"name": name, **provided}).items():
                    setattr(existing, key, value)
                await existing.save()
                updated += 1
            else:
                fields = _to_model_fields(data.model_dump())
                if not code:
                    code = await next_number("party_code", Party, "code", "CUST", 3)
                await Party.create(code=code, is_walk_in=False, credit_balance=Decimal("0"), active=True, **fields)
                created += 1
        except (ValueError, InvalidOperation, KeyError) as exc:
            errors.append(ImportRowError(row=i, message=row_error(exc)))
    return ImportSummary(created=created, updated=updated, errors=errors)
