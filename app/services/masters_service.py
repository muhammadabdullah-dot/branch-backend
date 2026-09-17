"""The branch's lists and settings: Item lists, reasons, payment methods, what bills print, gift voucher rules.

See models/masters.py for why Item lists stay as text on the Item and the list only describes that text. Everything
here is local to this branch: nothing is sent to head office (the Items and Parties it renames reach head office
through the usual snapshot).
"""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from tortoise import Tortoise
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from app.models import Account, Counter, ListEntry, PaymentMethod, ShopSetting, User


class MastersError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


# ── Item lists ──────────────────────────────────────────────────────────────────────────────────

# kind -> (table, column, longest value the column holds, what one is called)
ITEM_LISTS: dict[str, tuple[str, str, int, str]] = {
    "department": ("products", "department", 80, "department"),
    "category": ("products", "category", 80, "category"),
    "class": ("products", "item_class", 80, "class"),
    "subclass": ("products", "subclass", 80, "sub-class"),
    "manufacturer": ("products", "manufacturer", 120, "manufacturer"),
    "brand": ("products", "brand", 120, "brand"),
    "unit": ("products", "unit", 20, "unit"),
    "pack-unit": ("products", "pack_unit", 40, "pack unit"),
    "gst-rate": ("products", "tax_rate", 6, "GST rate"),
    "customer-group": ("parties", "category", 80, "customer group"),
}
# The Item form's fields, by the list each one picks from.
ITEM_FIELD_KINDS: dict[str, str] = {
    "department": "department", "category": "category", "item_class": "class", "subclass": "subclass",
    "manufacturer": "manufacturer", "brand": "brand", "unit": "unit", "pack_unit": "pack-unit", "tax_rate": "gst-rate",
}
# What a list is called in an import file, besides its own key.
_KIND_NAMES = {
    "departments": "department", "categories": "category", "classes": "class", "sub-class": "subclass", "sub-classes": "subclass",
    "subclasses": "subclass", "sub class": "subclass", "manufacturers": "manufacturer", "brands": "brand", "units": "unit",
    "pack units": "pack-unit", "pack unit": "pack-unit", "pack-units": "pack-unit", "gst rate": "gst-rate", "gst rates": "gst-rate",
    "gst": "gst-rate", "customer groups": "customer-group", "customer group": "customer-group",
}


def item_kind(kind: str) -> str:
    key = (kind or "").strip().lower()
    key = _KIND_NAMES.get(key, key)
    if key not in ITEM_LISTS:
        raise MastersError(f"There's no list called {kind}.")
    return key


def rate_code(value) -> str:
    """17, 17.0 and '17.00' are the same rate. Written the short way: 17, 7.5, 0."""
    try:
        rate = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise MastersError("A GST rate is a number from 0 to 100.")
    if rate < 0 or rate > 100:
        raise MastersError("A GST rate is a number from 0 to 100.")
    text = format(rate.normalize(), "f")
    return "0" if text in ("-0", "0.00") else text


def _clean_value(kind: str, name: str) -> str:
    _, _, longest, label = ITEM_LISTS[kind]
    if kind == "gst-rate":
        return rate_code(str(name).strip().rstrip("%").strip())
    value = re.sub(r"\s+", " ", (name or "").strip())
    if not value:
        raise MastersError(f"Type the {label}.")
    if len(value) > longest:
        raise MastersError(f"A {label} can be at most {longest} characters.")
    return value


async def _used_values(kind: str) -> dict[str, int]:
    table, column, _, _ = ITEM_LISTS[kind]
    conn = Tortoise.get_connection("default")
    if kind == "gst-rate":
        rows = await conn.execute_query_dict(f"SELECT CAST({column} AS REAL) AS v, COUNT(*) AS n FROM {table} GROUP BY CAST({column} AS REAL)")
        out: dict[str, int] = {}
        for row in rows:
            if row["v"] is None:
                continue
            code = rate_code(row["v"])
            out[code] = out.get(code, 0) + int(row["n"])
        return out
    rows = await conn.execute_query_dict(
        f"SELECT {column} AS v, COUNT(*) AS n FROM {table} WHERE {column} IS NOT NULL AND TRIM({column}) != '' GROUP BY {column}"
    )
    return {row["v"]: int(row["n"]) for row in rows}


