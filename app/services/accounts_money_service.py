"""Money coming in against what customers owe: payments at the counter, and cheques.

A credit customer's balance (the figure the till checks their credit against) goes down the moment they pay —
cash into the open till, or card / bank / wallet. A cheque takes the balance down when it's received, and puts
it back if the cheque bounces or turns out to be a mistake.
"""
from datetime import date, datetime, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.models import Account, CashMovement, Cheque, CustomerPayment, OutboxEvent, Party, TillSession, User, next_value
from app.services import vouchers_service
from app.services.accounts_chart_service import customer_account, money
from app.services.accounts_reports_service import shop_day

ZERO = Decimal("0")
PAYMENT_METHODS = ("CASH", "CARD", "BANK", "EASYPAISA", "JAZZCASH")


class MoneyError(Exception):
    def __init__(self, message: str):
        self.message = message


def payment_out(payment: CustomerPayment) -> dict:
    return {
        "id": str(payment.id), "number": payment.number, "partyId": str(payment.party_id),
        "partyName": payment.party.name if hasattr(payment, "party") and payment.party else None,
        "partyCode": payment.party.code if hasattr(payment, "party") and payment.party else None,
        "amount": format(money(payment.amount), "f"), "method": payment.method, "reference": payment.reference, "note": payment.note,
        "receivedBy": payment.received_by.name if hasattr(payment, "received_by") and payment.received_by else None,
        "balanceAfter": format(money(payment.balance_after), "f") if payment.balance_after is not None else None,
        "at": payment.at.isoformat(),
    }


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
        till = await TillSession.get_or_none(status="open")
        if not till:
            raise MoneyError("Cash goes into the till — open the till first.")
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


async def list_payments(party_id: str | None, from_at: datetime | None, to_at: datetime | None, limit: int, offset: int) -> tuple[list[dict], int]:
    qs = CustomerPayment.all()
    if party_id:
        qs = qs.filter(party_id=party_id)
    if from_at:
        qs = qs.filter(at__gte=from_at)
    if to_at:
        qs = qs.filter(at__lte=to_at)
    total = await qs.count()
    rows = await qs.order_by("-at").offset(offset).limit(limit).prefetch_related("party", "received_by")
    return [payment_out(p) for p in rows], total


# ── cheques ────────────────────────────────────────────────────────────────────────────────────

