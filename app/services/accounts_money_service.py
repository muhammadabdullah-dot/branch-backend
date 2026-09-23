"""Money coming in against what customers owe: payments at the counter, and cheques — and cheques the branch writes.

A credit customer's balance (the figure the till checks their credit against) goes down the moment they pay —
cash into the open till, or card / bank / wallet. A cheque takes the balance down when it's received, and puts
it back if the cheque bounces or turns out to be a mistake.

Nothing here writes a voucher: the posting run turns payments and cheques into vouchers, and when a payment is voided
or a cheque's clearing is undone the voucher it had is taken out at once, so the books follow without waiting.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from tortoise.models import Model
from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.models import Account, CashMovement, Cheque, CustomerPayment, OutboxEvent, Party, User, Voucher, next_value
from app.services import vouchers_service
from app.services.accounts_chart_service import customer_account, emit_account, money, next_account_code
from app.services.accounts_reports_service import PKT, shop_day

ZERO = Decimal("0")
PAYMENT_METHODS = ("CASH", "CARD", "BANK", "EASYPAISA", "JAZZCASH")
CHEQUES_ISSUED_KEY = "liab.cheques_issued"
CHEQUES_ISSUED_GROUP = "2104"


class MoneyError(Exception):
    def __init__(self, message: str):
        self.message = message


def _loaded(obj, name: str):
    """A related row that was fetched with the query, or None — never a query of its own."""
    value = getattr(obj, name, None)
    return value if isinstance(value, Model) else None


def payment_out(payment: CustomerPayment, extra: dict | None = None) -> dict:
    party = _loaded(payment, "party")
    received_by = _loaded(payment, "received_by")
    return {
        "id": str(payment.id), "number": payment.number, "partyId": str(payment.party_id),
        "partyName": party.name if party else None, "partyCode": party.code if party else None,
        "amount": format(money(payment.amount), "f"), "method": payment.method, "reference": payment.reference, "note": payment.note,
        "receivedBy": received_by.name if received_by else None,
        "balanceAfter": format(money(payment.balance_after), "f") if payment.balance_after is not None else None,
        "at": payment.at.isoformat(),
        "voidedAt": payment.voided_at.isoformat() if payment.voided_at else None, "voidedBy": payment.voided_by_name,
        "voidReason": payment.void_reason,
        **(extra or {}),
    }


async def payment_detail(payment: CustomerPayment) -> dict:
    """A payment with what a screen links to: the customer's account, the voucher it made, and the till its cash went into."""
    await payment.fetch_related("party", "received_by")
    account = await Account.get_or_none(system_key=f"customer:{payment.party_id}")
    movement = await CashMovement.get_or_none(id=payment.cash_movement_id).prefetch_related("till_session") if payment.cash_movement_id else None
    till = movement.till_session if movement else None
    source = f"cash-move:{movement.id}" if movement else f"customer-payment:{payment.id}"
    voucher = await Voucher.get_or_none(source=source)
    blocked = await _void_blocked(payment, movement)
    return payment_out(payment, {
        "accountId": str(account.id) if account else None, "voucherId": str(voucher.id) if voucher else None,
        "voucherNumber": voucher.number if voucher else None,
        "tillSessionId": str(till.id) if till else None, "tillSessionNumber": till.session_number if till else None,
        "tillOpen": bool(till and till.status == "open"), "voidable": blocked is None, "voidBlocked": blocked,
    })


