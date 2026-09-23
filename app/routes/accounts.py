"""The branch's books over HTTP: chart, vouchers, reports, customer payments, cheques and the month-end controls."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.middlewares.auth import get_current_user, require_any_permission, require_permission
from app.models import Account, User, Voucher
from app.services import (
    accounts_areas,
    accounts_chart_service,
    accounts_money_service,
    accounts_posting_service,
    accounts_reports_service,
    vouchers_service,
)
from app.services.accounts_areas import access_of
from app.services.accounts_reports_service import shop_day
from app.services.rbac_service import has_permission

Day = date

router = APIRouter(prefix="/accounts", tags=["accounts"])

# A resource per screen and action (core/abilities.py). Whole-book reports need only their own tick; the ledger, the
# chart and vouchers are also limited to the accounts in the person's areas (services/accounts_areas.py).
_desk = require_permission("accounts.desk", "R")
_post_now = require_permission("accounts.desk", "X")
_trial_balance = require_permission("accounts.trial-balance", "R")
_income_statement = require_permission("accounts.income-statement", "R")
_balance_sheet = require_permission("accounts.balance-sheet", "R")
_month_by_month = require_permission("accounts.month-by-month", "R")
_day_book = require_permission("accounts.day-book", "R")
_ledger = require_permission("accounts.ledger", "R")
_vouchers = require_permission("accounts.vouchers", "R")
_voucher_open = require_any_permission(("accounts.vouchers", "R"), ("accounts.day-book", "R"))
# Which of the two a voucher needs depends on its type: the opening balances have their own tick.
_write = require_any_permission(("accounts.vouchers", "W"), ("accounts.opening-balances", "W"))
_post = require_permission("accounts.vouchers.post", "X")
_reverse = require_permission("accounts.vouchers.reverse", "X")
_opening = require_permission("accounts.opening-balances", "W")
_chart = require_permission("accounts.chart", "W")
_settings_read = require_permission("accounts.settings", "R")
_settings = require_permission("accounts.settings", "W")
_period = require_permission("accounts.period", "X")
_receivables = require_permission("accounts.receivables", "R")
_receive = require_permission("accounts.receivables", "W")
_void_payment = require_permission("accounts.receivables", "X")
_statement = require_any_permission(("accounts.receivables", "R"), ("accounts.payables", "R"), ("accounts.ledger", "R"))
_cheques_read = require_permission("accounts.cheques", "R")
_cheques = require_permission("accounts.cheques", "W")
# Every screen that picks an account reads the chart.
CHART_READERS = (
    ("accounts.chart", "R"), ("accounts.ledger", "R"), ("accounts.vouchers", "R"), ("accounts.opening-balances", "R"),
    ("accounts.cheques", "R"), ("accounts.settings", "R"), ("accounts.receivables", "R"),
)
# The till names the account a cash in or cash out is for.
_chart_read = require_any_permission(*CHART_READERS, ("store.till", "W"))
_lookup = require_any_permission(("accounts.chart", "R"), ("accounts.vouchers", "W"), ("accounts.opening-balances", "W"), ("store.till", "W"))

ERRORS = (
    vouchers_service.VoucherError, accounts_chart_service.ChartError, accounts_money_service.MoneyError,
)


async def _guard(call: Callable, *args, **kwargs):
    try:
        return await call(*args, **kwargs)
    except accounts_areas.AreaRefused as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.message)
    except ERRORS as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


async def _till_only(user: User) -> bool:
    """Someone reaching the chart only because they work the till: they name an account on a cash in or out, whatever
    it is, and have no accounts screen to limit."""
    for resource, action in CHART_READERS:
        if await has_permission(user, resource, action):
            return False
    return True


async def _may_write(user: User, vtype: str | None) -> None:
    if (vtype or "").upper() == "OB":
        if not await has_permission(user, "accounts.opening-balances", "W"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Writing the opening balances needs its own access. Ask your manager for it.")
    elif not await has_permission(user, "accounts.vouchers", "W"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Writing vouchers needs its own access. Ask your manager for it.")


def _range(from_: date | None, to: date | None, default_days: int = 0) -> tuple[date | None, date]:
    end = to or shop_day()
    start = from_ if from_ else (end - timedelta(days=default_days) if default_days else None)
    if start and start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The start date is after the end date.")
    return start, end


# ── chart ──────────────────────────────────────────────────────────────────────────────────────

class GroupIn(BaseModel):
    categoryCode: str | None = None
    name: str | None = None
    priority: int | None = None
    manualCode: str | None = None


class SubGroupIn(BaseModel):
    groupCode: str | None = None
    name: str


class AccountIn(BaseModel):
    groupCode: str | None = None
    subGroupCode: str | None = None
    name: str | None = None
    kind: str | None = None
    active: bool | None = None
    restricted: bool | None = None
    checkLimit: bool | None = None
    balanceLimit: Decimal | None = None
    bankName: str | None = None
    bankAccountNo: str | None = None
    manualCode: str | None = None
    remarks: str | None = None


def _account_fields(payload: AccountIn) -> dict:
    data = payload.model_dump(exclude_unset=True)
    mapping = {"groupCode": "group_code", "subGroupCode": "sub_group_code", "checkLimit": "check_limit", "balanceLimit": "balance_limit",
               "bankName": "bank_name", "bankAccountNo": "bank_account_no", "manualCode": "manual_code"}
    return {mapping.get(k, k): v for k, v in data.items()}


@router.get("/chart")
async def chart(user: User = Depends(_chart_read)) -> dict:
    """The chart, as far as the person's areas go."""
    await accounts_chart_service.ensure_party_accounts()
    access = accounts_areas.EVERYTHING if await _till_only(user) else await access_of(user)
    return await accounts_chart_service.tree(access)


