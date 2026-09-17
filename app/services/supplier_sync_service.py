"""Suppliers as one company list, at the branch end.

Head office keeps the list. Every supplier on it has a company identity (`company_id`) that is the same at head office
and at every branch; this branch's own `id` and `code` never change, because GRNs, orders and the supplier's ledger
account point at them.

- A supplier added or changed here goes to head office as an outbox event written in the same transaction
  (`announce`). The event says which fields the person changed and which head office revision this copy was at, so head
  office can tell a change made here from one it made itself in the meantime.
- What head office sends (`supplier.upsert`, `supplier.list`) is applied here without writing an event: it came from
  there, and sending it back would bounce between the two for ever.
- Deciding that two suppliers are the same one is head office's job alone, so there is one answer for the whole
  company. A message names this branch's supplier (`localId`) when head office has matched one, and says `hold` while a
  person at head office still has to decide; this branch never guesses.
- Once, when this software first starts (`counters` row `rollout:company-suppliers`), every supplier this branch already
  has goes up, followed by a request for the company list (`queue_existing`).
"""
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from tortoise.transactions import in_transaction

from app.core import logs
from app.core.device_context import get_device_id
from app.models import Counter, KnownBranch, OutboxEvent, Supplier, User

ROLLOUT = "rollout:company-suppliers"
HEAD_OFFICE = "HO"

# Payload name -> column, for every field the company list shares. `code` is left out on purpose: it stays this
# branch's own.
SHARED = {
    "name": "name", "contactPerson": "contact_person", "phone": "phone", "phone2": "phone2", "email": "email",
    "address": "address", "city": "city", "ntn": "ntn", "sTaxRegNo": "s_tax_reg_no", "cnic": "cnic",
    "dueDays": "due_days", "discountPercent": "discount_percent", "remarks": "remarks", "active": "active",
}
_LENGTHS = {
    "name": 160, "contact_person": 120, "phone": 30, "phone2": 30, "email": 180, "address": 255, "city": 80,
    "ntn": 40, "s_tax_reg_no": 40, "cnic": 40, "remarks": 255,
}


class SupplierSyncError(Exception):
    def __init__(self, message: str):
        self.message = message


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value) -> datetime | None:
    if not value:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def state(s: Supplier) -> dict:
    """The supplier as this branch has it, in the words head office reads."""
    return {
        "localId": s.id, "companyId": s.company_id, "code": s.code, "name": s.name, "contactPerson": s.contact_person,
        "phone": s.phone, "phone2": s.phone2, "email": s.email, "address": s.address, "city": s.city, "ntn": s.ntn,
        "sTaxRegNo": s.s_tax_reg_no, "cnic": s.cnic, "dueDays": s.due_days,
        "discountPercent": format(Decimal(s.discount_percent or 0), "f"), "remarks": s.remarks, "active": s.active,
        "origin": s.origin, "baseRev": s.rev, "updatedAt": _iso(s.updated_at),
    }


async def own_code() -> str | None:
    from app.services import registration_service

    identity = await registration_service.current()
    return identity.code if identity else None


async def announce(supplier: Supplier, changed: list[str] | None, user: User | None, *, initial: bool = False) -> None:
    """Tell head office. `changed` names the fields the person changed; None means all of them (a new supplier)."""
    await OutboxEvent.create(
        aggregate_type="Supplier", aggregate_id=supplier.id,
        payload={"supplier": state(supplier), "changed": changed, "initial": initial},
        origin_user_id=str(user.id) if user else None, origin_device_id=get_device_id(),
    )


async def origin_labels() -> tuple[str | None, dict[str, str]]:
    """This branch's code, and the other branches' names, for saying where each supplier came from."""
    return await own_code(), {b.code: b.name for b in await KnownBranch.all()}


def origin_of(s: Supplier, own: str | None, names: dict[str, str]) -> tuple[str, str | None]:
    if s.origin == HEAD_OFFICE:
        return "head-office", "Head office"
    if s.origin is None or s.origin == own:
        return "this-branch", None
    return "other-branch", names.get(s.origin, s.origin)


# ── from head office ─────────────────────────────────────────────────────────────────────────────

async def _free_code(wanted: str | None) -> str:
    """Head office's code when it's free here, so the same supplier reads the same everywhere; else the next SUP number."""
    from app.services.supplier_service import next_code

    code = (wanted or "").strip().upper()
    if code and len(code) <= 20 and not await Supplier.exists(code=code):
        return code
    return await next_code()