@atomic()
async def receive_payment(user: User, party_id: str, amount: Decimal, method: str, reference: str | None, note: str | None) -> CustomerPayment:
    party = await Party.get_or_none(id=party_id)
    if not party or not party.active:
        raise MoneyError("Pick the customer who's paying.")
    if party.is_walk_in:
        raise MoneyError("A walk-in customer has no balance to pay off.")
    amount = money(amount)
    if amount <= 0:
        raise MoneyError("Enter the amount received.")
    method = (method or "").upper()
    if method not in PAYMENT_METHODS:
        raise MoneyError("Pick how they paid: cash, card, bank transfer, Easypaisa or JazzCash.")
    reference = (reference or "").strip()[:60] or None
    if method == "BANK" and not reference:
        raise MoneyError("A bank transfer needs its transaction ID.")
    seq = await next_value("customer_payment", 1)
    number = f"RCP-{seq:06d}"
    movement = None
    if method == "CASH":
        from app.services.till_service import session_for

        # The drawer of whoever takes the money — with several tills open there is no single "the till".
        till = await session_for(user)
        if not till:
            raise MoneyError("Cash goes into your till, so open your till first.")
        account = await customer_account(party)
        movement = await CashMovement.create(
            till_session=till, kind="in", amount=amount, denominations={}, user=user, account=account, payee=party.name,
            notes=f"{number}: payment from {party.name} ({party.code})",
        )
    party.credit_balance = Decimal(party.credit_balance) - amount
    await party.save(update_fields=["credit_balance"])
    payment = await CustomerPayment.create(
        number=number, party=party, amount=amount, method=method, reference=reference, note=(note or "").strip()[:255] or None,
        received_by=user, cash_movement=movement, balance_after=party.credit_balance,
    )
    await OutboxEvent.create(
        aggregate_type="CustomerPayment", aggregate_id=str(payment.id),
        payload={"number": number, "partyCode": party.code, "amount": str(amount), "method": method, "balanceAfter": str(party.credit_balance)},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    await payment.fetch_related("party", "received_by")
    return payment


def day_bounds(start: date | None, end: date | None) -> tuple[datetime | None, datetime | None]:
    """Shop days (Pakistan time) as the UTC instants they run between."""
    lo = datetime.combine(start, datetime.min.time(), tzinfo=PKT).astimezone(timezone.utc) if start else None
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=PKT).astimezone(timezone.utc) if end else None
    return lo, hi


# The payments history's headings, each to what it sorts by: amounts as numbers (the database keeps them as text), the
# customer by name and the voucher by the number of the one the payment made.
PAYMENT_SORTS = {
    "at": "at", "number": "number", "customer": "party__name", "method": "method", "amount": "sort_amount",
    "balanceAfter": "sort_balance", "voucher": "sort_voucher", "receivedBy": "received_by__name",
}


def _sorted_payments(qs, sort: str | None, order: str | None):
    """In the order a heading asks for, newest first within a tie. No heading (or one it doesn't know): newest first."""
    field = PAYMENT_SORTS.get(sort or "")
    if not field:
        return qs.order_by("-at")
    from tortoise.expressions import RawSQL

    table, vouchers = CustomerPayment._meta.db_table, Voucher._meta.db_table
    if field == "sort_amount":
        qs = qs.annotate(sort_amount=RawSQL(f'CAST("{table}"."amount" AS REAL)'))
    elif field == "sort_balance":
        qs = qs.annotate(sort_balance=RawSQL(f'CAST("{table}"."balance_after" AS REAL)'))
    elif field == "sort_voucher":
        # Found the way payment_detail finds it: by the till movement the payment made, else by the payment itself.
        qs = qs.annotate(sort_voucher=RawSQL(
            f'(SELECT v."number" FROM "{vouchers}" v WHERE v."source" = CASE WHEN "{table}"."cash_movement_id" IS NULL '
            f"""THEN 'customer-payment:' || "{table}"."id" ELSE 'cash-move:' || "{table}"."cash_movement_id" END)"""
        ))
    direction = "-" if order == "desc" else ""
    if field == "at":
        return qs.order_by(f"{direction}at", "id")
    return qs.order_by(f"{direction}{field}", "-at", "id")


