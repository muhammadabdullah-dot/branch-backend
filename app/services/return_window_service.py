"""How long after a sale an Item can be brought back, set per department by the Branch Manager.

Kept as one shop setting (Lists and Settings > Receipt and Vouchers > Return window): the number of days for each
department, by the department's entry on the Item lists. Keying by the entry rather than its name means renaming a
department keeps its window. A department with no number set takes returns at any time; 0 means it takes none.

The owner's rule: after the window the return is refused with a plain message naming the department and the days.
Nobody overrides it at the counter; the Branch Manager changes the window if the shop decides otherwise.

Checked on Process Return (a refund, a replace or an exchange) and wherever else goods come back against a bill:
`refuse_outside_window` is the one check to call.
"""
from datetime import datetime, timezone

from app.core.pk_time import pk_day, today_pk
from app.models import ListEntry, Product, ShopSetting, User

KEY = "return-windows"
MAX_DAYS = 3650


class ReturnWindowError(Exception):
    def __init__(self, message: str):
        self.message = message


async def _stored() -> tuple[dict[str, int], ShopSetting | None]:
    row = await ShopSetting.get_or_none(key=KEY)
    days: dict[str, int] = {}
    if row and isinstance(row.value, dict) and isinstance(row.value.get("days"), dict):
        for entry_id, value in row.value["days"].items():
            try:
                days[str(entry_id)] = int(value)
            except (TypeError, ValueError):
                continue
    return days, row


async def windows() -> dict:
    """Every department on the Item lists with its window (None: no limit), for the settings card."""
    from app.services import masters_service

    await masters_service.sync_item_list("department")
    days, row = await _stored()
    rows = []
    for entry in await ListEntry.filter(kind="department").order_by("name"):
        rows.append({"id": str(entry.id), "name": entry.name, "active": entry.active, "days": days.get(str(entry.id))})
    return {
        "departments": rows,
        "updatedAt": row.updated_at if row else None,
        "updatedBy": row.updated_by_name if row else None,
    }


async def save_windows(values: dict[str, int | None], user: User) -> dict:
    """Replaces the windows. A department left out or blank takes returns at any time."""
    known = {str(i) for i in await ListEntry.filter(kind="department").values_list("id", flat=True)}
    days: dict[str, int] = {}
    for entry_id, value in (values or {}).items():
        if value is None or value == "":
            continue
        if str(entry_id) not in known:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ReturnWindowError("Days are a whole number, like 7. Leave it blank for no limit.")
        if number < 0 or number > MAX_DAYS:
            raise ReturnWindowError(f"Days are from 0 (no returns) to {MAX_DAYS}. Leave it blank for no limit.")
        days[str(entry_id)] = number
    now = datetime.now(timezone.utc)
    row = await ShopSetting.get_or_none(key=KEY)
    if row is None:
        await ShopSetting.create(key=KEY, value={"days": days}, updated_at=now, updated_by_name=user.name)
    else:
        row.value, row.updated_at, row.updated_by_name = {"days": days}, now, user.name
        await row.save()
    return await windows()


async def window_for_departments(names: set[str]) -> dict[str, int]:
    """The window of each department name that has one."""
    names = {n for n in names if n}
    if not names:
        return {}
    days, _ = await _stored()
    if not days:
        return {}
    out: dict[str, int] = {}
    for entry in await ListEntry.filter(kind="department", code__in=list(names)):
        if str(entry.id) in days:
            out[entry.code] = days[str(entry.id)]
    return out


def days_since(sale_at: datetime) -> int:
    """Whole Pakistan days from the day of the sale to today: 0 on the day itself."""
    return (today_pk() - pk_day(sale_at)).days


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


async def refuse_outside_window(sale_at: datetime, products: list[Product], invoice_number: str | None = None) -> None:
    """Raises ReturnWindowError when any of these Items is past its department's return window for a sale made at
    `sale_at`. Items without a department, or whose department has no window, can always come back."""
    by_department = await window_for_departments({p.department for p in products if p.department})
    if not by_department:
        return
    age = days_since(sale_at)
    bill = f"{invoice_number} was" if invoice_number else "The bill was"
    for product in products:
        limit = by_department.get(product.department or "")
        if limit is None:
            continue
        if limit == 0:
            raise ReturnWindowError(
                f"{product.name} can't be taken back: the Branch Manager has set no returns for the {product.department} department."
            )
        if age > limit:
            ago = "today" if age == 0 else f"{_plural(age, 'day')} ago"
            raise ReturnWindowError(
                f"{product.name} can't be taken back: {product.department} Items are returned within "
                f"{_plural(limit, 'day')} of the sale, and {bill} made {ago}."
            )
