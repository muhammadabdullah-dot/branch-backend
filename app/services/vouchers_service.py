"""Vouchers — the only way anything enters the books.

Manual vouchers follow the old software: a Cash Payment names the cash account once at the top and lists
what was paid below; a Journal lists debits and credits that must agree. They are saved as drafts and posted
by someone who may post. A posted voucher never changes: it's reversed by a journal that undoes it line for
line, and both stay on the record.

Automatic vouchers are the software's: generated from the branch's records by the posting run
(accounts_posting_service), one per source, and replaced when the source changes — unless its month is
closed. They are never edited or reversed by hand; the record they came from is what gets corrected.
"""
import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.models import Account, AccountsSettings, OutboxEvent, Party, User, Voucher, VoucherLine, next_value
from app.services import accounts_areas
from app.services.accounts_chart_service import money
from app.services.accounts_reports_service import shop_day

ZERO = Decimal("0")

TYPE_LABELS = {
    "CPV": "Cash Payment", "CRV": "Cash Receipt", "BPV": "Bank Payment", "BRV": "Bank Receipt",
    "JV": "Journal", "CV": "Contra", "OB": "Opening Balances",
    "SV": "Sales", "PV": "Purchase", "PRV": "Purchase Return", "TV": "Till", "STV": "Stock Adjustment",
    "TRV": "Transfer", "GVV": "Gift Voucher",
}
MANUAL_TYPES = ("CPV", "CRV", "BPV", "BRV", "JV", "CV", "OB")
HEADER_KINDS = {"CPV": ("cash",), "CRV": ("cash",), "BPV": ("bank", "wallet"), "BRV": ("bank", "wallet")}
PAYING_TYPES = ("CPV", "BPV")
RECEIVING_TYPES = ("CRV", "BRV")
MONEY_KINDS = ("cash", "bank", "wallet")


class VoucherError(Exception):
    def __init__(self, message: str):
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def settings() -> AccountsSettings:
    row = await AccountsSettings.get_or_none(id=1)
    if row is None:
        row = await AccountsSettings.create(id=1)
    return row


def closed_message(locked_until: date) -> str:
    return f"The books are closed up to {locked_until:%d %b %Y}. Date it after that, or reopen the month first."


def check_open(row: AccountsSettings, day: date) -> None:
    if row.locked_until and day <= row.locked_until:
        raise VoucherError(closed_message(row.locked_until))


async def book_prefix() -> str:
    from app.services import registration_service

    identity = await registration_service.current()
    return (identity.code if identity else "BR").upper()


async def _number(vtype: str) -> str:
    seq = await next_value(f"voucher:{vtype}", 1)
    return f"{await book_prefix()}-{vtype}-{seq:06d}"


# ── what a voucher looks like outside ───────────────────────────────────────────────────────────

async def voucher_out(voucher: Voucher, with_lines: bool = True, with_source: bool = False) -> dict:
    out = {
        "id": str(voucher.id), "number": voucher.number, "vtype": voucher.vtype, "typeLabel": TYPE_LABELS.get(voucher.vtype, voucher.vtype),
        "date": voucher.date.isoformat(), "status": voucher.status, "auto": voucher.auto, "source": voucher.source,
        "headerAccountId": str(voucher.header_account_id) if voucher.header_account_id else None,
        "referenceNo": voucher.reference_no, "description": voucher.description,
        "chequeNo": voucher.cheque_no, "chequeDate": voucher.cheque_date.isoformat() if voucher.cheque_date else None,
        "total": format(money(voucher.total), "f"), "createdBy": voucher.created_by_name, "postedBy": voucher.posted_by_name,
        "postedAt": voucher.posted_at.isoformat() if voucher.posted_at else None,
        "cancelledBy": voucher.cancelled_by_name, "cancelReason": voucher.cancel_reason,
        "reversalOf": str(voucher.reversal_of_id) if voucher.reversal_of_id else None, "reversed": voucher.reversed,
        "version": voucher.version, "createdAt": voucher.created_at.isoformat() if voucher.created_at else None,
    }
    if with_lines:
        lines = await VoucherLine.filter(voucher=voucher).order_by("line_no").prefetch_related("account")
        out["lines"] = [{
            "id": str(l.id), "accountId": str(l.account_id), "accountCode": l.account.code, "accountName": l.account.name,
            "accountKind": l.account.kind, "debit": format(money(l.debit), "f"), "credit": format(money(l.credit), "f"),
            "description": l.description, "referenceNo": l.reference_no,
            "isHeader": bool(voucher.header_account_id) and str(l.account_id) == str(voucher.header_account_id) and l.description == "__header__",
        } for l in lines]
        for line in out["lines"]:
            if line["description"] == "__header__":
                line["description"] = None
        if voucher.reversal_of_id:
            original = await Voucher.get_or_none(id=voucher.reversal_of_id)
            out["reversalOfNumber"] = original.number if original else None
        if voucher.reversed:
            reversal = await Voucher.filter(reversal_of_id=str(voucher.id)).first()
            out["reversedBy"] = reversal.number if reversal else None
            out["reversedById"] = str(reversal.id) if reversal else None
        if with_source and voucher.source:
            out["sourceInfo"] = await source_info(voucher.source)
    return out