@router.get("/lookup")
async def lookup(q: str | None = None, kinds: str | None = None, limit: int = 30, user: User = Depends(_lookup)) -> list[dict]:
    """Accounts to pick in a voucher line (those the person may use) or a till Cash Out (any), by code or name."""
    qs = Account.filter(active=True)
    if not await has_permission(user, "store.till", "W"):
        access = await access_of(user)
        if not access.uses_everything:
            areas = await accounts_areas.areas_by_account()
            qs = qs.filter(id__in=[i for i, (area, key) in areas.items() if access.can_use(area, key)])
    if kinds:
        qs = qs.filter(kind__in=[k.strip() for k in kinds.split(",") if k.strip()])
    if q and q.strip():
        from tortoise.expressions import Q

        term = q.strip()
        qs = qs.filter(Q(name__icontains=term) | Q(code__startswith=term) | Q(manual_code__icontains=term))
    rows = await qs.order_by("code").limit(min(max(limit, 1), 200)).prefetch_related("group")
    return [{"id": str(a.id), "code": a.code, "name": a.name, "kind": a.kind, "groupCode": a.group_id, "groupName": a.group.name,
             "restricted": a.restricted, "systemKey": a.system_key} for a in rows]


@router.post("/groups")
async def create_group(payload: GroupIn, user: User = Depends(_chart)) -> dict:
    await _guard(accounts_areas.check_group_change, await access_of(user), category_code=payload.categoryCode)
    group = await _guard(accounts_chart_service.create_group, payload.categoryCode or "", payload.name or "", payload.priority or 0, payload.manualCode)
    return accounts_chart_service.group_payload(group)


@router.patch("/groups/{code}")
async def update_group(code: str, payload: GroupIn, user: User = Depends(_chart)) -> dict:
    await _guard(accounts_areas.check_group_change, await access_of(user), group_code=code)
    group = await _guard(accounts_chart_service.update_group, code, payload.name, payload.priority, payload.manualCode)
    return accounts_chart_service.group_payload(group)


@router.post("/sub-groups")
async def create_sub_group(payload: SubGroupIn, user: User = Depends(_chart)) -> dict:
    await _guard(accounts_areas.check_group_change, await access_of(user), group_code=payload.groupCode)
    sub = await _guard(accounts_chart_service.create_sub_group, payload.groupCode or "", payload.name)
    return {"code": sub.code, "name": sub.name, "groupCode": sub.group_id}