async def _uses_of(kind: str, code: str) -> int:
    table, column, _, _ = ITEM_LISTS[kind]
    conn = Tortoise.get_connection("default")
    if kind == "gst-rate":
        rows = await conn.execute_query_dict(f"SELECT COUNT(*) AS n FROM {table} WHERE CAST({column} AS REAL) = ?", [float(code)])
    else:
        rows = await conn.execute_query_dict(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?", [code])
    return int(rows[0]["n"]) if rows else 0


async def _add_missing(kind: str, codes) -> None:
    known = set(await ListEntry.filter(kind=kind).values_list("code", flat=True))
    missing = [code for code in codes if code not in known]
    if not missing:
        return
    try:
        await ListEntry.bulk_create([ListEntry(kind=kind, code=c, name=c, active=True) for c in missing], batch_size=500)
    except IntegrityError:
        # Two screens opened the list at the same moment; add what the other didn't.
        for code in missing:
            if not await ListEntry.exists(kind=kind, code=code):
                try:
                    await ListEntry.create(kind=kind, code=code, name=code, active=True)
                except IntegrityError:
                    pass


# Offered once, then the branch's own: the customer groups the app used to suggest, and the GST rates in use in
# Pakistan (standard, exempt). Nothing uses them until someone picks them, so any of them can be deleted.
STARTER_ITEM_VALUES: dict[str, list[str]] = {
    "customer-group": ["Retail", "Wholesale", "Corporate", "Doctor", "Hospital", "Pharmacy", "Staff", "Institution"],
    "gst-rate": ["0", "18"],
}
_ITEM_STARTERS_MARK = "rollout:starter-item-lists"
_item_starters_ready = False


async def _ensure_item_starters() -> None:
    global _item_starters_ready
    if _item_starters_ready:
        return
    if not await Counter.exists(id=_ITEM_STARTERS_MARK):
        for kind, values in STARTER_ITEM_VALUES.items():
            await _add_missing(kind, values)
        try:
            await Counter.create(id=_ITEM_STARTERS_MARK, value=1)
        except IntegrityError:
            pass
    _item_starters_ready = True


async def sync_item_list(kind: str) -> dict[str, int]:
    """Every value written on an Item (or Party) is on its list, whether it came from the Item form, an import, or
    was there before lists existed. Returns how many records use each value."""
    await _ensure_item_starters()
    used = await _used_values(kind)
    await _add_missing(kind, used.keys())
    return used


async def item_list(kind: str) -> list[tuple[ListEntry, int]]:
    kind = item_kind(kind)
    used = await sync_item_list(kind)
    entries = await ListEntry.filter(kind=kind).order_by("name")
    return [(entry, used.get(entry.code, 0)) for entry in entries]


async def item_list_summary() -> list[dict]:
    """How long each list is, for the tabs."""
    out = []
    for kind in ITEM_LISTS:
        await sync_item_list(kind)
        total = await ListEntry.filter(kind=kind).count()
        off = await ListEntry.filter(kind=kind, active=False).count()
        out.append({"kind": kind, "total": total, "switchedOff": off})
    return out


async def choices() -> dict[str, list[str]]:
    """What the Item and Party forms offer: switched-on values only."""
    out: dict[str, list[str]] = {}
    for kind in ITEM_LISTS:
        await sync_item_list(kind)
        codes = await ListEntry.filter(kind=kind, active=True).values_list("code", flat=True)
        if kind == "gst-rate":
            out[kind] = sorted(codes, key=lambda c: Decimal(c))
        else:
            out[kind] = sorted(codes, key=str.lower)
    return out


async def add_item_entry(kind: str, name: str, user: User) -> ListEntry:
    kind = item_kind(kind)
    code = _clean_value(kind, name)
    label = ITEM_LISTS[kind][3]
    await sync_item_list(kind)
    clash = await _same_entry(kind, code)
    if clash:
        raise MastersError(f"The {label} {_shown(kind, clash.code)} is already on the list.")
    return await ListEntry.create(kind=kind, code=code, name=code, active=True, updated_by_name=user.name)


async def _same_entry(kind: str, code: str, except_id=None) -> ListEntry | None:
    qs = ListEntry.filter(kind=kind, code=code) if kind == "gst-rate" else ListEntry.filter(kind=kind, code__iexact=code)
    if except_id is not None:
        qs = qs.exclude(id=except_id)
    return await qs.first()


def _shown(kind: str, code: str) -> str:
    return f"{code}%" if kind == "gst-rate" else code


async def _move_records(conn, kind: str, old: str, new: str) -> int:
    table, column, _, _ = ITEM_LISTS[kind]
    if kind == "gst-rate":
        count = await conn.execute_query_dict(f"SELECT COUNT(*) AS n FROM {table} WHERE CAST({column} AS REAL) = ?", [float(old)])
        await conn.execute_query(f"UPDATE {table} SET {column} = ? WHERE CAST({column} AS REAL) = ?", [new, float(old)])
    else:
        count = await conn.execute_query_dict(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?", [old])
        await conn.execute_query(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", [new, old])
    return int(count[0]["n"]) if count else 0


async def update_item_entry(entry_id: str, name: str | None, active: bool | None, merge: bool, user: User) -> tuple[ListEntry, int, int]:
    """Rename (every Item or Party using it changes too), merge into another entry, or switch on/off.
    Returns the entry, how many records were changed, and how many use it now."""
    entry = await ListEntry.get_or_none(id=entry_id)
    if not entry or entry.kind not in ITEM_LISTS:
        raise MastersError("That list entry doesn't exist.", 404)
    kind, label = entry.kind, ITEM_LISTS[entry.kind][3]
    moved = 0
    if name is not None:
        new_code = _clean_value(kind, name)
        if new_code != entry.code:
            other = await _same_entry(kind, new_code, except_id=entry.id)
            if other and not merge:
                raise MastersError(
                    f"The {label} {_shown(kind, other.code)} is already on the list. Merge the two to move everything "
                    f"from {_shown(kind, entry.code)} onto {_shown(kind, other.code)}.", 409,
                )
            async with in_transaction() as conn:
                if other:
                    moved = await _move_records(conn, kind, entry.code, other.code)
                    other.active = other.active or entry.active if active is None else active
                    other.updated_by_name = user.name
                    await other.save(using_db=conn)
                    await entry.delete(using_db=conn)
                    entry = other
                else:
                    moved = await _move_records(conn, kind, entry.code, new_code)
                    entry.code = new_code
                    entry.name = new_code
                    entry.updated_by_name = user.name
                    await entry.save(using_db=conn)
    if active is not None and active != entry.active:
        entry.active = active
        entry.updated_by_name = user.name
        await entry.save()
    return entry, moved, await _uses_of(kind, entry.code)


async def delete_item_entry(entry_id: str) -> None:
    entry = await ListEntry.get_or_none(id=entry_id)
    if not entry or entry.kind not in ITEM_LISTS:
        raise MastersError("That list entry doesn't exist.", 404)
    uses = await _uses_of(entry.kind, entry.code)
    if uses:
        what = "customers" if entry.kind == "customer-group" else "Items"
        raise MastersError(
            f"{uses:,} {what} still use {_shown(entry.kind, entry.code)}. Switch it off instead, or rename it into another "
            f"{ITEM_LISTS[entry.kind][3]}."
        )
    await entry.delete()


async def refuse_switched_off_values(table: str, values: dict, before: dict | None = None) -> None:
    """The Item and Party forms can't put a switched-off value on a record. A value the record already had is left
    alone, so saving an old Item doesn't fail over a brand switched off since. Imports aren't checked: a legacy file
    is loaded as it is, and anything new it brings shows up on the lists."""
    for kind, (kind_table, column, _, label) in ITEM_LISTS.items():
        if kind_table != table or column not in values:
            continue
        value = values[column]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        code = rate_code(value) if kind == "gst-rate" else str(value)
        if before is not None and before.get(column) is not None:
            was = rate_code(before[column]) if kind == "gst-rate" else str(before[column])
            if was == code:
                continue
        if await ListEntry.exists(kind=kind, code=code, active=False):
            raise MastersError(f"The {label} {_shown(kind, code)} is switched off in Item Lists. Pick another, or switch it back on there.")


async def import_item_lists(rows: list[dict], user: User) -> tuple[int, int, list[tuple[int, str]]]:
    """Columns: List (Department, Brand, GST rate, Customer group...), Name, and optionally Active (yes/no)."""
    from app.services.import_service import cell_str_any

    created = updated = 0
    errors: list[tuple[int, str]] = []
    synced: set[str] = set()
    for i, row in enumerate(rows, start=2):
        try:
            kind = item_kind(cell_str_any(row, "list", "LIST", "kind", "KIND") or "")
            if kind not in synced:
                await sync_item_list(kind)
                synced.add(kind)
            code = _clean_value(kind, cell_str_any(row, "name", "NAME", "value", "VALUE") or "")
            active_raw = (cell_str_any(row, "active", "ACTIVE", "switched on") or "").strip().lower()
            active = None if not active_raw else active_raw in ("yes", "y", "true", "1", "on", "active")
            entry = await _same_entry(kind, code)
            if entry is None:
                await ListEntry.create(kind=kind, code=code, name=code, active=True if active is None else active, updated_by_name=user.name)
                created += 1
            elif active is not None and entry.active != active:
                entry.active = active
                entry.updated_by_name = user.name
                await entry.save()
                updated += 1
        except MastersError as exc:
            errors.append((i, exc.message))
    return created, updated, errors


# ── Reasons ─────────────────────────────────────────────────────────────────────────────────────

# kind -> (table, column, longest code the record holds, what the list is for)
REASON_KINDS: dict[str, tuple[str, str, int, str]] = {
    "adjustment": ("adjustments", "reason", 20, "stock adjustments"),
    "purchase-return": ("purchase_returns", "reason", 20, "returns to suppliers"),
    "sale-return": ("return_records", "reason", 40, "customer returns"),
}
# Codes the software itself relies on: the books post damaged and expired stock to their own loss accounts, and every
# adjustment and return already recorded carries one of these.
BUILTIN_REASONS: list[tuple[str, str, str, str | None]] = [
    ("adjustment", "damage", "Damaged", "remove"),
    ("adjustment", "expiry", "Expired", "remove"),
    ("adjustment", "found", "Found", "add"),
    ("purchase-return", "damaged", "Damaged", None),
    ("purchase-return", "expired", "Expired", None),
    ("purchase-return", "wrong-item", "Wrong item", None),
    ("purchase-return", "overstock", "Overstock", None),
    ("purchase-return", "other", "Other", None),
]
# Offered once, then the branch's own to reword or delete.
STARTER_REASONS: list[tuple[str, str, str, str | None]] = [
    ("adjustment", "lost", "Lost or stolen", "remove"),
    ("sale-return", "changed-mind", "Customer changed their mind", None),
    ("sale-return", "faulty", "Damaged or faulty", None),
    ("sale-return", "expired", "Expired or close to expiry", None),
    ("sale-return", "wrong-item", "Wrong item sold", None),
    ("sale-return", "other", "Other", None),
]
_STARTERS_MARK = "rollout:starter-reasons"
_reasons_ready = False


def reason_kind(kind: str) -> str:
    key = (kind or "").strip().lower()
    if key not in REASON_KINDS:
        raise MastersError(f"There's no reason list called {kind}.")
    return key


async def ensure_reasons() -> None:
    """Built-in reasons always exist; starter reasons are added once; a code already on a record but missing from the
    list is added, so every record keeps a readable reason."""
    global _reasons_ready
    if _reasons_ready:
        return
    for order, (kind, code, name, effect) in enumerate(BUILTIN_REASONS):
        entry = await ListEntry.get_or_none(kind=kind, code=code)
        if entry is None:
            try:
                await ListEntry.create(kind=kind, code=code, name=name, effect=effect, builtin=True, active=True, sort_order=order)
            except IntegrityError:
                pass
        elif not entry.builtin:
            entry.builtin = True
            entry.effect = effect
            await entry.save(update_fields=["builtin", "effect", "updated_at"])
    if not await Counter.exists(id=_STARTERS_MARK):
        for order, (kind, code, name, effect) in enumerate(STARTER_REASONS, start=len(BUILTIN_REASONS)):
            if not await ListEntry.exists(kind=kind, code=code):
                await ListEntry.create(kind=kind, code=code, name=name, effect=effect, active=True, sort_order=order)
        try:
            await Counter.create(id=_STARTERS_MARK, value=1)
        except IntegrityError:
            pass
    conn = Tortoise.get_connection("default")
    for kind, (table, column, _, _) in REASON_KINDS.items():
        rows = await conn.execute_query_dict(f"SELECT DISTINCT {column} AS v FROM {table} WHERE {column} IS NOT NULL AND {column} != ''")
        for row in rows:
            if not await ListEntry.exists(kind=kind, code=row["v"]):
                await ListEntry.create(kind=kind, code=row["v"], name=row["v"].replace("-", " ").capitalize(),
                                       effect="remove" if kind == "adjustment" else None, active=True, sort_order=99)
    _reasons_ready = True


async def _reason_uses(kind: str) -> dict[str, int]:
    table, column, _, _ = REASON_KINDS[kind]
    rows = await Tortoise.get_connection("default").execute_query_dict(
        f"SELECT {column} AS v, COUNT(*) AS n FROM {table} WHERE {column} IS NOT NULL GROUP BY {column}"
    )
    return {row["v"]: int(row["n"]) for row in rows}


async def reasons(kind: str | None = None) -> list[tuple[ListEntry, int]]:
    await ensure_reasons()
    kinds = [reason_kind(kind)] if kind else list(REASON_KINDS)
    out: list[tuple[ListEntry, int]] = []
    for k in kinds:
        uses = await _reason_uses(k)
        for entry in await ListEntry.filter(kind=k).order_by("sort_order", "name"):
            out.append((entry, uses.get(entry.code, 0)))
    return out


async def reason_entries(kind: str) -> dict[str, ListEntry]:
    """Every reason of a kind by code, switched-off ones too, for labelling records."""
    await ensure_reasons()
    return {entry.code: entry for entry in await ListEntry.filter(kind=kind)}


async def require_reason(kind: str, code: str | None) -> ListEntry:
    """The reason a new record is given must be on the list and switched on."""
    await ensure_reasons()
    entry = await ListEntry.get_or_none(kind=kind, code=(code or "").strip())
    if entry is None:
        raise MastersError("Pick a reason from the list.")
    if not entry.active:
        raise MastersError(f"The reason {entry.name} is switched off. Pick another.")
    return entry


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "reason"


async def add_reason(kind: str, name: str, effect: str | None, user: User) -> ListEntry:
    kind = reason_kind(kind)
    await ensure_reasons()
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        raise MastersError("Type the reason.")
    if len(name) > 80:
        raise MastersError("A reason can be at most 80 characters.")
    if await ListEntry.filter(kind=kind, name__iexact=name).exists():
        raise MastersError(f"{name} is already on the list.")
    if kind == "adjustment":
        if effect not in ("add", "remove"):
            raise MastersError("Say whether this reason adds stock or takes it off.")
    else:
        effect = None
    longest = REASON_KINDS[kind][2]
    base = _slug(name)[:longest].strip("-")
    code, n = base, 2
    while await ListEntry.exists(kind=kind, code=code):
        suffix = f"-{n}"
        code = f"{base[:longest - len(suffix)]}{suffix}"
        n += 1
    order = (await ListEntry.filter(kind=kind).count()) + 100
    return await ListEntry.create(kind=kind, code=code, name=name, effect=effect, active=True, sort_order=order, updated_by_name=user.name)


async def update_reason(entry_id: str, name: str | None, active: bool | None, effect: str | None, user: User) -> tuple[ListEntry, int]:
    await ensure_reasons()
    entry = await ListEntry.get_or_none(id=entry_id)
    if not entry or entry.kind not in REASON_KINDS:
        raise MastersError("That reason doesn't exist.", 404)
    uses = (await _reason_uses(entry.kind)).get(entry.code, 0)
    if name is not None:
        name = re.sub(r"\s+", " ", name.strip())
        if not name:
            raise MastersError("Type the reason.")
        if len(name) > 80:
            raise MastersError("A reason can be at most 80 characters.")
        if await ListEntry.filter(kind=entry.kind, name__iexact=name).exclude(id=entry.id).exists():
            raise MastersError(f"{name} is already on the list.")
        entry.name = name
    if effect is not None and entry.kind == "adjustment" and effect != entry.effect:
        if effect not in ("add", "remove"):
            raise MastersError("A reason either adds stock or takes it off.")
        if entry.builtin:
            raise MastersError(f"{entry.name} came with the software, so whether it adds or takes off stock can't change.")
        if uses:
            raise MastersError(f"{uses} adjustment(s) already use {entry.name}, so whether it adds or takes off stock can't change. Add a new reason instead.")
        entry.effect = effect
    if active is False and entry.active and entry.kind in ("adjustment", "purchase-return"):
        if not await ListEntry.filter(kind=entry.kind, active=True).exclude(id=entry.id).exists():
            raise MastersError(f"Keep at least one reason for {REASON_KINDS[entry.kind][3]} switched on.")
    if active is not None:
        entry.active = active
    entry.updated_by_name = user.name
    await entry.save()
    return entry, uses


async def delete_reason(entry_id: str) -> None:
    await ensure_reasons()
    entry = await ListEntry.get_or_none(id=entry_id)
    if not entry or entry.kind not in REASON_KINDS:
        raise MastersError("That reason doesn't exist.", 404)
    if entry.builtin:
        raise MastersError(f"{entry.name} came with the software and can't be deleted. Switch it off instead.")
    uses = (await _reason_uses(entry.kind)).get(entry.code, 0)
    if uses:
        raise MastersError(f"{uses} record(s) already use {entry.name}. Switch it off instead.")
    if entry.kind in ("adjustment", "purchase-return") and entry.active:
        if not await ListEntry.filter(kind=entry.kind, active=True).exclude(id=entry.id).exists():
            raise MastersError(f"Keep at least one reason for {REASON_KINDS[entry.kind][3]} switched on.")
    await entry.delete()


# ── Payment methods ─────────────────────────────────────────────────────────────────────────────

# The software's own order, and what each method asks for at the counter.
METHOD_ORDER = ("CASH", "CARD", "EASYPAISA", "JAZZCASH", "BANK", "CREDIT", "VOUCHER", "POINTS")
METHOD_RULES = {
    "cash": "Counted into the drawer. Change is given from it.",
    "card": "Needs the last 4 digits from the card machine's shop copy, and the customer's name and mobile number.",
    "wallet": "Needs the customer's wallet number and their name and mobile number. The transaction ID is optional.",
    "bank": "Needs the shop bank account it went into, the transaction ID and the customer's screenshot.",
    "credit": "Only for a customer allowed credit, up to their limit.",
    "gift-voucher": "Takes a gift voucher's balance.",
    "points": "A member's loyalty points, by the loyalty rules.",
}


async def payment_methods() -> list[PaymentMethod]:
    methods = await PaymentMethod.all()
    if methods and all(m.sort_order == 0 for m in methods):
        # First look since the order was kept: the software's order.
        for m in methods:
            m.sort_order = METHOD_ORDER.index(m.code) + 1 if m.code in METHOD_ORDER else 50
            await m.save(update_fields=["sort_order"])
    return sorted(methods, key=lambda m: (m.sort_order, m.code))


async def update_payment_method(code: str, name: str | None, active: bool | None, sort_order: int | None) -> PaymentMethod:
    method = await PaymentMethod.get_or_none(code=(code or "").upper())
    if not method:
        raise MastersError("That payment method doesn't exist.", 404)
    if name is not None:
        name = re.sub(r"\s+", " ", name.strip())
        if not name:
            raise MastersError("Type the name the counter and receipts show.")
        method.name = name[:60]
    if active is not None:
        if not active and method.code == "CASH":
            raise MastersError("Cash can't be switched off: the till, change and refunds all depend on it.")
        method.active = active
    if sort_order is not None:
        method.sort_order = sort_order
    await method.save()
    return method


async def refuse_switched_off_methods(codes) -> None:
    """A payment method switched off at this branch can't be taken, whatever a screen sends."""
    wanted = {str(c).upper() for c in codes if c}
    if not wanted:
        return
    off = await PaymentMethod.filter(code__in=list(wanted), active=False).first()
    if off:
        raise MastersError(f"{off.name} is switched off at this branch. Take the payment another way.")


async def bank_accounts() -> list[Account]:
    """The shop's bank accounts a transfer can go into: the bank accounts in the Chart of Accounts, which is also
    where the books match a transfer to."""
    return await Account.filter(kind="bank", active=True).order_by("name")


# ── Settings: what bills print, gift voucher rules ──────────────────────────────────────────────

RECEIPT_KEY = "receipt"
VOUCHER_KEY = "gift-vouchers"
RECEIPT_DEFAULTS = {
    "businessName": None, "ntn": None, "strn": None, "headerNote": None,
    "footerMessage": "Thank you for shopping with us", "returnPolicy": None,
}
VOUCHER_DEFAULTS = {"validityDays": 180, "minValue": "100", "maxValue": None}
# Wholesale bills take this much off the sale price of an Item without its own wholesale price; an Item without its
# own reorder level is low stock below this. 7% and 20 are what the software always used.
PRICING_STOCK_KEY = "pricing-stock"
PRICING_STOCK_DEFAULTS = {"wholesaleDiscountPercent": "7", "lowStockLevel": "20"}
_DEFAULTS = {RECEIPT_KEY: RECEIPT_DEFAULTS, VOUCHER_KEY: VOUCHER_DEFAULTS, PRICING_STOCK_KEY: PRICING_STOCK_DEFAULTS}


async def get_setting(key: str) -> tuple[dict, ShopSetting | None]:
    defaults = _DEFAULTS.get(key, VOUCHER_DEFAULTS)
    row = await ShopSetting.get_or_none(key=key)
    value = dict(defaults)
    if row and isinstance(row.value, dict):
        value.update({k: v for k, v in row.value.items() if k in defaults})
    return value, row


async def put_setting(key: str, value: dict, user: User) -> tuple[dict, ShopSetting]:
    row = await ShopSetting.get_or_none(key=key)
    now = datetime.now(timezone.utc)
    if row is None:
        row = await ShopSetting.create(key=key, value=value, updated_at=now, updated_by_name=user.name)
    else:
        row.value, row.updated_at, row.updated_by_name = value, now, user.name
        await row.save()
    merged, _ = await get_setting(key)
    return merged, row


async def voucher_rules() -> dict:
    value, _ = await get_setting(VOUCHER_KEY)
    return {
        "validityDays": int(value.get("validityDays") or 180),
        "minValue": Decimal(str(value.get("minValue") or "0")),
        "maxValue": Decimal(str(value["maxValue"])) if value.get("maxValue") not in (None, "") else None,
    }


async def pricing_stock() -> dict:
    """The wholesale discount % and the usual low stock level, as numbers."""
    value, _ = await get_setting(PRICING_STOCK_KEY)

    def number(key: str) -> Decimal:
        try:
            return Decimal(str(value.get(key)))
        except (ArithmeticError, ValueError):
            return Decimal(PRICING_STOCK_DEFAULTS[key])

    return {"wholesaleDiscountPercent": number("wholesaleDiscountPercent"), "lowStockLevel": number("lowStockLevel")}


async def low_stock_level() -> Decimal:
    """Below this an Item without its own reorder level is low stock."""
    return (await pricing_stock())["lowStockLevel"]