# ── the record an automatic voucher came from ─────────────────────────────────────────────────────

SOURCE_PATTERN = re.compile(
    r"^(sales-day|till-open|till-close|cash-move|customer-payment|grn|purchase-return|stock-corrections|"
    r"transfer-out-received|transfer-out-settled|transfer-out|transfer-in|gift-voucher-expiry|gift-voucher|"
    r"cheque-issued-cleared|cheque-issued|cheque-received|cheque-cleared|cheque-bounced):([0-9A-Za-z-]+)"
)
BILLS_SHOWN = 300


async def source_info(source: str | None) -> dict | None:
    """What a screen needs to open the record behind an automatic voucher: its kind, its id and number, and for a day's
    sales the bills and returns in it. `missing` when the record is no longer there."""
    match = SOURCE_PATTERN.match(source or "")
    if not match:
        return None
    prefix, key = match.group(1), match.group(2)
    from app import models as m

    async def one(model, **lookup):
        try:
            return await model.get_or_none(**lookup)
        except (ValueError, TypeError):
            return None

    if prefix in ("sales-day", "stock-corrections"):
        info: dict = {"kind": prefix, "day": key}
        if prefix == "sales-day":
            from app.services.accounts_posting_service import bounds

            try:
                lo, hi = bounds(date.fromisoformat(key), date.fromisoformat(key))
            except ValueError:
                return {**info, "missing": True}
            sales = await m.SaleRecord.filter(at__gte=lo, at__lt=hi).order_by("at").limit(BILLS_SHOWN).prefetch_related("party")
            info["bills"] = [{"id": str(s.id), "invoice": s.invoice_number, "at": s.at.isoformat(), "total": format(money(s.grand_total), "f"),
                              "party": s.party.name if s.party and not s.party.is_walk_in else None} for s in sales]
            info["billCount"] = await m.SaleRecord.filter(at__gte=lo, at__lt=hi).count()
            returns = await m.ReturnRecord.filter(at__gte=lo, at__lt=hi).order_by("at").limit(BILLS_SHOWN).prefetch_related("against")
            info["returns"] = [{"id": str(r.id), "invoice": r.against.invoice_number, "at": r.at.isoformat(), "total": format(money(r.refund_total), "f"),
                                "method": r.refund_method} for r in returns]
        return info
    if prefix in ("till-open", "till-close"):
        till = await one(m.TillSession, id=key)
        return {"kind": "till", "tillSessionId": key, "number": till.session_number if till else None, "open": bool(till and till.status == "open"),
                "missing": till is None, "till": await _till_figures(till)}
    if prefix == "cash-move":
        movement = await one(m.CashMovement, id=key)
        till = await movement.till_session if movement else None
        return {"kind": "cash-move", "movementId": key, "tillSessionId": str(till.id) if till else None, "number": till.session_number if till else None,
                "movement": movement.kind if movement else None, "missing": movement is None, "till": await _till_figures(till)}
    if prefix == "customer-payment":
        payment = await one(m.CustomerPayment, id=key)
        return {"kind": "customer-payment", "paymentId": key, "number": payment.number if payment else None, "missing": payment is None}
    if prefix == "grn":
        grn = await one(m.GRN, id=key)
        return {"kind": "grn", "grnId": key, "number": grn.grn_number if grn else None, "missing": grn is None}
    if prefix == "purchase-return":
        ret = await one(m.PurchaseReturn, id=key)
        return {"kind": "purchase-return", "returnId": key, "number": ret.return_number if ret else None, "missing": ret is None}
    if prefix.startswith("transfer-"):
        transfer = await one(m.Transfer, id=key)
        return {"kind": "transfer", "transferId": key, "number": transfer.number if transfer else None, "missing": transfer is None}
    if prefix.startswith("gift-voucher"):
        voucher = await one(m.GiftVoucher, id=key)
        return {"kind": "gift-voucher", "giftVoucherId": key, "number": voucher.code if voucher else None, "missing": voucher is None}
    cheque = await one(m.Cheque, id=key)
    return {"kind": "cheque", "chequeId": key, "number": cheque.number if cheque else None, "missing": cheque is None}


