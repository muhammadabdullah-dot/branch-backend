"""Invoice numbers: how this branch numbers its bills and returns (Branch Console > Lists and Settings > Receipt and
Vouchers > Invoice numbers).

A bill is {bill prefix}-{year}-{running number}, GG-2026-000001, and a return {return prefix}-{year}-{running number},
GG-RT-2026-000001. Out of the box that is exactly what the branch always printed. What a Branch Manager can set, kept in
shop_settings under "invoice-numbers" (no table of its own, so no migration):

- the bill prefix: the branch's code to begin with; letters, digits and dashes, up to 12 characters;
- whether the year is in the number, on to begin with. With it on each year is its own series and the running number
  starts again at 1 on 1 January (Pakistan time); with it off one series runs on for good (GG-000001);
- how many digits the running number has, 4 to 8, 6 to begin with;
- the next bill number, the real next one to begin with;
- the return prefix, {branch code}-RT to begin with. A return's year and digits follow the bill's.

The rules, all enforced here whatever a screen sends:

- The next bill number can be set higher, never at or below a number already used in its series: "GG-2026-000143 is
  already used. The next bill can't be lower than 000144."
- A new prefix starts its own series at the number given (1 unless one is typed). No two series can print the same
  number: every series ends in a dash and a running number is digits only, so GG-2026-000001 can only ever come from the
  series GG-2026- (the bill prefix GG with the year on, or GG-2026 with it off: the same series, one counter).
- The bill prefix and the return prefix differ, so a return never takes a bill's number; a prefix whose series the other
  kind of document already used is refused too, and a number is never handed out if the other kind already carries it.
- Both prefixes start with this branch's own code: exactly the code, or the code, a dash and more (GG, GG-A, GG-RT). A
  branch can't see what other branches have set, and each branch's code is its own at head office, so that is the only
  prefix no other branch can also print. A prefix that is, or starts with, another branch's code as head office last
  listed them (known_branches) is refused by name. Head office keys a branch's bills by the branch and the number (its
  sync inbox by branch and event, its snapshot rows and points history by branch), so it would not mix two branches'
  bills up; but the number is also what the customer, the receipt, FBR (the bill number is FBR's USIN) and head office's
  reports read, and one number printed by two branches would be two different bills to all of them.
- Bills and returns already made keep their numbers. Only the next ones change.

Each series has its own counter row, named for its prefix as printed ("invoice:GG-2026-", "sale_return:GG-RT-2026-"), and
numbering_service hands the numbers out: never one already used, never one twice. Before this setting the branch kept one
counter per kind ("invoice", "sale_return") that ran on through the years; the first bill or return after the upgrade
moves it onto the series it was numbering, so the next number is exactly the one the branch would have printed.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from tortoise.transactions import in_transaction

from app.core import logs
from app.core.pk_time import today_pk
from app.models import Counter, KnownBranch, ReturnRecord, SaleRecord, ShopSetting, User
from app.services import numbering_service

KEY = "invoice-numbers"
MIN_DIGITS, MAX_DIGITS, DEFAULT_DIGITS = 4, 8, 6
MAX_PREFIX = 12
# The number column holds 30 characters: a prefix, the year and 8 digits fit with room to spare.
MAX_PREFIX_WITH_CODE = 16
RETURN_MARK = "RT"
_SHAPE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")

# Bills and returns: the document, the column its number is in, the counter rows' name, and the counter the branch kept
# before this setting.
BILL, RETURN = "bill", "return"
_KINDS = {
    BILL: (SaleRecord, "invoice_number", "invoice", "invoice"),
    RETURN: (ReturnRecord, "number", "sale_return", "sale_return"),
}


class InvoiceNumbersError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Numbering:
    bill_prefix: str
    year_in_number: bool
    digits: int
    return_prefix: str

    def as_dict(self) -> dict:
        return {"billPrefix": self.bill_prefix, "yearInNumber": self.year_in_number, "digits": self.digits, "returnPrefix": self.return_prefix}


# The branch and its settings:

async def _branch_code() -> tuple[str, bool]:
    """This branch's code and whether head office has issued it. Before setup bills are numbered BR (sales_service)."""
    from app.services import registration_service
    from app.services.sales_service import UNVERIFIED_PREFIX

    identity = await registration_service.current()
    return (identity.code.strip().upper(), True) if identity else (UNVERIFIED_PREFIX, False)


def defaults(code: str) -> Numbering:
    return Numbering(code, True, DEFAULT_DIGITS, f"{code}-{RETURN_MARK}")


