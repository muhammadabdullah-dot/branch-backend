"""The branch's books over HTTP: chart, vouchers, reports, customer payments, cheques and the month-end controls."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.middlewares.auth import require_any_permission, require_permission
from app.models import Account, User, Voucher
from app.services import (
    accounts_chart_service,
    accounts_money_service,
    accounts_posting_service,
    accounts_reports_service,
    vouchers_service,
)
from app.services.accounts_reports_service import shop_day

Day = date

router = APIRouter(prefix="/accounts", tags=["accounts"])

_books = require_permission("accounts.books", "R")
_make = require_permission("accounts.vouchers", "W")
_post = require_permission("accounts.vouchers.post", "X")
_chart = require_permission("accounts.chart", "W")
_period = require_permission("accounts.period", "X")
_receive = require_permission("accounts.receivables", "W")
_payments_read = require_any_permission(("accounts.receivables", "R"), ("accounts.books", "R"))
_accounts_read = require_any_permission(("accounts.books", "R"), ("accounts.receivables", "R"), ("store.till", "W"))

ERRORS = (
    vouchers_service.VoucherError, accounts_chart_service.ChartError, accounts_money_service.MoneyError,
)


async def _guard(call: Callable, *args, **kwargs):
    try:
        return await call(*args, **kwargs)
    except ERRORS as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


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
async def chart(user: User = Depends(_accounts_read)) -> dict:
    await accounts_chart_service.ensure_party_accounts()
    return await accounts_chart_service.tree()


@router.get("/lookup")
async def lookup(q: str | None = None, kinds: str | None = None, limit: int = 30, user: User = Depends(_accounts_read)) -> list[dict]:
    """Accounts to pick in a voucher line or a till Cash Out, by code or name."""
    qs = Account.filter(active=True)
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
    group = await _guard(accounts_chart_service.create_group, payload.categoryCode or "", payload.name or "", payload.priority or 0, payload.manualCode)
    return accounts_chart_service.group_payload(group)


@router.patch("/groups/{code}")
async def update_group(code: str, payload: GroupIn, user: User = Depends(_chart)) -> dict:
    group = await _guard(accounts_chart_service.update_group, code, payload.name, payload.priority, payload.manualCode)
    return accounts_chart_service.group_payload(group)


@router.post("/sub-groups")
async def create_sub_group(payload: SubGroupIn, user: User = Depends(_chart)) -> dict:
    sub = await _guard(accounts_chart_service.create_sub_group, payload.groupCode or "", payload.name)
    return {"code": sub.code, "name": sub.name, "groupCode": sub.group_id}


@router.patch("/sub-groups/{code}")
async def update_sub_group(code: str, payload: SubGroupIn, user: User = Depends(_chart)) -> dict:
    sub = await _guard(accounts_chart_service.update_sub_group, code, payload.name)
    return {"code": sub.code, "name": sub.name, "groupCode": sub.group_id}


@router.post("/accounts")
async def create_account(payload: AccountIn, user: User = Depends(_chart)) -> dict:
    account = await _guard(accounts_chart_service.create_account, _account_fields(payload))
    return await accounts_chart_service.account_payload(account)


@router.patch("/accounts/{account_id}")
async def update_account(account_id: str, payload: AccountIn, user: User = Depends(_chart)) -> dict:
    account = await _guard(accounts_chart_service.update_account, account_id, _account_fields(payload))
    return await accounts_chart_service.account_payload(account)


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, user: User = Depends(_chart)) -> dict:
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
    limit: int = 50, offset: int = 0, user: User = Depends(_books),
) -> dict:
    await accounts_posting_service.ensure_recent()
    items, total = await vouchers_service.list_vouchers(type, status_, auto, from_, to, q, accountId, min(max(limit, 1), 500), max(offset, 0))
    return {"items": items, "total": total}


@router.get("/vouchers/{voucher_id}")
async def get_voucher(voucher_id: str, user: User = Depends(_books)) -> dict:
    return await vouchers_service.voucher_out(await _guard(vouchers_service.get, voucher_id))


async def _can_post(user: User) -> bool:
    from app.services.rbac_service import has_permission

    return await has_permission(user, "accounts.vouchers.post", "X")


@router.post("/vouchers")
async def create_voucher(payload: VoucherIn, user: User = Depends(_make)) -> dict:
    if payload.post and not await _can_post(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can save vouchers but not post them. Save it, and someone who posts will.")
    data = payload.model_dump(mode="json")
    voucher = await _guard(vouchers_service.create_draft, user, data)
    if payload.post:
        voucher = await _guard(vouchers_service.post, user, str(voucher.id))
    return await vouchers_service.voucher_out(voucher)


@router.put("/vouchers/{voucher_id}")
async def update_voucher(voucher_id: str, payload: VoucherIn, user: User = Depends(_make)) -> dict:
    if payload.post and not await _can_post(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can save vouchers but not post them.")
    voucher = await _guard(vouchers_service.update_draft, user, voucher_id, payload.model_dump(mode="json"))
    if payload.post:
        voucher = await _guard(vouchers_service.post, user, voucher_id)
    return await vouchers_service.voucher_out(voucher)


@router.post("/vouchers/{voucher_id}/post")
async def post_voucher(voucher_id: str, user: User = Depends(_post)) -> dict:
    return await vouchers_service.voucher_out(await _guard(vouchers_service.post, user, voucher_id))


@router.post("/vouchers/{voucher_id}/cancel")
async def cancel_voucher(voucher_id: str, payload: ReasonIn, user: User = Depends(_make)) -> dict:
    return await vouchers_service.voucher_out(await _guard(vouchers_service.cancel_draft, user, voucher_id, payload.reason))


@router.post("/vouchers/{voucher_id}/reverse")
async def reverse_voucher(voucher_id: str, payload: ReasonIn, user: User = Depends(_post)) -> dict:
    reversal = await _guard(vouchers_service.reverse, user, voucher_id, payload.date, payload.reason or "")
    return await vouchers_service.voucher_out(reversal)


# ── reports ────────────────────────────────────────────────────────────────────────────────────

@router.get("/ledger")
async def ledger(accountId: str, from_: date | None = Query(None, alias="from"), to: date | None = None, pdc: bool = False, user: User = Depends(_books)) -> dict:
    await accounts_posting_service.ensure_recent()
    start, end = _range(from_, to)
    return await _guard(accounts_reports_service.ledger, accountId, start, end, pdc)


@router.get("/trial-balance")
async def trial_balance(from_: date | None = Query(None, alias="from"), to: date | None = None, group: str | None = None, zero: bool = False, user: User = Depends(_books)) -> dict:
    await accounts_posting_service.ensure_recent()
    start, end = _range(from_, to)
    return await accounts_reports_service.trial_balance(start, end, group, zero)


@router.get("/income-statement")
async def income_statement(from_: date | None = Query(None, alias="from"), to: date | None = None, user: User = Depends(_books)) -> dict:
    await accounts_posting_service.ensure_recent()
    end = to or shop_day()
    start = from_ or end.replace(day=1)
    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The start date is after the end date.")
    return await accounts_reports_service.income_statement(start, end)


@router.get("/month-by-month")
async def month_by_month(from_: date | None = Query(None, alias="from"), to: date | None = None, user: User = Depends(_books)) -> dict:
    await accounts_posting_service.ensure_recent()
    end = to or shop_day()
    start = from_ or (await vouchers_service.settings()).books_start or end.replace(day=1)
    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The start date is after the end date.")
    if (end.year - start.year) * 12 + end.month - start.month >= 24:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick up to 24 months.")
    return await accounts_reports_service.month_by_month(start, end)


@router.get("/balance-sheet")
async def balance_sheet(asOf: date | None = None, user: User = Depends(_books)) -> dict:
    await accounts_posting_service.ensure_recent()
    settings = await vouchers_service.settings()
    return await accounts_reports_service.balance_sheet(asOf or shop_day(), settings.fiscal_start_month)


@router.get("/day-book")
async def day_book(from_: date | None = Query(None, alias="from"), to: date | None = None, type: str | None = None, limit: int = 100, offset: int = 0, user: User = Depends(_books)) -> dict:
    await accounts_posting_service.ensure_recent()
    end = to or shop_day()
    start = from_ or end
    return await accounts_reports_service.day_book(start, end, type, min(max(limit, 1), 500), max(offset, 0))


@router.get("/ageing")
async def ageing(kind: str = "customer", asOf: date | None = None, user: User = Depends(_books)) -> dict:
    await accounts_posting_service.ensure_recent()
    return await _guard(accounts_reports_service.ageing, kind, asOf or shop_day())


@router.get("/dashboard")
async def dashboard(user: User = Depends(_books)) -> dict:
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
async def get_settings(user: User = Depends(_books)) -> dict:
    return _settings_out(await vouchers_service.settings())


@router.patch("/settings")
async def update_settings(payload: SettingsIn, user: User = Depends(_period)) -> dict:
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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Say why the books are being reopened — it stays on the record.")
    until = payload.until
    if until is not None and until >= row.locked_until:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reopening keeps the books closed up to an earlier day, or none.")
    old = row.locked_until
    row.locked_until = until
    await row.save()
    await _record_period(user, f"Reopened the books (were closed to {old:%d %b %Y}, now {'open' if until is None else 'closed to ' + until.strftime('%d %b %Y')}): {payload.reason.strip()}")
    await accounts_posting_service.run(full=True)
    return _settings_out(await vouchers_service.settings())


async def _record_period(user: User, text: str) -> None:
    from app.services import alerts_service

    await alerts_service.notify("accounts.period", text, body=f"By {user.name}", link="/accounts/settings",
                                audience_any=[("accounts.period", "X"), ("accounts.books", "R")], tone="warning")


class RunIn(BaseModel):
    full: bool = False


@router.post("/posting/run")
async def run_posting(payload: RunIn, user: User = Depends(_books)) -> dict:
    if payload.full:
        from app.services.rbac_service import has_permission

        if not await has_permission(user, "accounts.period", "X"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Re-posting everything needs the right to close months.")
    return await accounts_posting_service.run(full=payload.full)


@router.get("/opening-suggestion")
async def opening_suggestion(user: User = Depends(_make)) -> dict:
    return await accounts_posting_service.opening_suggestion()


# ── customer payments and cheques ──────────────────────────────────────────────────────────────

class PaymentIn(BaseModel):
    partyId: str
    amount: Decimal
    method: str
    reference: str | None = None
    note: str | None = None


@router.get("/customer-payments")
async def list_payments(partyId: str | None = None, from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
                        limit: int = 50, offset: int = 0, user: User = Depends(_payments_read)) -> dict:
    items, total = await accounts_money_service.list_payments(partyId, from_, to, min(max(limit, 1), 500), max(offset, 0))
    return {"items": items, "total": total}


@router.post("/customer-payments")
async def receive_payment(payload: PaymentIn, user: User = Depends(_receive)) -> dict:
    payment = await _guard(accounts_money_service.receive_payment, user, payload.partyId, payload.amount, payload.method, payload.reference, payload.note)
    return accounts_money_service.payment_out(payment)


class ChequeIn(BaseModel):
    partyAccountId: str | None = None
    chequeNo: str | None = None
    drawnOn: str | None = None
    chequeDate: Day | None = None
    receivedOn: Day | None = None
    amount: Decimal = Decimal("0")
    note: str | None = None


class ChequeActionIn(BaseModel):
    bankAccountId: str | None = None
    date: Day | None = None
    note: str | None = None


@router.get("/cheques")
async def list_cheques(status_: str | None = Query(None, alias="status"), user: User = Depends(_books)) -> list[dict]:
    return await accounts_money_service.list_cheques(status_)


@router.post("/cheques")
async def record_cheque(payload: ChequeIn, user: User = Depends(_make)) -> dict:
    cheque = await _guard(accounts_money_service.record_cheque, user, payload.model_dump(mode="json"))
    return await accounts_money_service.cheque_out(cheque)


@router.post("/cheques/{cheque_id}/clear")
async def clear_cheque(cheque_id: str, payload: ChequeActionIn, user: User = Depends(_make)) -> dict:
    cheque = await _guard(accounts_money_service.clear_cheque, user, cheque_id, payload.bankAccountId or "", payload.date)
    return await accounts_money_service.cheque_out(cheque)


@router.post("/cheques/{cheque_id}/bounce")
async def bounce_cheque(cheque_id: str, payload: ChequeActionIn, user: User = Depends(_make)) -> dict:
    cheque = await _guard(accounts_money_service.bounce_cheque, user, cheque_id, payload.date, payload.note)
    return await accounts_money_service.cheque_out(cheque)


@router.post("/cheques/{cheque_id}/cancel")
async def cancel_cheque(cheque_id: str, payload: ChequeActionIn, user: User = Depends(_make)) -> dict:
    cheque = await _guard(accounts_money_service.cancel_cheque, user, cheque_id, payload.note)
    return await accounts_money_service.cheque_out(cheque)