async def _till_figures(till) -> dict | None:
    """The drawer's own close figures, so a till voucher shows the session it came from even where the X/Z screen can't."""
    if till is None:
        return None
    await till.fetch_related("opened_by", "closed_by", "counter")
    figure = lambda value: format(money(value), "f") if value is not None else None  # noqa: E731
    return {
        "status": till.status, "openedAt": till.opened_at.isoformat(), "closedAt": till.closed_at.isoformat() if till.closed_at else None,
        "openedBy": till.opened_by.name if till.opened_by else None, "closedBy": till.closed_by.name if till.closed_by else None,
        "counter": till.counter.name if till.counter else None, "openingFloat": figure(till.opening_float), "netCash": figure(till.net_cash),
        "countedCash": figure(till.counted_cash), "variance": figure(till.variance),
    }


async def problem_source(text: str) -> dict:
    """A posting problem names the record it is about at its start ("grn:<id>: debits and credits differ ...")."""
    match = SOURCE_PATTERN.match(text or "")
    if not match:
        return {"text": text, "source": None, "info": None, "voucherId": None}
    source = match.group(0)
    voucher = await Voucher.get_or_none(source=source)
    return {"text": text, "source": source, "info": await source_info(source), "voucherId": str(voucher.id) if voucher else None}


async def emit(voucher: Voucher) -> None:
    """Head office keeps a copy of the branch's books: every posted voucher, as it now stands."""
    payload = await voucher_out(voucher)
    for line in payload.get("lines", []):
        line.pop("accountName", None)
    await OutboxEvent.create(
        aggregate_type="AccVoucher", aggregate_id=str(voucher.id), payload={"voucher": payload},
        origin_device_id=get_device_id(),
    )


async def emit_settings(row: AccountsSettings | None = None) -> None:
    """Head office shows when a branch's books start, which months are closed and whether posting is working."""
    row = row or await settings()
    await OutboxEvent.create(
        aggregate_type="AccSettings", aggregate_id="settings", origin_device_id=get_device_id(),
        payload={"settings": {
            "fiscalStartMonth": row.fiscal_start_month, "booksStart": row.books_start.isoformat() if row.books_start else None,
            "lockedUntil": row.locked_until.isoformat() if row.locked_until else None,
            "lastPostingAt": row.last_posting_at.isoformat() if row.last_posting_at else None, "lastPostingNote": row.last_posting_note,
            "postingProblems": row.posting_problems or [],
        }},
    )


async def emit_deleted(voucher_id: str, number: str) -> None:
    await OutboxEvent.create(
        aggregate_type="AccVoucher", aggregate_id=voucher_id, payload={"voucher": {"id": voucher_id, "number": number, "deleted": True}},
        origin_device_id=get_device_id(),
    )


# ── manual vouchers ────────────────────────────────────────────────────────────────────────────

def _parse_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise VoucherError("That date isn't valid.") from exc