async def list_payments(party_id: str | None, from_at: datetime | None, to_at: datetime | None, limit: int, offset: int,
                        from_day: date | None = None, to_day: date | None = None, q: str | None = None,
                        voided: bool | None = None, sort: str | None = None, order: str | None = None) -> tuple[list[dict], int, str]:
    qs = CustomerPayment.all()
    if party_id:
        qs = qs.filter(party_id=party_id)
    if from_at:
        qs = qs.filter(at__gte=from_at)
    if to_at:
        qs = qs.filter(at__lte=to_at)
    lo, hi = day_bounds(from_day, to_day)
    if lo:
        qs = qs.filter(at__gte=lo)
    if hi:
        qs = qs.filter(at__lt=hi)
    if q and q.strip():
        from tortoise.expressions import Q

        term = q.strip()
        qs = qs.filter(Q(number__icontains=term) | Q(party__name__icontains=term) | Q(party__code__icontains=term) | Q(reference__icontains=term))
    if voided is not None:
        qs = qs.filter(voided_at__isnull=not voided)
    total = await qs.count()
    # What stands: the sum of the payments that aren't voided, for the whole filter, not just the page.
    standing = sum((Decimal(str(a)) for a in await qs.filter(voided_at__isnull=True).values_list("amount", flat=True)), ZERO)
    rows = await _sorted_payments(qs, sort, order).offset(offset).limit(limit)
    return [await payment_detail(p) for p in rows], total, format(money(standing), "f")


async def get_payment(payment_id: str) -> CustomerPayment:
    try:
        payment = await CustomerPayment.get_or_none(id=payment_id)
    except (ValueError, TypeError):
        payment = None
    if not payment:
        raise MoneyError("That payment doesn't exist.")
    return payment


async def _void_blocked(payment: CustomerPayment, movement: CashMovement | None) -> str | None:
    """Why a payment can't be voided now, or None when it can."""
    if payment.voided_at:
        return "It is already voided."
    row = await vouchers_service.settings()
    if row.locked_until and shop_day(payment.at) <= row.locked_until:
        return f"Its month is closed (the books are closed up to {row.locked_until:%d %b %Y})."
    if payment.method == "CASH" and movement is not None:
        till = movement.till_session if _loaded(movement, "till_session") else await movement.till_session
        if till.status != "open":
            return f"The cash went into till {till.session_number}, which is closed and counted. Pay the customer back with a cash payment voucher instead."
    return None