@router.patch("/sub-groups/{code}")
async def update_sub_group(code: str, payload: SubGroupIn, user: User = Depends(_chart)) -> dict:
    await _guard(accounts_areas.check_group_change, await access_of(user), sub_group_code=code)
    sub = await _guard(accounts_chart_service.update_sub_group, code, payload.name)
    return {"code": sub.code, "name": sub.name, "groupCode": sub.group_id}


@router.delete("/groups/{code}")
async def delete_group(code: str, user: User = Depends(_chart)) -> dict:
    await _guard(accounts_areas.check_group_change, await access_of(user), group_code=code)
    await _guard(accounts_chart_service.delete_group, code)
    return {"deleted": True}


@router.delete("/sub-groups/{code}")
async def delete_sub_group(code: str, user: User = Depends(_chart)) -> dict:
    await _guard(accounts_areas.check_group_change, await access_of(user), sub_group_code=code)
    await _guard(accounts_chart_service.delete_sub_group, code)
    return {"deleted": True}


@router.post("/accounts")
async def create_account(payload: AccountIn, user: User = Depends(_chart)) -> dict:
    await _guard(accounts_areas.check_account_change, await access_of(user), None, group_code=payload.groupCode, kind=payload.kind)
    account = await _guard(accounts_chart_service.create_account, _account_fields(payload))
    return await accounts_chart_service.account_payload(account)


@router.patch("/accounts/{account_id}")
async def update_account(account_id: str, payload: AccountIn, user: User = Depends(_chart)) -> dict:
    await _guard(accounts_areas.check_account_change, await access_of(user), await Account.get_or_none(id=account_id),
                 group_code=payload.groupCode, kind=payload.kind)
    account = await _guard(accounts_chart_service.update_account, account_id, _account_fields(payload))
    return await accounts_chart_service.account_payload(account)


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, user: User = Depends(_chart)) -> dict:
    await _guard(accounts_areas.check_account_change, await access_of(user), await Account.get_or_none(id=account_id))
    await _guard(accounts_chart_service.delete_account, account_id)
    return {"deleted": True}


# ── vouchers ───────────────────────────────────────────────────────────────────────────────────

class VoucherLineIn(BaseModel):
    accountId: str | None = None
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str | None = None
    referenceNo: str | None = None


class VoucherIn(BaseModel):
    vtype: str
    date: Day | None = None
    headerAccountId: str | None = None
    referenceNo: str | None = None
    description: str | None = None
    chequeNo: str | None = None
    chequeDate: Day | None = None
    lines: list[VoucherLineIn] = []
    # Save and post in one go, for someone who may post.
    post: bool = False


class ReasonIn(BaseModel):
    reason: str | None = None
    date: Day | None = None


@router.get("/vouchers")
async def list_vouchers(
    type: str | None = None, status_: str | None = Query(None, alias="status"), auto: bool | None = None,
    from_: date | None = Query(None, alias="from"), to: date | None = None, q: str | None = None, accountId: str | None = None,
    accountKind: str | None = None, limit: int = 50, offset: int = 0, sort: str | None = None, order: str | None = None,
    user: User = Depends(_vouchers),
) -> dict:
    """Only vouchers whose every line is in the person's areas. The Day Book is the whole book. `sort`: date, number,
    type, description, amount, status or by; `order`: asc or desc. Without one, newest first."""
    await accounts_posting_service.ensure_recent()
    items, total = await vouchers_service.list_vouchers(type, status_, auto, from_, to, q, accountId, min(max(limit, 1), 500), max(offset, 0),
                                                        access=await access_of(user), account_kind=accountKind, sort=sort, order=order)
    return {"items": items, "total": total}