async def cheque_out(cheque: Cheque) -> dict:
    await cheque.fetch_related("party_account", "bank_account")
    today = shop_day()
    return {
        "id": str(cheque.id), "number": cheque.number, "partyAccountId": str(cheque.party_account_id),
        "partyAccountName": cheque.party_account.name, "partyAccountCode": cheque.party_account.code,
        "bankAccountId": str(cheque.bank_account_id) if cheque.bank_account_id else None,
        "bankAccountName": cheque.bank_account.name if cheque.bank_account else None,
        "chequeNo": cheque.cheque_no, "drawnOn": cheque.drawn_on, "chequeDate": cheque.cheque_date.isoformat(),
        "receivedOn": cheque.received_on.isoformat(), "amount": format(money(cheque.amount), "f"), "status": cheque.status,
        "postDated": cheque.status == "pending" and cheque.cheque_date > today,
        "clearedOn": cheque.cleared_on.isoformat() if cheque.cleared_on else None,
        "bouncedOn": cheque.bounced_on.isoformat() if cheque.bounced_on else None,
        "note": cheque.note, "createdBy": cheque.created_by_name,
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


@atomic()
async def record_cheque(user: User, payload: dict) -> Cheque:
    account = await Account.get_or_none(id=payload.get("partyAccountId"))
    if not account or not account.active:
        raise MoneyError("Pick who the cheque is from.")
    if account.kind in ("cash", "bank", "wallet"):
        raise MoneyError("A cheque comes from a customer or another party, not one of the branch's own cash or bank accounts.")
    amount = money(payload.get("amount"))
    if amount <= 0:
        raise MoneyError("Enter the cheque's amount.")
    cheque_no = (payload.get("chequeNo") or "").strip()[:30]
    if not cheque_no:
        raise MoneyError("Enter the cheque number.")
    received_on = _day(payload.get("receivedOn") or shop_day(), "day it was received")
    vouchers_service.check_open(await vouchers_service.settings(), received_on)
    seq = await next_value("cheque", 1)
    cheque = await Cheque.create(
        number=f"CHQ-{seq:06d}", party_account=account, cheque_no=cheque_no, drawn_on=(payload.get("drawnOn") or "").strip()[:80] or None,
        cheque_date=_day(payload.get("chequeDate") or received_on, "cheque's date"), received_on=received_on, amount=amount,
        note=(payload.get("note") or "").strip()[:255] or None, created_by_name=user.name,
    )
    await _party_effect(account, -amount)
    return cheque


async def _cheque(cheque_id: str) -> Cheque:
    cheque = await Cheque.get_or_none(id=cheque_id).prefetch_related("party_account")
    if not cheque:
        raise MoneyError("That cheque doesn't exist.")
    return cheque


@atomic()
async def clear_cheque(user: User, cheque_id: str, bank_account_id: str, cleared_on) -> Cheque:
    cheque = await _cheque(cheque_id)
    if cheque.status != "pending":
        raise MoneyError(f"{cheque.number} is {cheque.status}.")
    bank = await Account.get_or_none(id=bank_account_id)
    if not bank or bank.kind not in ("bank", "wallet") or not bank.active:
        raise MoneyError("Pick the bank account it was deposited into.")
    day = _day(cleared_on or shop_day(), "day it cleared")
    if day < cheque.received_on:
        raise MoneyError("A cheque can't clear before it was received.")
    vouchers_service.check_open(await vouchers_service.settings(), day)
    cheque.status, cheque.bank_account, cheque.cleared_on = "cleared", bank, day
    await cheque.save()
    return cheque


@atomic()
async def bounce_cheque(user: User, cheque_id: str, bounced_on, note: str | None) -> Cheque:
    cheque = await _cheque(cheque_id)
    if cheque.status not in ("pending", "cleared"):
        raise MoneyError(f"{cheque.number} is {cheque.status}.")
    day = _day(bounced_on or shop_day(), "day it bounced")
    if day < cheque.received_on:
        raise MoneyError("A cheque can't bounce before it was received.")
    vouchers_service.check_open(await vouchers_service.settings(), day)
    cheque.status, cheque.bounced_on = "bounced", day
    if note and note.strip():
        cheque.note = f"{(cheque.note + ' — ') if cheque.note else ''}Bounced: {note.strip()}"[:255]
    await cheque.save()
    await _party_effect(cheque.party_account, Decimal(cheque.amount))
    return cheque


@atomic()
async def cancel_cheque(user: User, cheque_id: str, reason: str | None) -> Cheque:
    cheque = await _cheque(cheque_id)
    if cheque.status != "pending":
        raise MoneyError("Only a cheque still in hand can be cancelled. A cleared cheque that came back is bounced.")
    if not (reason or "").strip():
        raise MoneyError("Say why it's being cancelled.")
    vouchers_service.check_open(await vouchers_service.settings(), cheque.received_on)
    cheque.status = "cancelled"
    cheque.note = f"{(cheque.note + ' — ') if cheque.note else ''}Cancelled: {reason.strip()}"[:255]
    await cheque.save()
    await _party_effect(cheque.party_account, Decimal(cheque.amount))
    return cheque


async def list_cheques(status: str | None) -> list[dict]:
    qs = Cheque.all()
    if status:
        qs = qs.filter(status__in=[s.strip() for s in status.split(",") if s.strip()])
    return [await cheque_out(c) for c in await qs.order_by("cheque_date", "created_at")]


def now() -> datetime:
    return datetime.now(timezone.utc)