async def _build(
    vtype: str, payload: dict, access: accounts_areas.Access | None = None,
) -> tuple[Account | None, list[tuple[Account, Decimal, Decimal, str | None, str | None]]]:
    """The voucher's lines as they'll be written. With `access`, every account on it must be one the person may use."""
    if vtype not in MANUAL_TYPES:
        raise VoucherError("Pick a voucher type: cash or bank payment or receipt, journal, contra or opening balances.")
    header = None
    if vtype in HEADER_KINDS:
        header_id = payload.get("headerAccountId")
        header = await Account.get_or_none(id=header_id) if header_id else None
        if not header or not header.active:
            raise VoucherError("Pick the cash account it's paid from or received into." if vtype in ("CPV", "CRV") else "Pick the bank account.")
        if header.kind not in HEADER_KINDS[vtype]:
            raise VoucherError(f"{header.name} isn't a {'cash' if vtype in ('CPV', 'CRV') else 'bank'} account.")
    rows = []
    used: list[tuple[int | None, Account]] = [(None, header)] if header else []
    for index, line in enumerate(payload.get("lines") or []):
        account_id = line.get("accountId")
        account = await Account.get_or_none(id=account_id) if account_id else None
        debit, credit = money(line.get("debit")), money(line.get("credit"))
        if not account and debit == ZERO and credit == ZERO:
            continue  # an empty row left in the grid
        if not account:
            raise VoucherError(f"Line {index + 1}: pick an account.")
        if not account.active:
            raise VoucherError(f"Line {index + 1}: {account.name} is switched off.")
        if account.restricted:
            raise VoucherError(f"Line {index + 1}: {account.name} is kept for the software's own entries.")
        if debit < ZERO or credit < ZERO:
            raise VoucherError(f"Line {index + 1}: amounts can't be negative.")
        if (debit > ZERO) == (credit > ZERO):
            raise VoucherError(f"Line {index + 1}: enter a debit or a credit, not both and not neither.")
        if header and str(account.id) == str(header.id):
            raise VoucherError(f"Line {index + 1}: {account.name} is already the voucher's own account.")
        if vtype in PAYING_TYPES and credit > ZERO:
            raise VoucherError(f"Line {index + 1}: a payment voucher lists what was paid, so amounts go on the debit side.")
        if vtype in RECEIVING_TYPES and debit > ZERO:
            raise VoucherError(f"Line {index + 1}: a receipt voucher lists what was received, so amounts go on the credit side.")
        if vtype == "CV" and account.kind not in MONEY_KINDS:
            raise VoucherError(f"Line {index + 1}: a contra only moves money between cash, bank and wallet accounts.")
        rows.append((account, debit, credit, (line.get("description") or "").strip()[:255] or None, (line.get("referenceNo") or "").strip()[:60] or None))
        used.append((index + 1, account))
    if not rows:
        raise VoucherError("Add at least one line.")
    total_dr = sum((r[1] for r in rows), ZERO)
    total_cr = sum((r[2] for r in rows), ZERO)
    if vtype in PAYING_TYPES:
        rows.append((header, ZERO, total_dr, "__header__", None))
    elif vtype in RECEIVING_TYPES:
        rows.append((header, total_cr, ZERO, "__header__", None))
    elif vtype == "OB" and total_dr != total_cr:
        from app.services.accounts_chart_service import Resolver

        equity = await Resolver().key("equity.opening")
        diff = total_dr - total_cr
        rows.append((equity, ZERO if diff > 0 else -diff, diff if diff > 0 else ZERO, "Balancing figure", None))
        used.append((None, equity))
    elif total_dr != total_cr:
        raise VoucherError(f"Debits (Rs {total_dr:,.2f}) and credits (Rs {total_cr:,.2f}) must agree.")
    if access is not None:
        await accounts_areas.check_use(access, used)
    return header, rows


async def check_seen(access: accounts_areas.Access | None, voucher: Voucher, doing: str) -> None:
    """Changing, posting, cancelling or reversing a voucher needs every account on it within the person's areas."""
    if access is not None:
        ids = await VoucherLine.filter(voucher=voucher).values_list("account_id", flat=True)
        await accounts_areas.check_lines(access, voucher.number, [str(i) for i in ids], doing)


async def _write_lines(voucher: Voucher, rows) -> None:
    await VoucherLine.filter(voucher=voucher).delete()
    for index, (account, debit, credit, description, reference) in enumerate(rows):
        await VoucherLine.create(voucher=voucher, line_no=index + 1, account=account, debit=debit, credit=credit,
                                 description=description, reference_no=reference)
    voucher.total = sum((r[1] for r in rows), ZERO)


def _header_fields(voucher: Voucher, payload: dict) -> None:
    voucher.reference_no = (payload.get("referenceNo") or "").strip()[:60] or None
    voucher.description = (payload.get("description") or "").strip()[:500] or None
    voucher.cheque_no = (payload.get("chequeNo") or "").strip()[:30] or None
    voucher.cheque_date = _parse_date(payload["chequeDate"]) if payload.get("chequeDate") else None