def _clean(prefix: str | None) -> str:
    return (prefix or "").strip().upper()


def _own(prefix: str, code: str) -> bool:
    return prefix == code or prefix.startswith(f"{code}-")


def _shape_problem(what: str, prefix: str, code: str) -> str | None:
    """What is wrong with a prefix on its own, or None."""
    if not prefix:
        return f"Enter the {what} prefix."
    if not _SHAPE.match(prefix):
        return f"The {what} prefix can have letters, digits and dashes, with no dash at either end and no two together, like {code} or {code}-{RETURN_MARK}."
    longest = MAX_PREFIX if prefix not in (code, f"{code}-{RETURN_MARK}") else MAX_PREFIX_WITH_CODE
    if len(prefix) > longest:
        return f"The {what} prefix can be up to {MAX_PREFIX} characters."
    if not _own(prefix, code):
        example = f"{code}-{RETURN_MARK}" if what == "return" else code
        return (
            f"The {what} prefix has to start with this branch's code, {code} (like {example}). Every branch's code is its "
            "own, so a number that starts with it can't be printed by another branch."
        )
    return None


async def _other_branch_problem(what: str, prefix: str, code: str) -> str | None:
    for branch in await KnownBranch.all():
        other = _clean(branch.code)
        if other and other != code and (prefix == other or prefix.startswith(f"{other}-")):
            return f"{other} is the code of branch {branch.name}, so a {what} prefix of {prefix} could print the same numbers as {branch.name}. Pick another."
    return None


async def _stored() -> tuple[dict | None, ShopSetting | None]:
    row = await ShopSetting.get_or_none(key=KEY)
    return (row.value if row and isinstance(row.value, dict) else None), row


async def effective() -> Numbering:
    """The numbering bills and returns get now: the saved setting, or the defaults. A saved setting that no longer fits
    this branch (its prefix isn't this branch's code any more, as on a database copied to another branch) is set aside
    for the defaults rather than print another branch's numbers."""
    code, registered = await _branch_code()
    base = defaults(code)
    value, _ = await _stored()
    if not value or not registered:
        return base
    bill, ret = _clean(value.get("billPrefix")), _clean(value.get("returnPrefix"))
    try:
        digits = int(value.get("digits") or DEFAULT_DIGITS)
    except (TypeError, ValueError):
        digits = DEFAULT_DIGITS
    if _shape_problem("bill", bill, code) or _shape_problem("return", ret, code) or bill == ret or not MIN_DIGITS <= digits <= MAX_DIGITS:
        logs.log.warning("invoice numbers: the saved setting %s doesn't fit branch %s; using the defaults", value, code)
        return base
    return Numbering(bill, bool(value.get("yearInNumber", True)), digits, ret)


def series_prefix(prefix: str, year_in_number: bool, year: int) -> str:
    """The part of every number in a series before its running number: GG-2026- with the year, GG- without it."""
    return f"{prefix}-{year}-" if year_in_number else f"{prefix}-"


def _series_id(kind: str, prefix: str) -> str:
    return f"{_KINDS[kind][2]}:{prefix}"


# Handing numbers out:

async def _adopt_legacy(kind: str, code: str, year: int) -> None:
    """The counter the branch kept before this setting ("invoice", "sale_return") numbered the default series; it moves
    onto that series' own row, keeping the higher value, and goes. Inside the caller's transaction."""
    legacy = await Counter.get_or_none(id=_KINDS[kind][3])
    if legacy is None:
        return
    base = defaults(code)
    target = _series_id(kind, series_prefix(base.bill_prefix if kind == BILL else base.return_prefix, True, year))
    row = await Counter.get_or_none(id=target)
    if row is None:
        await Counter.create(id=target, value=legacy.value)
    elif row.value < legacy.value:
        row.value = legacy.value
        await row.save(update_fields=["value"])
    await legacy.delete()


async def _peek(kind: str, prefix: str, code: str, year: int) -> int:
    """The running number the series `prefix` would hand out now. Before the old counter has moved (nothing has been
    numbered since the upgrade) it is read where it still is."""
    model, field, _, legacy_id = _KINDS[kind]
    series = _series_id(kind, prefix)
    base = defaults(code)
    if prefix == series_prefix(base.bill_prefix if kind == BILL else base.return_prefix, True, year) \
            and not await Counter.exists(id=series) and await Counter.exists(id=legacy_id):
        series = legacy_id
    return await numbering_service.peek_number(series, model, field, prefix)