def _take(supplier: Supplier, data: dict) -> None:
    for key, column in SHARED.items():
        if key not in data:
            continue
        value = data[key]
        if column == "active":
            value = bool(value)
        elif column == "due_days":
            value = int(value or 0)
        elif column == "discount_percent":
            try:
                value = Decimal(str(value or 0))
            except InvalidOperation:
                value = Decimal("0")
        elif isinstance(value, str):
            value = value.strip()[: _LENGTHS[column]] or None
        if column == "name" and not value:
            continue
        setattr(supplier, column, value)
    supplier.company_id = data["companyId"]
    supplier.origin = (data.get("origin") or HEAD_OFFICE)[:20]
    supplier.rev = int(data.get("rev") or 0)
    supplier.updated_at = _dt(data.get("updatedAt")) or datetime.now(timezone.utc)


async def _apply(data: dict, local_id: str | None, hold: bool) -> str:
    from app.services import accounts_chart_service

    company_id = data.get("companyId")
    if not company_id or not data.get("name"):
        raise SupplierSyncError("Head office sent a supplier without its name or company identity.")
    rev = int(data.get("rev") or 0)
    async with in_transaction():
        supplier = await Supplier.get_or_none(company_id=company_id)
        outcome = "updated"
        if supplier is not None and local_id and local_id != supplier.id:
            other = await Supplier.get_or_none(id=local_id)
            if other is not None and not other.company_id:
                raise SupplierSyncError(
                    f"This branch has both {supplier.name} ({supplier.code}) and {other.name} ({other.code}) for "
                    f"{data.get('name')}. Head office should decide whether {other.code} stays at this branch only."
                )
        if supplier is None and local_id:
            supplier = await Supplier.get_or_none(id=local_id)
            if supplier is not None and supplier.company_id and supplier.company_id != company_id:
                raise SupplierSyncError(
                    f"Head office matched {data.get('name')} to {supplier.name} here, but {supplier.name} is already "
                    "a different supplier on the company list."
                )
            outcome = "linked" if supplier is not None else outcome
        if supplier is None:
            if hold:
                # A person at head office is still deciding whether this is one of ours.
                return "held"
            supplier = Supplier(id=f"sup-{secrets.token_hex(4)}", code=await _free_code(data.get("code")))
            _take(supplier, data)
            await supplier.save(force_create=True)
            outcome = "created"
        elif supplier.company_id == company_id and supplier.rev >= rev:
            return "older"
        else:
            _take(supplier, data)
            await supplier.save()
        # The supplier's ledger account is there from the start and carries its name.
        await accounts_chart_service.supplier_account(supplier)
    return outcome


async def apply_upsert(payload: dict) -> str:
    return await _apply(payload.get("supplier") or {}, payload.get("localId"), bool(payload.get("hold")))


async def apply_list(payload: dict) -> str:
    """The whole company list: after this branch's own suppliers reached head office, and whenever it asks again."""
    outcomes: dict[str, int] = {}
    problems: list[str] = []
    for entry in payload.get("suppliers") or []:
        data = entry.get("supplier") or {}
        try:
            outcome = await _apply(data, entry.get("localId"), bool(entry.get("hold")))
        except SupplierSyncError as exc:
            outcome = "not applied"
            problems.append(exc.message)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    summary = ", ".join(f"{n} {what}" for what, n in sorted(outcomes.items())) or "nothing"
    logs.log.info("suppliers: company list from head office: %s", summary)
    if problems:
        raise SupplierSyncError(f"{len(problems)} supplier(s) couldn't be applied: " + " ".join(problems)[:700])
    return summary


# ── once ─────────────────────────────────────────────────────────────────────────────────────────

async def queue_existing() -> int:
    """Once: every supplier this branch has goes to head office, which matches each to the company list (by name and
    phone, else NTN) or adds it, then sends the list back. Nothing is matched here, so nothing is guessed twice."""
    if await Counter.exists(id=ROLLOUT):
        return 0
    queued = 0
    async with in_transaction():
        for supplier in await Supplier.all().order_by("code"):
            if supplier.company_id:
                continue
            await announce(supplier, None, None, initial=True)
            queued += 1
        # Last, so head office has every one of this branch's suppliers before it answers with the list.
        await OutboxEvent.create(
            aggregate_type="SupplierList", aggregate_id="company-list",
            payload={"suppliers": queued, "requestedAt": datetime.now(timezone.utc).isoformat()},
        )
        await Counter.create(id=ROLLOUT, value=1)
    return queued