@atomic()
async def create_draft(user: User, payload: dict, access: accounts_areas.Access | None = None) -> Voucher:
    vtype = (payload.get("vtype") or "").upper()
    day = _parse_date(payload.get("date") or shop_day())
    check_open(await settings(), day)
    header, rows = await _build(vtype, payload, access)
    voucher = Voucher(number=await _number(vtype), vtype=vtype, date=day, status="draft", header_account=header,
                      created_by=user, created_by_name=user.name)
    _header_fields(voucher, payload)
    await voucher.save()
    await _write_lines(voucher, rows)
    await voucher.save()
    return voucher


@atomic()
async def update_draft(user: User, voucher_id: str, payload: dict, access: accounts_areas.Access | None = None) -> Voucher:
    voucher = await Voucher.get_or_none(id=voucher_id)
    if not voucher or voucher.auto:
        raise VoucherError("That voucher doesn't exist.")
    if voucher.status != "draft":
        raise VoucherError(f"{voucher.number} is {voucher.status}. Only drafts can be changed.")
    await check_seen(access, voucher, "change")
    day = _parse_date(payload.get("date") or voucher.date)
    check_open(await settings(), day)
    header, rows = await _build(voucher.vtype, payload, access)
    voucher.date = day
    voucher.header_account = header
    _header_fields(voucher, payload)
    await _write_lines(voucher, rows)
    await voucher.save()
    return voucher


async def _customer_balance_effect(voucher: Voucher, sign: int) -> None:
    """A customer's running balance (what the till checks credit against) follows what's posted to their account by hand."""
    if voucher.vtype == "OB":
        return
    for line in await VoucherLine.filter(voucher=voucher).prefetch_related("account"):
        if line.account.kind != "customer" or not line.account.party_ref:
            continue
        party = await Party.get_or_none(id=line.account.party_ref)
        if party:
            party.credit_balance = party.credit_balance + sign * (line.debit - line.credit)
            await party.save(update_fields=["credit_balance"])


async def _check_limits(voucher: Voucher) -> None:
    from app.services import accounts_reports_service

    for line in await VoucherLine.filter(voucher=voucher).prefetch_related("account__group__category__type"):
        account = line.account
        if not account.check_limit or account.balance_limit is None:
            continue
        balance = await accounts_reports_service.balance_of(account.id)
        nature_debit = account.group.category.type.nature == "debit"
        after = balance + (line.debit - line.credit if nature_debit else line.credit - line.debit)
        if after > account.balance_limit:
            raise VoucherError(f"This takes {account.name} to Rs {after:,.2f}, over its limit of Rs {account.balance_limit:,.2f}.")


@atomic()
async def post(user: User, voucher_id: str, access: accounts_areas.Access | None = None) -> Voucher:
    voucher = await Voucher.get_or_none(id=voucher_id)
    if not voucher or voucher.auto:
        raise VoucherError("That voucher doesn't exist.")
    if voucher.status != "draft":
        raise VoucherError(f"{voucher.number} is already {voucher.status}.")
    await check_seen(access, voucher, "post")
    check_open(await settings(), voucher.date)
    lines = await VoucherLine.filter(voucher=voucher)
    if not lines:
        raise VoucherError("A voucher with no lines can't be posted.")
    if sum((l.debit for l in lines), ZERO) != sum((l.credit for l in lines), ZERO):
        raise VoucherError("Its debits and credits don't agree.")
    await _check_limits(voucher)
    voucher.status = "posted"
    voucher.posted_by = user
    voucher.posted_by_name = user.name
    voucher.posted_at = _now()
    voucher.version += 1
    await voucher.save()
    await _customer_balance_effect(voucher, 1)
    await emit(voucher)
    return voucher


@atomic()
async def cancel_draft(user: User, voucher_id: str, reason: str | None, access: accounts_areas.Access | None = None) -> Voucher:
    voucher = await Voucher.get_or_none(id=voucher_id)
    if not voucher or voucher.auto:
        raise VoucherError("That voucher doesn't exist.")
    if voucher.status != "draft":
        raise VoucherError("Only a draft can be cancelled. A posted voucher is reversed.")
    await check_seen(access, voucher, "cancel")
    voucher.status = "cancelled"
    voucher.cancelled_by_name = user.name
    voucher.cancelled_at = _now()
    voucher.cancel_reason = (reason or "").strip()[:255] or None
    await voucher.save()
    return voucher