async def _take(kind: str) -> str:
    """The next number for a bill or a return, taken. Inside the document's own transaction."""
    code, _ = await _branch_code()
    year = today_pk().year
    setting = await effective()
    prefix = series_prefix(setting.bill_prefix if kind == BILL else setting.return_prefix, setting.year_in_number, year)
    await _adopt_legacy(kind, code, year)
    model, field, _, _ = _KINDS[kind]
    other_model, other_field, _, _ = _KINDS[RETURN if kind == BILL else BILL]
    series = _series_id(kind, prefix)
    number = await numbering_service.next_number(series, model, field, prefix, setting.digits)
    for _attempt in range(3):
        if not await other_model.exists(**{other_field: number}):
            return number
        # The other kind already printed it (only possible after prefixes were swapped about over the years): go past
        # every number of theirs in this series.
        top = await numbering_service.highest(other_model, other_field, prefix)
        counter = await Counter.get(id=series)
        counter.value = max(counter.value, top + 1)
        await counter.save(update_fields=["value"])
        number = await numbering_service.next_number(series, model, field, prefix, setting.digits)
    return number


async def next_bill_number() -> str:
    return await _take(BILL)


async def next_return_number() -> str:
    return await _take(RETURN)


async def peek_bill_number() -> str:
    """The number the next bill will most likely get (another till may take it first)."""
    code, _ = await _branch_code()
    year = today_pk().year
    setting = await effective()
    prefix = series_prefix(setting.bill_prefix, setting.year_in_number, year)
    return f"{prefix}{await _peek(BILL, prefix, code, year):0{setting.digits}d}"


# Checking a setting:

async def _highest_written(model, field: str, prefix: str) -> tuple[int, str | None]:
    """The highest running number in the series `prefix` and the number as it was printed."""
    pattern = re.compile(re.escape(prefix) + r"(\d+)")
    top, written = 0, None
    for value in await model.filter(**{f"{field}__startswith": prefix}).values_list(field, flat=True):
        match = pattern.fullmatch(value or "")
        if match and int(match.group(1)) >= top:
            top, written = int(match.group(1)), value
    return top, written


async def assess(bill_prefix: str | None, year_in_number: bool, digits: int | None, return_prefix: str | None,
                 next_number: int | None) -> dict:
    """What a proposed setting would do and what is wrong with it: the next bill and return numbers, the lowest next bill
    number its series allows, the problems (each one refuses a save, the first is the one said) and notes."""
    code, registered = await _branch_code()
    year = today_pk().year
    bill, ret = _clean(bill_prefix), _clean(return_prefix)
    problems: list[str] = []
    notes: list[str] = []
    if not registered:
        problems.append("This branch isn't set up with head office yet. Its numbers start with the code head office gives it, so they can be set once it is.")
    for what, prefix in (("bill", bill), ("return", ret)):
        problem = _shape_problem(what, prefix, code) or await _other_branch_problem(what, prefix, code)
        if problem:
            problems.append(problem)
    if bill and bill == ret:
        problems.append("The return prefix has to differ from the bill prefix, so a return can never take a bill's number.")
    width = digits if isinstance(digits, int) else 0
    if not MIN_DIGITS <= width <= MAX_DIGITS:
        problems.append(f"The running number has {MIN_DIGITS} to {MAX_DIGITS} digits.")
        width = DEFAULT_DIGITS
    shapes_ok = not any(_shape_problem(w, p, code) for w, p in (("bill", bill), ("return", ret)))
    out = {"nextBill": None, "nextReturn": None, "nextNumber": None, "lowestNext": 1, "nextReturnNumber": None, "year": year}
    if not shapes_ok:
        return {**out, "problems": problems, "notes": notes}

    bill_series = series_prefix(bill, year_in_number, year)
    return_series = series_prefix(ret, year_in_number, year)
    top, top_written = await _highest_written(SaleRecord, "invoice_number", bill_series)
    suggested = await _peek(BILL, bill_series, code, year)
    number = next_number if next_number is not None else suggested
    if next_number is not None and next_number < 1:
        problems.append("The next bill number is 1 or more.")
    elif next_number is not None and next_number <= top:
        problems.append(f"{top_written} is already used. The next bill can't be lower than {top + 1:0{width}d}.")
    if number >= 10 ** width:
        problems.append(f"With {width} digits the highest bill number is {'9' * width}. Pick more digits.")
    return_next = await _peek(RETURN, return_series, code, year)
    if return_next >= 10 ** width:
        problems.append(f"Returns in this series are already past {'9' * width}, so the running number needs more than {width} digits.")
    # Neither kind may take the other's series: a return numbered like a bill, or a bill like a return.
    if bill_series != return_series:
        used_by_returns, example = await _highest_written(ReturnRecord, "number", bill_series)
        if used_by_returns:
            problems.append(f"Returns already carry numbers like {example}, so bills can't be numbered {bill_series}... Pick another bill prefix.")
        used_by_bills, example = await _highest_written(SaleRecord, "invoice_number", return_series)
        if used_by_bills:
            problems.append(f"Bills already carry numbers like {example}, so returns can't be numbered {return_series}... Pick another return prefix.")
    if next_number is not None and next_number > max(top, suggested - 1) + 1 and next_number < 10 ** width:
        skipped_from = max(top + 1, suggested)
        notes.append(f"Numbers {skipped_from:0{width}d} to {next_number - 1:0{width}d} will be skipped: no bill will carry them.")
    if year_in_number:
        notes.append(f"The running number starts again at 1 on 1 January {year + 1}.")
    return {
        **out,
        "nextBill": f"{bill_series}{max(number, 1):0{width}d}", "nextReturn": f"{return_series}{return_next:0{width}d}",
        "nextNumber": max(number, 1), "lowestNext": top + 1, "nextReturnNumber": return_next,
        "problems": problems, "notes": notes,
    }