@router.get("/vouchers/{voucher_id}")
async def get_voucher(voucher_id: str, user: User = Depends(_voucher_open)) -> dict:
    voucher = await _guard(vouchers_service.get, voucher_id)
    # The Day Book already shows every voucher in full, so opening one from it isn't limited to the reader's areas.
    if not await has_permission(user, "accounts.day-book", "R"):
        await _guard(vouchers_service.check_seen, await access_of(user), voucher, "open")
    return await vouchers_service.voucher_out(voucher, with_source=True)


async def _can_post(user: User) -> bool:
    return await has_permission(user, "accounts.vouchers.post", "X")


@router.post("/vouchers")
async def create_voucher(payload: VoucherIn, user: User = Depends(_write)) -> dict:
    await _may_write(user, payload.vtype)
    if payload.post and not await _can_post(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can save vouchers but not post them. Save it, and someone who posts will.")
    access = await access_of(user)
    data = payload.model_dump(mode="json")
    voucher = await _guard(vouchers_service.create_draft, user, data, access)
    if payload.post:
        voucher = await _guard(vouchers_service.post, user, str(voucher.id), access)
    return await vouchers_service.voucher_out(voucher)


@router.put("/vouchers/{voucher_id}")
async def update_voucher(voucher_id: str, payload: VoucherIn, user: User = Depends(_write)) -> dict:
    existing = await Voucher.get_or_none(id=voucher_id)
    await _may_write(user, existing.vtype if existing else payload.vtype)
    if payload.post and not await _can_post(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can save vouchers but not post them.")
    access = await access_of(user)
    voucher = await _guard(vouchers_service.update_draft, user, voucher_id, payload.model_dump(mode="json"), access)
    if payload.post:
        voucher = await _guard(vouchers_service.post, user, voucher_id, access)
    return await vouchers_service.voucher_out(voucher)


@router.post("/vouchers/{voucher_id}/post")
async def post_voucher(voucher_id: str, user: User = Depends(_post)) -> dict:
    return await vouchers_service.voucher_out(await _guard(vouchers_service.post, user, voucher_id, await access_of(user)))


@router.post("/vouchers/{voucher_id}/cancel")
async def cancel_voucher(voucher_id: str, payload: ReasonIn, user: User = Depends(_write)) -> dict:
    existing = await Voucher.get_or_none(id=voucher_id)
    await _may_write(user, existing.vtype if existing else None)
    return await vouchers_service.voucher_out(await _guard(vouchers_service.cancel_draft, user, voucher_id, payload.reason, await access_of(user)))


@router.post("/vouchers/{voucher_id}/reverse")
async def reverse_voucher(voucher_id: str, payload: ReasonIn, user: User = Depends(_reverse)) -> dict:
    reversal = await _guard(vouchers_service.reverse, user, voucher_id, payload.date, payload.reason or "", await access_of(user))
    return await vouchers_service.voucher_out(reversal)


# ── reports ────────────────────────────────────────────────────────────────────────────────────

@router.get("/ledger")
async def ledger(accountId: str, from_: date | None = Query(None, alias="from"), to: date | None = None, pdc: bool = False, user: User = Depends(_ledger)) -> dict:
    account = await Account.get_or_none(id=accountId)
    if account:
        await _guard(accounts_areas.check_ledger, await access_of(user), account)
    await accounts_posting_service.ensure_recent()
    start, end = _range(from_, to)
    return await _guard(accounts_reports_service.ledger, accountId, start, end, pdc)


@router.get("/trial-balance")
async def trial_balance(from_: date | None = Query(None, alias="from"), to: date | None = None, group: str | None = None, zero: bool = False,
                        category: str | None = None, user: User = Depends(_trial_balance)) -> dict:
    """`group` and `category` each take one code or several separated by commas."""
    await accounts_posting_service.ensure_recent()
    start, end = _range(from_, to)
    return await accounts_reports_service.trial_balance(start, end, group, zero, category)


@router.get("/income-statement")
async def income_statement(from_: date | None = Query(None, alias="from"), to: date | None = None, user: User = Depends(_income_statement)) -> dict:
    await accounts_posting_service.ensure_recent()
    end = to or shop_day()
    start = from_ or end.replace(day=1)
    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The start date is after the end date.")
    return await accounts_reports_service.income_statement(start, end)


@router.get("/month-by-month")
async def month_by_month(from_: date | None = Query(None, alias="from"), to: date | None = None, user: User = Depends(_month_by_month)) -> dict:
    await accounts_posting_service.ensure_recent()
    end = to or shop_day()
    start = from_ or (await vouchers_service.settings()).books_start or end.replace(day=1)
    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The start date is after the end date.")
    if (end.year - start.year) * 12 + end.month - start.month >= 24:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick up to 24 months.")
    return await accounts_reports_service.month_by_month(start, end)


@router.get("/balance-sheet")
async def balance_sheet(asOf: date | None = None, user: User = Depends(_balance_sheet)) -> dict:
    await accounts_posting_service.ensure_recent()
    settings = await vouchers_service.settings()
    return await accounts_reports_service.balance_sheet(asOf or shop_day(), settings.fiscal_start_month)


@router.get("/day-book")
async def day_book(from_: date | None = Query(None, alias="from"), to: date | None = None, type: str | None = None, limit: int = 100, offset: int = 0, user: User = Depends(_day_book)) -> dict:
    await accounts_posting_service.ensure_recent()
    end = to or shop_day()
    start = from_ or end
    return await accounts_reports_service.day_book(start, end, type, min(max(limit, 1), 500), max(offset, 0))


@router.get("/statement")
async def statement(accountId: str, from_: date | None = Query(None, alias="from"), to: date | None = None, user: User = Depends(_statement)) -> dict:
    """A customer's or supplier's statement of account: Receivables or Payables (or the ledger), and the party's area."""
    account = await Account.get_or_none(id=accountId)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That account doesn't exist.")
    screen = "accounts.receivables" if account.kind == "customer" else "accounts.payables"
    if not (await has_permission(user, screen, "R") or await has_permission(user, "accounts.ledger", "R")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A statement for this party needs access to " + ("Receivables." if account.kind == "customer" else "Payables."))
    await _guard(accounts_areas.check_ledger, await access_of(user), account)
    await accounts_posting_service.ensure_recent()
    start, end = _range(from_, to)
    return await _guard(accounts_reports_service.statement, accountId, start, end)


@router.get("/ageing")
async def ageing(kind: str = "customer", asOf: date | None = None, user: User = Depends(get_current_user)) -> dict:
    if kind == "supplier" and not await has_permission(user, "accounts.payables", "R"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Seeing what suppliers are owed needs access to Payables.")
    if kind != "supplier" and not await has_permission(user, "accounts.receivables", "R"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Seeing what customers owe needs access to Receivables.")
    await accounts_posting_service.ensure_recent()
    return await _guard(accounts_reports_service.ageing, kind, asOf or shop_day())


@router.get("/dashboard")
async def dashboard(user: User = Depends(_desk)) -> dict:
    await accounts_posting_service.ensure_recent()
    return await accounts_reports_service.dashboard()


# ── settings, month end, posting ───────────────────────────────────────────────────────────────

class SettingsIn(BaseModel):
    fiscalStartMonth: int | None = None
    booksStart: Day | None = None
    tenderAccounts: dict[str, str | None] | None = None


class CloseIn(BaseModel):
    until: Day | None = None
    reason: str | None = None


def _settings_out(row) -> dict:
    return {
        "fiscalStartMonth": row.fiscal_start_month, "booksStart": row.books_start.isoformat() if row.books_start else None,
        "lockedUntil": row.locked_until.isoformat() if row.locked_until else None, "tenderAccounts": row.tender_accounts or {},
        "lastPostingAt": row.last_posting_at.isoformat() if row.last_posting_at else None, "lastPostingNote": row.last_posting_note,
        "postingProblems": row.posting_problems or [], "tenderDefaults": accounts_chart_service.TENDER_KEYS,
    }


@router.get("/settings")
async def get_settings(user: User = Depends(_settings_read)) -> dict:
    return _settings_out(await vouchers_service.settings())


@router.patch("/settings")
async def update_settings(payload: SettingsIn, user: User = Depends(_settings)) -> dict:
    row = await vouchers_service.settings()
    repost = False
    if payload.fiscalStartMonth is not None:
        if not 1 <= payload.fiscalStartMonth <= 12:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The financial year starts in a month from 1 to 12.")
        row.fiscal_start_month = payload.fiscalStartMonth
    if payload.booksStart is not None and payload.booksStart != row.books_start:
        if row.locked_until:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Months are closed. Reopen them before moving the books' start.")
        if payload.booksStart > shop_day():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The books can't start in the future.")
        row.books_start = payload.booksStart
        repost = True
    if payload.tenderAccounts is not None:
        cleaned = {}
        for code, account_id in payload.tenderAccounts.items():
            if not account_id:
                continue
            account = await Account.get_or_none(id=account_id)
            if not account or account.kind not in ("cash", "bank", "wallet") and code not in ("VOUCHER", "POINTS"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{code} money has to land in a cash, bank or wallet account.")
            cleaned[code.upper()] = account_id
        if cleaned != (row.tender_accounts or {}):
            row.tender_accounts = cleaned
            repost = True
    await row.save()
    if repost:
        await accounts_posting_service.run(full=True)
    else:
        await vouchers_service.emit_settings()
    return _settings_out(await vouchers_service.settings())


@router.post("/close-month")
async def close_month(payload: CloseIn, user: User = Depends(_period)) -> dict:
    row = await vouchers_service.settings()
    until = payload.until
    if until is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick the last day to close.")
    if until >= shop_day():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only days that are over can be closed.")
    if row.locked_until and until <= row.locked_until:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The books are already closed up to {row.locked_until:%d %b %Y}.")
    drafts = await Voucher.filter(status="draft", date__lte=until).count()
    if drafts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{drafts} draft voucher{'s are' if drafts != 1 else ' is'} dated in that period. Post or cancel them first.")
    await accounts_posting_service.run(full=False)
    row = await vouchers_service.settings()
    row.locked_until = until
    await row.save()
    await vouchers_service.emit_settings(row)
    await _record_period(user, f"Closed the books up to {until:%d %b %Y}")
    return _settings_out(row)


@router.post("/reopen")
async def reopen(payload: CloseIn, user: User = Depends(_period)) -> dict:
    row = await vouchers_service.settings()
    if not row.locked_until:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No month is closed.")
    if not (payload.reason or "").strip() or len(payload.reason.strip()) < 10:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Say why the books are being reopened. It stays on the record.")
    until = payload.until
    if until is not None and until >= row.locked_until:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reopening keeps the books closed up to an earlier day, or none.")
    old = row.locked_until
    row.locked_until = until
    await row.save()
    await _record_period(user, f"Reopened the books (were closed to {old:%d %b %Y}, now {'open' if until is None else 'closed to ' + until.strftime('%d %b %Y')}): {payload.reason.strip()}")
    await accounts_posting_service.run(full=True)
    return _settings_out(await vouchers_service.settings())


@router.get("/period-history")
async def period_history(user: User = Depends(_settings_read)) -> list[dict]:
    """Every close and reopen, newest first, from the notices sent when they happened."""
    from app.models import Notice

    rows = await Notice.filter(kind="accounts.period").order_by("-at").limit(200)
    return [{"at": n.at.isoformat(), "what": n.title, "by": (n.body or "").removeprefix("By ") or None,
             "reopened": n.title.startswith("Reopened")} for n in rows]


async def _record_period(user: User, text: str) -> None:
    from app.services import alerts_service

    await alerts_service.notify("accounts.period", text, body=f"By {user.name}", link="/accounts/settings",
                                audience_any=[("accounts.period", "X"), ("accounts.settings", "R")], tone="warning")


class RunIn(BaseModel):
    full: bool = False


@router.post("/posting/run")
async def run_posting(payload: RunIn, user: User = Depends(_post_now)) -> dict:
    if payload.full:
        if not await has_permission(user, "accounts.period", "X"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Re-posting everything needs the right to close months.")
    return await accounts_posting_service.run(full=payload.full)


@router.get("/opening-suggestion")
async def opening_suggestion(user: User = Depends(_opening)) -> dict:
    """Only lines for accounts the person may use; the rest is theirs to leave to someone who can."""
    suggestion = await accounts_posting_service.opening_suggestion()
    access = await access_of(user)
    if not access.uses_everything:
        areas = await accounts_areas.areas_by_account()
        suggestion["lines"] = [line for line in suggestion["lines"] if access.can_use(*areas.get(line["accountId"], (None, None)))]
    return suggestion


# ── customer payments and cheques ──────────────────────────────────────────────────────────────

class PaymentIn(BaseModel):
    partyId: str
    amount: Decimal
    method: str
    reference: str | None = None
    note: str | None = None


@router.get("/customer-payments")
async def list_payments(partyId: str | None = None, from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
                        fromDay: date | None = None, toDay: date | None = None, q: str | None = None, voided: bool | None = None,
                        limit: int = 50, offset: int = 0, sort: str | None = None, order: str | None = None,
                        user: User = Depends(_receivables)) -> dict:
    """`from`/`to` are instants (today's payments at the counter); `fromDay`/`toDay` are shop days (the history).
    `sort`: at, number, customer, method, amount, balanceAfter, voucher or receivedBy; `order`: asc or desc. Without
    one, newest first."""
    items, total, standing = await accounts_money_service.list_payments(partyId, from_, to, min(max(limit, 1), 500), max(offset, 0),
                                                                        fromDay, toDay, q, voided, sort, order)
    return {"items": items, "total": total, "amount": standing}


@router.get("/customer-payments/{payment_id}")
async def get_payment(payment_id: str, user: User = Depends(_receivables)) -> dict:
    return await accounts_money_service.payment_detail(await _guard(accounts_money_service.get_payment, payment_id))


@router.post("/customer-payments")
async def receive_payment(payload: PaymentIn, user: User = Depends(_receive)) -> dict:
    payment = await _guard(accounts_money_service.receive_payment, user, payload.partyId, payload.amount, payload.method, payload.reference, payload.note)
    return await accounts_money_service.payment_detail(payment)


class VoidIn(BaseModel):
    reason: str | None = None


@router.post("/customer-payments/{payment_id}/void")
async def void_payment(payment_id: str, payload: VoidIn, user: User = Depends(_void_payment)) -> dict:
    """Takes the money back off the customer's account: needs the right to void, and what customers owe and cash and bank
    in the person's areas."""
    access = await access_of(user)
    for area in ("receivables", "cash-bank"):
        if not access.can_use(area):
            raise HTTPException(status.HTTP_403_FORBIDDEN, accounts_areas.refuse("You can't void a customer payment.", area).message)
    payment = await _guard(accounts_money_service.void_payment, user, payment_id, payload.reason)
    return await accounts_money_service.payment_detail(payment)


class ChequeIn(BaseModel):
    direction: str | None = None
    partyAccountId: str | None = None
    bankAccountId: str | None = None
    chequeNo: str | None = None
    drawnOn: str | None = None
    chequeDate: Day | None = None
    receivedOn: Day | None = None
    amount: Decimal | None = None
    note: str | None = None


class ChequeActionIn(BaseModel):
    bankAccountId: str | None = None
    date: Day | None = None
    note: str | None = None


async def _cheque_areas(user: User, *account_ids: str | None) -> None:
    """A cheque moves money (cash and bank) against a party, so the person needs both areas for every account it uses."""
    access = await access_of(user)
    if access.uses_everything:
        return
    if not access.can_use("cash-bank"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, accounts_areas.refuse("You can't work with cheques.", "cash-bank").message)
    for account_id in account_ids:
        account = await Account.get_or_none(id=account_id) if account_id else None
        if account is None:
            continue
        area = await accounts_areas.account_area(account)
        if not access.can_use(area, account.system_key):
            raise HTTPException(status.HTTP_403_FORBIDDEN, accounts_areas.refuse(f"You can't work with cheques for {account.name}.", area).message)


async def _cheque_accounts(cheque_id: str) -> tuple[str | None, str | None]:
    from app.models import Cheque

    try:
        cheque = await Cheque.get_or_none(id=cheque_id)
    except (ValueError, TypeError):
        cheque = None
    return (str(cheque.party_account_id), str(cheque.bank_account_id) if cheque.bank_account_id else None) if cheque else (None, None)


@router.get("/cheques")
async def list_cheques(status_: str | None = Query(None, alias="status"), direction: str | None = None, id: str | None = None,
                       user: User = Depends(_cheques_read)) -> list[dict]:
    """Only cheques for parties in the person's areas."""
    rows = await accounts_money_service.list_cheques(status_, direction, id)
    access = await access_of(user)
    if access.sees_everything:
        return rows
    areas = await accounts_areas.areas_by_account()
    return [r for r in rows if access.can_see(*areas.get(r["partyAccountId"], (None, None)))]


@router.post("/cheques")
async def record_cheque(payload: ChequeIn, user: User = Depends(_cheques)) -> dict:
    await _cheque_areas(user, payload.partyAccountId, payload.bankAccountId)
    cheque = await _guard(accounts_money_service.record_cheque, user, payload.model_dump(mode="json"))
    return await accounts_money_service.cheque_out(cheque)


@router.patch("/cheques/{cheque_id}")
async def update_cheque(cheque_id: str, payload: ChequeIn, user: User = Depends(_cheques)) -> dict:
    await _cheque_areas(user, *(await _cheque_accounts(cheque_id)), payload.partyAccountId, payload.bankAccountId)
    cheque = await _guard(accounts_money_service.update_cheque, user, cheque_id, payload.model_dump(mode="json", exclude_unset=True))
    return await accounts_money_service.cheque_out(cheque)


@router.post("/cheques/{cheque_id}/clear")
async def clear_cheque(cheque_id: str, payload: ChequeActionIn, user: User = Depends(_cheques)) -> dict:
    await _cheque_areas(user, *(await _cheque_accounts(cheque_id)), payload.bankAccountId)
    cheque = await _guard(accounts_money_service.clear_cheque, user, cheque_id, payload.bankAccountId or "", payload.date)
    return await accounts_money_service.cheque_out(cheque)


@router.post("/cheques/{cheque_id}/bounce")
async def bounce_cheque(cheque_id: str, payload: ChequeActionIn, user: User = Depends(_cheques)) -> dict:
    await _cheque_areas(user, *(await _cheque_accounts(cheque_id)))
    cheque = await _guard(accounts_money_service.bounce_cheque, user, cheque_id, payload.date, payload.note)
    return await accounts_money_service.cheque_out(cheque)


@router.post("/cheques/{cheque_id}/undo")
async def undo_cheque(cheque_id: str, payload: ChequeActionIn, user: User = Depends(_cheques)) -> dict:
    """A clearing or a bounce undone while its month is open."""
    await _cheque_areas(user, *(await _cheque_accounts(cheque_id)))
    cheque = await _guard(accounts_money_service.undo_cheque, user, cheque_id, payload.note)
    return await accounts_money_service.cheque_out(cheque)


@router.post("/cheques/{cheque_id}/redeposit")
async def redeposit_cheque(cheque_id: str, payload: ChequeActionIn, user: User = Depends(_cheques)) -> dict:
    """A bounced cheque deposited again: returns the new cheque in hand."""
    await _cheque_areas(user, *(await _cheque_accounts(cheque_id)))
    cheque = await _guard(accounts_money_service.redeposit_cheque, user, cheque_id, payload.date, payload.note)
    return await accounts_money_service.cheque_out(cheque)


@router.post("/cheques/{cheque_id}/cancel")
async def cancel_cheque(cheque_id: str, payload: ChequeActionIn, user: User = Depends(_cheques)) -> dict:
    await _cheque_areas(user, *(await _cheque_accounts(cheque_id)))
    cheque = await _guard(accounts_money_service.cancel_cheque, user, cheque_id, payload.note)
    return await accounts_money_service.cheque_out(cheque)