@atomic()
async def reverse(user: User, voucher_id: str, day, reason: str, access: accounts_areas.Access | None = None) -> Voucher:
    voucher = await Voucher.get_or_none(id=voucher_id)
    if not voucher:
        raise VoucherError("That voucher doesn't exist.")
    if voucher.auto:
        raise VoucherError("The software made this voucher from the branch's records. Correct the record and it follows.")
    if voucher.status != "posted":
        raise VoucherError("Only a posted voucher can be reversed.")
    if voucher.reversed:
        raise VoucherError(f"{voucher.number} was already reversed.")
    if voucher.reversal_of_id:
        raise VoucherError("This is itself a reversal. Post a new voucher instead.")
    await check_seen(access, voucher, "reverse")
    if not (reason or "").strip() or len(reason.strip()) < 5:
        raise VoucherError("Say why it's being reversed.")
    day = _parse_date(day or shop_day())
    if day < voucher.date:
        raise VoucherError("A reversal can't be dated before the voucher it undoes.")
    check_open(await settings(), day)
    reversal = Voucher(
        number=await _number("JV"), vtype="JV", date=day, status="posted", reversal_of_id=str(voucher.id),
        description=f"Reversal of {voucher.number}: {reason.strip()}"[:500], reference_no=voucher.number,
        created_by=user, created_by_name=user.name, posted_by=user, posted_by_name=user.name, posted_at=_now(),
    )
    await reversal.save()
    rows = [(l.account, l.credit, l.debit, None if l.description == "__header__" else l.description, l.reference_no)
            for l in await VoucherLine.filter(voucher=voucher).order_by("line_no").prefetch_related("account")]
    await _write_lines(reversal, rows)
    await reversal.save()
    voucher.reversed = True
    voucher.version += 1
    await voucher.save()
    await _customer_balance_effect(reversal, 1)
    await emit(voucher)
    await emit(reversal)
    return reversal


async def get(voucher_id: str) -> Voucher:
    voucher = await Voucher.get_or_none(id=voucher_id)
    if not voucher:
        raise VoucherError("That voucher doesn't exist.")
    return voucher


async def list_vouchers(
    vtype: str | None, status: str | None, auto: bool | None, from_day: date | None, to_day: date | None,
    q: str | None, account_id: str | None, limit: int, offset: int, access: accounts_areas.Access | None = None,
    account_kind: str | None = None,
) -> tuple[list[dict], int]:
    qs = Voucher.all()
    if vtype:
        qs = qs.filter(vtype__in=[t.strip().upper() for t in vtype.split(",") if t.strip()])
    if status:
        qs = qs.filter(status__in=[s.strip() for s in status.split(",") if s.strip()])
    if auto is not None:
        qs = qs.filter(auto=auto)
    if from_day:
        qs = qs.filter(date__gte=from_day)
    if to_day:
        qs = qs.filter(date__lte=to_day)
    if q and q.strip():
        from tortoise.expressions import Q

        term = q.strip()
        qs = qs.filter(Q(number__icontains=term) | Q(description__icontains=term) | Q(reference_no__icontains=term))
    if account_id:
        ids = await VoucherLine.filter(account_id=account_id).distinct().values_list("voucher_id", flat=True)
        qs = qs.filter(id__in=list(ids))
    if account_kind:
        # Month by Month's "paid to suppliers" and "collected from customers": vouchers that touch a party of that kind.
        from tortoise.expressions import Subquery

        kinds = [k.strip() for k in account_kind.split(",") if k.strip()]
        qs = qs.filter(id__in=Subquery(VoucherLine.filter(account__kind__in=kinds).values("voucher_id")))
    hidden = await accounts_areas.unreadable_account_ids(access) if access is not None else []
    if hidden:
        # Only vouchers whose every line the person can see.
        from tortoise.expressions import Subquery

        qs = qs.exclude(id__in=Subquery(VoucherLine.filter(account_id__in=hidden).values("voucher_id")))
    total = await qs.count()
    rows = await qs.order_by("-date", "-created_at").offset(offset).limit(limit)
    return [await voucher_out(v, with_lines=False) for v in rows], total


