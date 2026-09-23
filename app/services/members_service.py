"""D.Marina members and loyalty points on the branch server.

Everything that creates or changes a member, a points entry or the points rules writes an outbox event
in the same transaction, so head office hears of it and passes it on to the other branches. What head
office sends back (a member signed up elsewhere, points earned elsewhere, rules set at head office) is
applied here without writing an event — it came from there.
"""
import re
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal

from tortoise.expressions import Q

from app.core.device_context import get_device_id
from app.models import LoyaltyEntry, LoyaltySettings, Member, OutboxEvent, Party, User, next_value

ZERO = Decimal("0")
HEAD_OFFICE = "HO"


class MemberError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def branch_code() -> str:
    from app.services import sales_service

    return await sales_service.invoice_prefix()


# ── numbers ──────────────────────────────────────────────────────────────────────────────────────

def normalize_phone(raw: str | None, what: str = "the customer's mobile number") -> str:
    """Digits only, in the local form a shopkeeper reads back: +92 300 1234567 and 3001234567 are both
    03001234567."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0092"):
        digits = digits[4:]
    if digits.startswith("92") and len(digits) == 12:
        digits = "0" + digits[2:]
    if len(digits) == 10 and digits.startswith("3"):
        digits = "0" + digits
    if not 10 <= len(digits) <= 13:
        raise MemberError(f"Enter {what} as 11 digits, like 0300 1234567.")
    return digits


# ── what head office and the other branches are told ───────────────────────────────────────────

async def _member_payload(member: Member) -> dict:
    party = await Party.get_or_none(id=member.party_id) if member.party_id else None
    return {
        "code": member.code, "name": member.name, "phone": member.phone,
        "joinedVia": member.joined_via, "joinedMethod": member.joined_method,
        "homeBranchCode": member.home_branch_code, "active": member.active,
        "partyCode": party.code if party else None, "partyName": party.name if party else None,
        "createdBy": member.created_by_name,
        "createdAt": _iso(member.created_at), "updatedAt": _iso(member.updated_at),
    }


def _entry_payload(entry: LoyaltyEntry, member_code: str) -> dict:
    return {
        "id": str(entry.id), "memberCode": member_code, "kind": entry.kind, "points": entry.points,
        "invoiceNumber": entry.invoice_number, "branchCode": entry.branch_code, "note": entry.note,
        "by": entry.by_name, "at": _iso(entry.at),
    }


def settings_payload(s: LoyaltySettings) -> dict:
    return {
        "enabled": s.enabled, "rupeesPerPoint": str(s.rupees_per_point), "pointValue": str(s.point_value),
        "minRedeemPoints": s.min_redeem_points, "maxRedeemPercent": str(s.max_redeem_percent),
        "updatedAt": _iso(s.updated_at), "updatedBy": s.updated_by_name, "updatedFrom": s.updated_from,
    }


async def _event(aggregate_type: str, aggregate_id: str, payload: dict, user: User | None) -> None:
    await OutboxEvent.create(
        aggregate_type=aggregate_type, aggregate_id=aggregate_id, payload=payload,
        origin_user_id=str(user.id) if user else None, origin_device_id=get_device_id(),
    )


async def _announce_member(member: Member, user: User | None) -> None:
    await _event("Member", member.code, {"member": await _member_payload(member)}, user)


# ── members ──────────────────────────────────────────────────────────────────────────────────────

async def get(code: str) -> Member:
    member = await Member.get_or_none(code=(code or "").strip().upper()).prefetch_related("party")
    if not member:
        raise MemberError("No member with that code.", 404)
    return member


# The Members screen's sort keys and what they sort by. Status reads Active before Switched off, as the column does.
SORTS = {
    "code": "code", "name": "name", "phone": "phone", "points": "points_balance", "joinedVia": "joined_via",
    "branch": "home_branch_code", "party": "party__name", "status": "-active", "joined": "created_at",
}


async def search(
    q: str | None, limit: int, offset: int, sort: str | None = None, order: str | None = None,
) -> tuple[list[Member], int]:
    qs = Member.all()
    text = (q or "").strip()
    if text:
        predicate = Q(code__iexact=text) | Q(name__icontains=text)
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 4:
            predicate |= Q(phone__endswith=digits[-10:] if len(digits) > 10 else digits)
        qs = qs.filter(predicate)
    total = await qs.count()
    field = SORTS.get(sort or "")
    if field and order == "desc":
        field = field[1:] if field.startswith("-") else f"-{field}"
    ordering = (field, "id") if field else ("-created_at",)
    items = await qs.order_by(*ordering).offset(offset).limit(limit).prefetch_related("party")
    return items, total


async def lookup(q: str) -> Member | None:
    """The one member a till entry means: their code, or their mobile number."""
    text = (q or "").strip()
    if not text:
        return None
    member = await Member.get_or_none(code=text.upper())
    if not member:
        try:
            member = await Member.get_or_none(phone=normalize_phone(text))
        except MemberError:
            member = None
    if member:
        await member.fetch_related("party")
    return member


async def _new_code() -> str:
    seq = await next_value("member", 1)
    return f"{await branch_code()}-{seq:06d}"


async def find_or_create(
    name: str | None, phone: str | None, *, joined_via: str, joined_method: str | None,
    user: User | None, party: Party | None = None,
) -> tuple[Member, bool]:
    """The member for this mobile number, signing them up if they're new. One number is one member: a
    known number keeps the name already on file. Links the member to a Party that has none yet."""
    number = normalize_phone(phone)
    member = await Member.get_or_none(phone=number)
    created = changed = False
    if member is None:
        clean = (name or "").strip()
        if not clean:
            raise MemberError("Enter the customer's name.")
        member = await Member.create(
            code=await _new_code(), name=clean[:120], phone=number, joined_via=joined_via,
            joined_method=joined_method, home_branch_code=await branch_code(),
            party=party if party and not party.is_walk_in else None,
            created_by_name=user.name if user else None,
        )
        created = changed = True
    elif not member.active:
        raise MemberError(f"Member {member.code} ({member.name}) is switched off. Switch them back on under Members first.")
    elif party and not party.is_walk_in and member.party_id is None:
        member.party = party
        await member.save(update_fields=["party_id", "updated_at"])
        changed = True
    if changed:
        await _announce_member(member, user)
    await member.fetch_related("party")
    return member, created


async def create_from_screen(name: str, phone: str, party_id: str | None, user: User) -> Member:
    party = await Party.get_or_none(id=party_id) if party_id else None
    if await Member.exists(phone=normalize_phone(phone)):
        existing = await Member.get(phone=normalize_phone(phone))
        raise MemberError(f"{existing.phone} already belongs to {existing.name} ({existing.code}).")
    member, _ = await find_or_create(name, phone, joined_via="members-screen", joined_method=None, user=user, party=party)
    return member


async def update(code: str, data: dict, user: User) -> Member:
    member = await get(code)
    if "name" in data and data["name"] is not None:
        clean = data["name"].strip()
        if not clean:
            raise MemberError("A member needs a name.")
        member.name = clean[:120]
    if "phone" in data and data["phone"] is not None:
        number = normalize_phone(data["phone"])
        if number != member.phone:
            clash = await Member.get_or_none(phone=number)
            if clash:
                raise MemberError(f"{number} already belongs to {clash.name} ({clash.code}).")
            member.phone = number
    if "partyId" in data:
        member.party = await Party.get_or_none(id=data["partyId"]) if data["partyId"] else None
    if "active" in data and data["active"] is not None:
        member.active = bool(data["active"])
    await member.save()
    await _announce_member(member, user)
    await member.fetch_related("party")
    return member


async def entries(member: Member, limit: int = 100) -> list[LoyaltyEntry]:
    return await LoyaltyEntry.filter(member=member).order_by("-at").limit(limit)


# ── points ───────────────────────────────────────────────────────────────────────────────────────

async def settings() -> LoyaltySettings:
    current = await LoyaltySettings.get_or_none(id=1)
    if current is None:
        current = await LoyaltySettings.create(id=1)
    return current


async def update_settings(data: dict, user: User) -> LoyaltySettings:
    s = await settings()
    if data.get("rupeesPerPoint") is not None:
        if Decimal(str(data["rupeesPerPoint"])) <= 0:
            raise MemberError("Rupees for one point must be more than zero.")
        s.rupees_per_point = Decimal(str(data["rupeesPerPoint"]))
    if data.get("pointValue") is not None:
        if Decimal(str(data["pointValue"])) <= 0:
            raise MemberError("What a point is worth must be more than zero.")
        s.point_value = Decimal(str(data["pointValue"]))
    if data.get("minRedeemPoints") is not None:
        if int(data["minRedeemPoints"]) < 0:
            raise MemberError("The fewest points to redeem can't be negative.")
        s.min_redeem_points = int(data["minRedeemPoints"])
    if data.get("maxRedeemPercent") is not None:
        pct = Decimal(str(data["maxRedeemPercent"]))
        if not ZERO < pct <= 100:
            raise MemberError("The share of a bill points may pay is 1 to 100%.")
        s.max_redeem_percent = pct
    if data.get("enabled") is not None:
        s.enabled = bool(data["enabled"])
    s.updated_at = datetime.now(timezone.utc)
    s.updated_by_name = user.name
    s.updated_from = await branch_code()
    await s.save()
    await _event("LoyaltySettings", "settings", {"settings": settings_payload(s)}, user)
    return s


async def _refresh_balance(member: Member) -> None:
    total = sum(await LoyaltyEntry.filter(member=member).values_list("points", flat=True))
    if member.points_balance != total:
        member.points_balance = total
        await Member.filter(id=member.id).update(points_balance=total)


async def add_entry(
    member: Member, kind: str, points: int, *, invoice_number: str | None, note: str | None, user: User | None,
) -> LoyaltyEntry:
    entry = await LoyaltyEntry.create(
        member=member, kind=kind, points=points, invoice_number=invoice_number,
        branch_code=await branch_code(), note=(note or None) and note[:255],
        by_name=user.name if user else None, at=datetime.now(timezone.utc),
    )
    await _refresh_balance(member)
    await _event("LoyaltyEntry", str(entry.id), {"entry": _entry_payload(entry, member.code)}, user)
    return entry


def points_for_amount(amount: Decimal, s: LoyaltySettings) -> int:
    """Points a bill earns: whole points for each full `rupees_per_point` paid."""
    if amount <= 0 or s.rupees_per_point <= 0:
        return 0
    return int((amount / s.rupees_per_point).to_integral_value(rounding=ROUND_DOWN))


def redeem_points(amount: Decimal, s: LoyaltySettings) -> int:
    """The points behind a rupee amount of points on a bill. The amount has to be a whole number of points."""
    points = amount / s.point_value
    if points != points.to_integral_value():
        raise MemberError(f"Points come off a bill in steps of Rs {s.point_value.normalize():f}, so adjust the points amount.")
    return int(points)


# ── what head office sends ───────────────────────────────────────────────────────────────────────

async def apply_member(data: dict) -> str:
    code = (data.get("code") or "").strip().upper()
    if not code:
        raise MemberError("A member from head office has no code.")
    phone = normalize_phone(data.get("phone"))
    member = await Member.get_or_none(code=code)
    incoming_at = _dt(data.get("updatedAt"))
    if member is None:
        clash = await Member.get_or_none(phone=phone)
        if clash:
            raise MemberError(f"{phone} is already member {clash.code} ({clash.name}) at this branch, so {code} wasn't added here.")
        await Member.create(
            code=code, name=(data.get("name") or code)[:120], phone=phone, joined_via=data.get("joinedVia") or "till",
            joined_method=data.get("joinedMethod"), home_branch_code=(data.get("homeBranchCode") or HEAD_OFFICE)[:10],
            active=bool(data.get("active", True)), created_by_name=data.get("createdBy"),
        )
        return "created"
    if incoming_at and member.updated_at and incoming_at <= member.updated_at:
        return "older"
    if phone != member.phone and await Member.exists(phone=phone):
        raise MemberError(f"{phone} is already another member at this branch, so {code}'s new number wasn't applied.")
    await Member.filter(id=member.id).update(
        name=(data.get("name") or member.name)[:120], phone=phone, active=bool(data.get("active", member.active)),
        updated_at=incoming_at or datetime.now(timezone.utc),
    )
    return "updated"


async def apply_entry(data: dict) -> str:
    entry_id = data.get("id")
    if not entry_id:
        raise MemberError("A points entry from head office has no id.")
    if await LoyaltyEntry.exists(id=entry_id):
        return "already-here"
    member = await Member.get_or_none(code=(data.get("memberCode") or "").upper())
    if member is None:
        raise MemberError(f"Points for member {data.get('memberCode')}, who isn't known at this branch.")
    await LoyaltyEntry.create(
        id=entry_id, member=member, kind=data.get("kind") or "adjust", points=int(data.get("points") or 0),
        invoice_number=data.get("invoiceNumber"), branch_code=(data.get("branchCode") or HEAD_OFFICE)[:10],
        note=data.get("note"), by_name=data.get("by"), at=_dt(data.get("at")) or datetime.now(timezone.utc),
    )
    await _refresh_balance(member)
    return "created"


async def apply_settings(data: dict) -> str:
    s = await settings()
    incoming_at = _dt(data.get("updatedAt"))
    if s.updated_at and incoming_at and incoming_at <= s.updated_at:
        return "older"
    s.enabled = bool(data.get("enabled", s.enabled))
    s.rupees_per_point = Decimal(str(data.get("rupeesPerPoint") or s.rupees_per_point))
    s.point_value = Decimal(str(data.get("pointValue") or s.point_value))
    s.min_redeem_points = int(data.get("minRedeemPoints") if data.get("minRedeemPoints") is not None else s.min_redeem_points)
    s.max_redeem_percent = Decimal(str(data.get("maxRedeemPercent") or s.max_redeem_percent))
    s.updated_at = incoming_at or datetime.now(timezone.utc)
    s.updated_by_name = data.get("updatedBy")
    s.updated_from = (data.get("updatedFrom") or HEAD_OFFICE)[:10]
    await s.save()
    return "updated"