@atomic()
async def void_payment(user: User, payment_id: str, reason: str | None) -> CustomerPayment:
    """Take a payment back: the customer owes it again, its voucher comes out of the books, and a cash payment's cash-in
    comes out of the drawer so the till still counts right. Only while its month is open, and for cash only while its
    till is still open (a closed till has been counted, so the money is refunded with a voucher instead)."""
    payment = await get_payment(payment_id)
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise MoneyError("Say why the payment is being voided. It stays on the record.")
    movement = await CashMovement.get_or_none(id=payment.cash_movement_id).prefetch_related("till_session") if payment.cash_movement_id else None
    blocked = await _void_blocked(payment, movement)
    if blocked:
        raise MoneyError(f"{payment.number} can't be voided. {blocked}")
    if movement is not None:
        source = f"cash-move:{movement.id}"
        await movement.delete()
    else:
        source = f"customer-payment:{payment.id}"
    if await vouchers_service.delete_auto(source) == "locked":
        raise MoneyError(f"{payment.number} is in a closed month, so it can't be voided.")
    party = await Party.get(id=payment.party_id)
    party.credit_balance = Decimal(party.credit_balance) + Decimal(payment.amount)
    await party.save(update_fields=["credit_balance"])
    payment.voided_at = now()
    payment.voided_by_name = user.name
    payment.void_reason = reason[:255]
    payment.cash_movement = None
    await payment.save()
    await OutboxEvent.create(
        aggregate_type="CustomerPayment", aggregate_id=str(payment.id),
        payload={"number": payment.number, "partyCode": party.code, "amount": str(money(payment.amount)), "method": payment.method,
                 "voided": True, "voidReason": payment.void_reason, "balanceAfter": str(party.credit_balance)},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    await payment.fetch_related("party", "received_by")
    return payment


# ── cheques ────────────────────────────────────────────────────────────────────────────────────

CHEQUE_SOURCES = ("cheque-received", "cheque-cleared", "cheque-bounced", "cheque-issued", "cheque-issued-cleared")


async def ensure_cheques_issued_account() -> Account:
    """Where a cheque the branch writes sits until the bank pays it: owed, but not yet out of the bank."""
    account = await Account.get_or_none(system_key=CHEQUES_ISSUED_KEY)
    if account is None:
        account = await Account.create(
            code=await next_account_code(CHEQUES_ISSUED_GROUP), name="CHEQUES ISSUED (NOT YET CLEARED)", group_id=CHEQUES_ISSUED_GROUP,
            sub_group_id=f"{CHEQUES_ISSUED_GROUP}01", kind="general", system_key=CHEQUES_ISSUED_KEY, standard=False,
        )
        await emit_account(account)
    return account


async def cheque_out(cheque: Cheque) -> dict:
    await cheque.fetch_related("party_account", "bank_account")
    today = shop_day()
    sources = [f"{prefix}:{cheque.id}" for prefix in CHEQUE_SOURCES]
    vouchers = await Voucher.filter(source__in=sources).order_by("date", "created_at")
    redeposit_of = await Cheque.get_or_none(id=cheque.redeposit_of_id) if cheque.redeposit_of_id else None
    redeposited_as = await Cheque.filter(redeposit_of_id=str(cheque.id)).first()
    return {
        "id": str(cheque.id), "number": cheque.number, "direction": cheque.direction, "partyAccountId": str(cheque.party_account_id),
        "partyAccountName": cheque.party_account.name, "partyAccountCode": cheque.party_account.code, "partyKind": cheque.party_account.kind,
        "bankAccountId": str(cheque.bank_account_id) if cheque.bank_account_id else None,
        "bankAccountName": cheque.bank_account.name if cheque.bank_account else None,
        "chequeNo": cheque.cheque_no, "drawnOn": cheque.drawn_on, "chequeDate": cheque.cheque_date.isoformat(),
        "receivedOn": cheque.received_on.isoformat(), "amount": format(money(cheque.amount), "f"), "status": cheque.status,
        "postDated": cheque.status == "pending" and cheque.cheque_date > today,
        "clearedOn": cheque.cleared_on.isoformat() if cheque.cleared_on else None,
        "bouncedOn": cheque.bounced_on.isoformat() if cheque.bounced_on else None,
        "note": cheque.note, "createdBy": cheque.created_by_name,
        "redepositOfId": cheque.redeposit_of_id, "redepositOfNumber": redeposit_of.number if redeposit_of else None,
        "redepositedAsId": str(redeposited_as.id) if redeposited_as else None, "redepositedAsNumber": redeposited_as.number if redeposited_as else None,
        "vouchers": [{"id": str(v.id), "number": v.number, "date": v.date.isoformat(), "typeLabel": vouchers_service.TYPE_LABELS.get(v.vtype, v.vtype)} for v in vouchers],
    }


async def _party_effect(account: Account, amount: Decimal) -> None:
    if account.kind == "customer" and account.party_ref:
        party = await Party.get_or_none(id=account.party_ref)
        if party:
            party.credit_balance = Decimal(party.credit_balance) + amount
            await party.save(update_fields=["credit_balance"])


def _day(value, what: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise MoneyError(f"Enter the {what}.") from exc


def _note(existing: str | None, addition: str) -> str:
    return f"{(existing.rstrip('. ') + '. ') if existing else ''}{addition}"[:255]


async def _drop(cheque: Cheque, *prefixes: str) -> None:
    """Take out the vouchers a cheque's undone step made, now rather than at the next posting run."""
    for prefix in prefixes:
        if await vouchers_service.delete_auto(f"{prefix}:{cheque.id}") == "locked":
            raise MoneyError(f"{cheque.number}'s voucher is in a closed month. Reopen the month first.")


async def _party_account(direction: str, account_id) -> Account:
    account = await Account.get_or_none(id=account_id) if account_id else None
    if not account or not account.active:
        raise MoneyError("Pick who the cheque is from." if direction == "received" else "Pick the supplier the cheque is written to.")
    if account.kind in ("cash", "bank", "wallet"):
        raise MoneyError("A cheque comes from a customer or another party, not one of the branch's own cash or bank accounts."
                         if direction == "received" else "A cheque is written to a supplier or another party, not to one of the branch's own accounts.")
    return account


async def _bank_account(account_id, what: str) -> Account:
    bank = await Account.get_or_none(id=account_id) if account_id else None
    if not bank or bank.kind not in ("bank", "wallet") or not bank.active:
        raise MoneyError(what)
    return bank


@atomic()
async def record_cheque(user: User, payload: dict) -> Cheque:
    direction = (payload.get("direction") or "received").lower()
    if direction not in ("received", "issued"):
        raise MoneyError("A cheque is either received or issued.")
    account = await _party_account(direction, payload.get("partyAccountId"))
    amount = money(payload.get("amount"))
    if amount <= 0:
        raise MoneyError("Enter the cheque's amount.")
    cheque_no = (payload.get("chequeNo") or "").strip()[:30]
    if not cheque_no:
        raise MoneyError("Enter the cheque number.")
    received_on = _day(payload.get("receivedOn") or shop_day(), "day it was received" if direction == "received" else "day it was written")
    vouchers_service.check_open(await vouchers_service.settings(), received_on)
    bank = None
    if direction == "issued":
        bank = await _bank_account(payload.get("bankAccountId"), "Pick the bank account the cheque is drawn on.")
        await ensure_cheques_issued_account()
    seq = await next_value("cheque", 1)
    cheque = await Cheque.create(
        number=f"CHQ-{seq:06d}", direction=direction, party_account=account, bank_account=bank, cheque_no=cheque_no,
        drawn_on=(payload.get("drawnOn") or "").strip()[:80] or (bank.bank_name if bank else None) or None,
        cheque_date=_day(payload.get("chequeDate") or received_on, "cheque's date"), received_on=received_on, amount=amount,
        note=(payload.get("note") or "").strip()[:255] or None, created_by_name=user.name,
    )
    if direction == "received":
        await _party_effect(account, -amount)
    return cheque


async def _cheque(cheque_id: str) -> Cheque:
    try:
        cheque = await Cheque.get_or_none(id=cheque_id).prefetch_related("party_account", "bank_account")
    except (ValueError, TypeError):
        cheque = None
    if not cheque:
        raise MoneyError("That cheque doesn't exist.")
    return cheque


@atomic()
async def update_cheque(user: User, cheque_id: str, payload: dict) -> Cheque:
    """Correct a cheque still in hand: who, which cheque, the dates, the amount. A cleared or bounced one is undone first."""
    cheque = await _cheque(cheque_id)
    if cheque.status != "pending":
        raise MoneyError(f"{cheque.number} is {_status_word(cheque)}. Only a cheque still in hand can be changed. Undo the clearing first.")
    settings = await vouchers_service.settings()
    vouchers_service.check_open(settings, cheque.received_on)
    account = cheque.party_account
    if payload.get("partyAccountId") and str(payload["partyAccountId"]) != str(cheque.party_account_id):
        account = await _party_account(cheque.direction, payload["partyAccountId"])
    amount = money(payload["amount"]) if payload.get("amount") not in (None, "") else money(cheque.amount)
    if amount <= 0:
        raise MoneyError("Enter the cheque's amount.")
    cheque_no = (payload.get("chequeNo") or cheque.cheque_no or "").strip()[:30]
    if not cheque_no:
        raise MoneyError("Enter the cheque number.")
    received_on = _day(payload.get("receivedOn") or cheque.received_on, "day it was received")
    vouchers_service.check_open(settings, received_on)
    if cheque.direction == "issued" and payload.get("bankAccountId"):
        cheque.bank_account = await _bank_account(payload["bankAccountId"], "Pick the bank account the cheque is drawn on.")
    if cheque.direction == "received":
        # What the old customer owes goes back up, and the new one's comes down.
        await _party_effect(cheque.party_account, Decimal(cheque.amount))
        await _party_effect(account, -amount)
    changes = []
    if str(account.id) != str(cheque.party_account_id):
        changes.append(f"from {cheque.party_account.name}")
    if amount != money(cheque.amount):
        changes.append(f"amount was Rs {money(cheque.amount):,.2f}")
    cheque.party_account, cheque.amount, cheque.cheque_no, cheque.received_on = account, amount, cheque_no, received_on
    cheque.cheque_date = _day(payload.get("chequeDate") or cheque.cheque_date, "cheque's date")
    if "drawnOn" in payload:
        cheque.drawn_on = (payload.get("drawnOn") or "").strip()[:80] or None
    if "note" in payload:
        cheque.note = (payload.get("note") or "").strip()[:255] or None
    if changes:
        cheque.note = _note(cheque.note, f"Changed by {user.name} ({', '.join(changes)})")
    await cheque.save()
    return cheque


def _status_word(cheque: Cheque) -> str:
    return {"pending": "in hand", "cleared": "cleared", "bounced": "bounced", "cancelled": "cancelled"}.get(cheque.status, cheque.status)


@atomic()
async def clear_cheque(user: User, cheque_id: str, bank_account_id: str, cleared_on) -> Cheque:
    cheque = await _cheque(cheque_id)
    if cheque.status != "pending":
        raise MoneyError(f"{cheque.number} is {_status_word(cheque)}.")
    if cheque.direction == "issued":
        bank = cheque.bank_account if not bank_account_id else await _bank_account(bank_account_id, "Pick the bank account it was paid from.")
        if bank is None:
            raise MoneyError("Pick the bank account it was paid from.")
    else:
        bank = await _bank_account(bank_account_id, "Pick the bank account it was deposited into.")
    day = _day(cleared_on or shop_day(), "day it cleared")
    if day < cheque.received_on:
        raise MoneyError("A cheque can't clear before it was received." if cheque.direction == "received" else "A cheque can't clear before it was written.")
    vouchers_service.check_open(await vouchers_service.settings(), day)
    cheque.status, cheque.bank_account, cheque.cleared_on = "cleared", bank, day
    await cheque.save()
    return cheque


@atomic()
async def bounce_cheque(user: User, cheque_id: str, bounced_on, note: str | None) -> Cheque:
    cheque = await _cheque(cheque_id)
    if cheque.direction == "issued":
        raise MoneyError("A cheque the branch wrote is cancelled, not bounced, if it isn't paid. Undo its clearing first if it was marked cleared.")
    if cheque.status not in ("pending", "cleared"):
        raise MoneyError(f"{cheque.number} is {_status_word(cheque)}.")
    day = _day(bounced_on or shop_day(), "day it bounced")
    if day < cheque.received_on or (cheque.cleared_on and day < cheque.cleared_on):
        raise MoneyError("A cheque can't bounce before it was received or cleared.")
    vouchers_service.check_open(await vouchers_service.settings(), day)
    cheque.status, cheque.bounced_on = "bounced", day
    if note and note.strip():
        cheque.note = _note(cheque.note, f"Bounced: {note.strip()}")
    await cheque.save()
    await _party_effect(cheque.party_account, Decimal(cheque.amount))
    return cheque


@atomic()
async def undo_cheque(user: User, cheque_id: str, note: str | None) -> Cheque:
    """Undo the last step while its month is open: a clearing goes back to in hand; a bounce goes back to where the cheque
    was before it (cleared, or in hand)."""
    cheque = await _cheque(cheque_id)
    settings = await vouchers_service.settings()
    if cheque.status == "cleared":
        vouchers_service.check_open(settings, cheque.cleared_on)
        await _drop(cheque, "cheque-cleared", "cheque-issued-cleared")
        cheque.status, cheque.cleared_on = "pending", None
        if cheque.direction == "received":
            cheque.bank_account = None
        cheque.note = _note(cheque.note, f"Clearing undone by {user.name}" + (f": {note.strip()}" if note and note.strip() else ""))
    elif cheque.status == "bounced":
        vouchers_service.check_open(settings, cheque.bounced_on)
        again = await Cheque.filter(redeposit_of_id=str(cheque.id)).exclude(status="cancelled").first()
        if again:
            raise MoneyError(f"It was deposited again as {again.number}. Cancel that one first.")
        await _drop(cheque, "cheque-bounced")
        cheque.status = "cleared" if cheque.cleared_on else "pending"
        cheque.bounced_on = None
        cheque.note = _note(cheque.note, f"Bounce undone by {user.name}" + (f": {note.strip()}" if note and note.strip() else ""))
        await _party_effect(cheque.party_account, -Decimal(cheque.amount))
    else:
        raise MoneyError(f"{cheque.number} is {_status_word(cheque)}, so there is nothing to undo.")
    await cheque.save()
    return cheque


@atomic()
async def redeposit_cheque(user: User, cheque_id: str, day_value, note: str | None) -> Cheque:
    """A bounced cheque presented again. It becomes a new cheque in hand (the bounce stays on the record), so each
    presentation has its own vouchers and can clear or bounce on its own."""
    cheque = await _cheque(cheque_id)
    if cheque.direction != "received" or cheque.status != "bounced":
        raise MoneyError("Only a received cheque that bounced can be deposited again.")
    again = await Cheque.filter(redeposit_of_id=str(cheque.id)).exclude(status="cancelled").first()
    if again:
        raise MoneyError(f"It was already deposited again as {again.number}.")
    day = _day(day_value or shop_day(), "day it was deposited again")
    if day < cheque.bounced_on:
        raise MoneyError("It can't be deposited again before the day it bounced.")
    vouchers_service.check_open(await vouchers_service.settings(), day)
    seq = await next_value("cheque", 1)
    fresh = await Cheque.create(
        number=f"CHQ-{seq:06d}", direction="received", party_account=cheque.party_account, cheque_no=cheque.cheque_no, drawn_on=cheque.drawn_on,
        cheque_date=cheque.cheque_date, received_on=day, amount=cheque.amount, redeposit_of_id=str(cheque.id), created_by_name=user.name,
        note=_note((note or "").strip() or None, f"Deposited again after {cheque.number} bounced"),
    )
    await _party_effect(cheque.party_account, -Decimal(cheque.amount))
    return fresh


@atomic()
async def cancel_cheque(user: User, cheque_id: str, reason: str | None) -> Cheque:
    cheque = await _cheque(cheque_id)
    if cheque.status != "pending":
        raise MoneyError("Only a cheque still in hand can be cancelled. A cleared cheque that came back is bounced."
                         if cheque.direction == "received" else "Only a cheque not yet cleared can be cancelled. Undo its clearing first.")
    if not (reason or "").strip():
        raise MoneyError("Say why it's being cancelled.")
    vouchers_service.check_open(await vouchers_service.settings(), cheque.received_on)
    cheque.status = "cancelled"
    cheque.note = _note(cheque.note, f"Cancelled: {reason.strip()}")
    await cheque.save()
    await _drop(cheque, *CHEQUE_SOURCES)
    if cheque.direction == "received":
        await _party_effect(cheque.party_account, Decimal(cheque.amount))
    return cheque


async def list_cheques(status: str | None, direction: str | None = None, cheque_id: str | None = None) -> list[dict]:
    qs = Cheque.all()
    if status:
        qs = qs.filter(status__in=[s.strip() for s in status.split(",") if s.strip()])
    if direction:
        qs = qs.filter(direction=direction)
    if cheque_id:
        qs = qs.filter(id=cheque_id)
    return [await cheque_out(c) for c in await qs.order_by("cheque_date", "created_at")]


def now() -> datetime:
    return datetime.now(timezone.utc)