# ── automatic vouchers ─────────────────────────────────────────────────────────────────────────

Row = tuple  # (Account, debit, credit, description)


def _fingerprint(vtype: str, day: date, description: str | None, reference: str | None, rows: list[Row]) -> str:
    body = json.dumps([vtype, day.isoformat(), description, reference,
                       [(str(a.id), format(d, "f"), format(c, "f"), desc) for a, d, c, desc in rows]], sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


async def _balanced(rows: list[Row], source: str) -> list[Row]:
    """Combine the rows, round them to paisa, drop the empty ones, and make them agree: a rounding difference of up
    to a rupee goes to ROUND OFF DIFFERENCES; anything bigger is a fault in the source, not something to paper over."""
    combined: dict[tuple[str, str | None, str], list] = {}
    for account, debit, credit, description in rows:
        net = Decimal(debit or 0) - Decimal(credit or 0)
        if net == 0:
            continue
        side = "D" if net > 0 else "C"
        key = (str(account.id), description, side)
        if key not in combined:
            combined[key] = [account, ZERO, description, side]
        combined[key][1] += abs(net)
    out: list[Row] = []
    for account, amount, description, side in combined.values():
        amount = money(amount)
        if amount == 0:
            continue
        out.append((account, amount if side == "D" else ZERO, amount if side == "C" else ZERO, description))
    diff = sum((r[1] for r in out), ZERO) - sum((r[2] for r in out), ZERO)
    if diff != 0:
        if abs(diff) > Decimal("1.00"):
            raise VoucherError(f"{source}: debits and credits differ by Rs {diff:,.2f}.")
        from app.services.accounts_chart_service import Resolver

        rounding = await Resolver().key("income.rounding")
        out.append((rounding, -diff if diff < 0 else ZERO, diff if diff > 0 else ZERO, "Paisa rounding"))
    out.sort(key=lambda r: (0 if r[1] > 0 else 1, r[0].code))
    return out


async def upsert_auto(
    source: str, vtype: str, day: date, rows: list[Row], description: str, reference: str | None = None,
    header: Account | None = None, row_settings: AccountsSettings | None = None,
) -> str:
    """Create or replace the automatic voucher for a source. Returns created · updated · unchanged · locked · empty."""
    row_settings = row_settings or await settings()
    rows = await _balanced(rows, source)
    existing = await Voucher.get_or_none(source=source)
    if not rows:
        if existing:
            return await delete_auto(source, row_settings)
        return "empty"
    description = description[:500]
    fingerprint = _fingerprint(vtype, day, description, reference, rows)
    if existing and existing.source_hash == fingerprint and existing.status == "posted":
        return "unchanged"
    locked = row_settings.locked_until
    if locked and (day <= locked or (existing and existing.date <= locked)):
        return "locked"
    now = _now()
    if existing:
        existing.vtype, existing.date, existing.description, existing.reference_no = vtype, day, description, reference
        existing.header_account = header
        existing.source_hash = fingerprint
        existing.status = "posted"
        existing.posted_at = now
        existing.version += 1
        await _write_lines(existing, [(a, d, c, desc, None) for a, d, c, desc in rows])
        await existing.save()
        await emit(existing)
        return "updated"
    voucher = Voucher(
        number=await _number(vtype), vtype=vtype, date=day, status="posted", auto=True, source=source, source_hash=fingerprint,
        header_account=header, description=description, reference_no=reference, created_by_name="System",
        posted_by_name="System", posted_at=now,
    )
    await voucher.save()
    await _write_lines(voucher, [(a, d, c, desc, None) for a, d, c, desc in rows])
    await voucher.save()
    await emit(voucher)
    return "created"


async def delete_auto(source: str, row_settings: AccountsSettings | None = None) -> str:
    existing = await Voucher.get_or_none(source=source, auto=True)
    if not existing:
        return "empty"
    row_settings = row_settings or await settings()
    if row_settings.locked_until and existing.date <= row_settings.locked_until:
        return "locked"
    voucher_id, number = str(existing.id), existing.number
    await existing.delete()
    await emit_deleted(voucher_id, number)
    return "deleted"