async def settings_out(viewer: User | None) -> dict:
    from app.core.abilities import BRANCH_MANAGER

    code, registered = await _branch_code()
    setting = await effective()
    _, row = await _stored()
    checked = await assess(setting.bill_prefix, setting.year_in_number, setting.digits, setting.return_prefix, None)
    return {
        **setting.as_dict(),
        "branchCode": code, "registered": registered, "year": checked["year"],
        "nextNumber": checked["nextNumber"] or 1, "lowestNext": checked["lowestNext"],
        "nextBill": checked["nextBill"] or "", "nextReturn": checked["nextReturn"] or "",
        "nextReturnNumber": checked["nextReturnNumber"] or 1,
        "defaults": defaults(code).as_dict(),
        "saved": row is not None,
        "updatedAt": row.updated_at if row else None, "updatedBy": row.updated_by_name if row else None,
        "canChange": bool(viewer and viewer.role_id == BRANCH_MANAGER and registered),
    }


async def save(user: User, data) -> tuple[dict, str]:
    """Saves the setting and, when a next bill number is given, moves the bill series' counter to it. Returns the
    setting as the card sees it and a note of what changed for the activity trail."""
    from app.core.abilities import BRANCH_MANAGER

    if user.role_id != BRANCH_MANAGER:
        raise InvoiceNumbersError("Only a Branch Manager can change the invoice numbers.", 403)
    code, _ = await _branch_code()
    year = today_pk().year
    async with in_transaction():
        # The old counters move first, so what is checked below is where the series really stand.
        await _adopt_legacy(BILL, code, year)
        await _adopt_legacy(RETURN, code, year)
        before = await effective()
        checked = await assess(data.billPrefix, bool(data.yearInNumber), data.digits, data.returnPrefix, data.nextNumber)
        if checked["problems"]:
            raise InvoiceNumbersError(checked["problems"][0])
        after = Numbering(_clean(data.billPrefix), bool(data.yearInNumber), int(data.digits), _clean(data.returnPrefix))
        now = datetime.now(timezone.utc)
        row = await ShopSetting.get_or_none(key=KEY)
        if row is None:
            await ShopSetting.create(key=KEY, value=after.as_dict(), updated_at=now, updated_by_name=user.name)
        else:
            row.value, row.updated_at, row.updated_by_name = after.as_dict(), now, user.name
            await row.save()
        if data.nextNumber is not None:
            series = _series_id(BILL, series_prefix(after.bill_prefix, after.year_in_number, year))
            counter = await Counter.get_or_none(id=series)
            if counter is None:
                await Counter.create(id=series, value=data.nextNumber)
            else:
                counter.value = data.nextNumber
                await counter.save(update_fields=["value"])
    out = await settings_out(user)

    changes = []
    for label, key in (("bill prefix", "billPrefix"), ("return prefix", "returnPrefix"), ("digits", "digits")):
        was, now_value = before.as_dict()[key], after.as_dict()[key]
        changes.append(f"{label} {now_value}" + (f" (was {was})" if was != now_value else ""))
    year_text = "year in the number " + ("on" if after.year_in_number else "off")
    changes.append(year_text + (" (was " + ("on" if before.year_in_number else "off") + ")" if before.year_in_number != after.year_in_number else ""))
    changes.append(f"next bill {out['nextBill']}, next return {out['nextReturn']}")
    return out, "Invoice numbers: " + "; ".join(changes)
