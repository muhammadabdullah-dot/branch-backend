import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.models import GiftVoucher, Party, VoucherRedemption
from app.services.inventory_service import in_window


class VoucherError(Exception):
    def __init__(self, message: str):
        self.message = message


async def find(code: str) -> GiftVoucher | None:
    return await GiftVoucher.get_or_none(code=code.strip().upper())


async def list_all(
    limit: int, offset: int, from_at: datetime | None = None, to_at: datetime | None = None,
) -> tuple[list[GiftVoucher], int]:
    """Branch-wide, not terminal-scoped — every voucher issued at any till, not just the ones
    this browser happened to issue or look up."""
    qs = in_window(GiftVoucher.all(), "issued_at", from_at, to_at)
    total = await qs.count()
    vouchers = await qs.order_by("-issued_at").offset(offset).limit(limit)
    return vouchers, total


PAID_BY = ("CASH", "CARD", "BANK", "EASYPAISA", "JAZZCASH", "COMPLIMENTARY")


@atomic()
async def issue(face_value: Decimal, party_id: str | None, paid_by: str | None = None, reference: str | None = None, user=None) -> GiftVoucher:
    """Issue a voucher, either to a specific customer or open to whoever holds the code.

    The customer is picked from the registry rather than typed. A typed name is a label that cannot
    be checked at the till, so "issued to Sana Bibi" meant nothing the moment the voucher was spent.

    How it was paid for is recorded: cash goes into the open till as a Cash In, card / bank / wallet go to their
    accounts, and a complimentary voucher is a marketing cost. Until it's spent, it's money the branch owes.
    """
    if face_value <= 0:
        raise VoucherError("A voucher has to be worth something.")
    from app.schemas.types import money_str
    from app.services import masters_service

    # The branch's gift voucher rules (Branch Console > Lists and Settings > Receipt and Vouchers).
    rules = await masters_service.voucher_rules()
    if face_value < rules["minValue"]:
        raise VoucherError(f"A gift voucher is at least Rs {money_str(rules['minValue'])} at this branch.")
    if rules["maxValue"] is not None and face_value > rules["maxValue"]:
        raise VoucherError(f"A gift voucher is at most Rs {money_str(rules['maxValue'])} at this branch.")
    paid_by = (paid_by or "").strip().upper() or None
    if paid_by is not None and paid_by not in PAID_BY:
        raise VoucherError("Pick how the voucher was paid for: cash, card, bank, Easypaisa, JazzCash, or complimentary.")
    if paid_by and paid_by != "COMPLIMENTARY":
        try:
            await masters_service.refuse_switched_off_methods([paid_by])
        except masters_service.MastersError as exc:
            raise VoucherError(exc.message) from exc
    party = None
    if party_id:
        party = await Party.get_or_none(id=party_id, active=True)
        if not party:
            raise VoucherError("That customer isn't in the registry, or has been deactivated.")
        if party.is_walk_in:
            # The walk-in party is everyone who didn't give a name. Tying a voucher to it would read
            # as restricted while being spendable by literally any cash customer.
            raise VoucherError(
                "Pick a named customer, or leave it open. A voucher can't belong to the walk-in party."
            )
    code = f"GV-{random.randint(10000, 99999)}"
    while await GiftVoucher.exists(code=code):
        code = f"GV-{random.randint(10000, 99999)}"
    reference = (reference or "").strip()[:60] or None
    if paid_by == "CASH":
        from app.models import CashMovement
        from app.services.accounts_chart_service import Resolver
        from app.services.till_service import session_for

        if user is None:
            raise VoucherError("Who's taking the cash?")
        # The drawer of whoever takes the money — with several tills open there is no single "the till".
        till = await session_for(user)
        if not till:
            raise VoucherError("Cash for a voucher goes into your till, so open your till first.")
        await CashMovement.create(
            till_session=till, kind="in", amount=face_value, denominations={}, user=user,
            account=await Resolver().key("liab.gift_vouchers"), payee=party.name if party else None,
            notes=f"Gift voucher {code} sold",
        )
    return await GiftVoucher.create(
        code=code,
        face_value=face_value,
        balance=face_value,
        party=party,
        issued_to_name=party.name if party else None,
        expires_at=datetime.now(timezone.utc) + timedelta(days=rules["validityDays"]),
        paid_by=paid_by, payment_reference=reference, issued_by_name=getattr(user, "name", None),
    )


# ── Who a voucher belongs to ───────────────────────────────────────────────────────────────────
#
# A voucher is one of three things, and the rule below is the only place that decides which:
#
#   * linked to a customer        → only that customer's bill may spend it
#   * named, but never linked     → a voucher issued before customers were picked from the registry,
#                                   when the name was typed. It still belongs to that person. It is
#                                   resolved against the registry by exact name; if that finds
#                                   exactly one customer it is theirs, and if it doesn't, nobody can
#                                   spend it until someone makes it unambiguous.
#   * no customer and no name     → an open gift voucher; whoever holds the code spends it
#
# The middle case is the one that went wrong. Vouchers issued with a typed name were left unlinked
# when linking arrived, and the check only looked at the link — so a voucher that plainly read
# "Ali Traders" on screen was treated as anyone's, and Ali Traders' credit went onto another
# customer's bill. A name on a voucher now always means it belongs to someone.


async def _party_by_name(name: str) -> tuple[Party | None, str | None]:
    """The single registered customer with exactly this name, or the reason there isn't one."""
    matches = await Party.filter(name__iexact=name.strip(), is_walk_in=False, active=True).limit(2)
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, (
            f"it was issued by name to \"{name}\", and no customer with that name is registered, "
            "so there is no way to check who is holding it. Register the customer, then try again."
        )
    return None, (
        f"it was issued by name to \"{name}\", and more than one customer has that name, "
        "so it can't be told whose it is."
    )


async def owner_of(voucher: GiftVoucher, *, link: bool = False) -> tuple[Party | None, str | None]:
    """(owner, reason). owner is None with no reason for an open voucher; None with a reason when
    the voucher belongs to someone who can't be identified.

    `link=True` writes the resolved customer onto a name-only voucher, so the question is settled
    once rather than re-asked at every till. Only set on a write path — a lookup must not change data.
    """
    if voucher.party_id:
        await voucher.fetch_related("party")
        return voucher.party, None
    if not (voucher.issued_to_name or "").strip():
        return None, None
    party, reason = await _party_by_name(voucher.issued_to_name)
    if party and link:
        voucher.party_id = party.id
        await voucher.save(update_fields=["party_id"])
    return party, reason


async def refusal_for(voucher: GiftVoucher, party_id: str | None, *, link: bool = False) -> str | None:
    """Why this voucher can't go on a bill for `party_id` — or None if it can.

    Called in two places and they must never disagree: when a code is typed into the payment dialog,
    so the salesperson hears no before they take the customer's money, and when the sale commits,
    so a browser that skipped the first check still can't get past the second.
    """
    if voucher.status != "active":
        return f"Voucher {voucher.code} is {voucher.status} and can't be used."
    if voucher.expires_at and voucher.expires_at < datetime.now(timezone.utc):
        return f"Voucher {voucher.code} expired on {voucher.expires_at:%d %b %Y}."
    if voucher.balance <= 0:
        return f"Voucher {voucher.code} has no balance left."

    owner, unresolved = await owner_of(voucher, link=link)
    if unresolved:
        return f"Voucher {voucher.code} can't be used: {unresolved}"
    if owner and str(owner.id) != str(party_id or ""):
        return (
            f"Voucher {voucher.code} belongs to {owner.name} ({owner.code}) and can only be used on "
            f"their bill. Select {owner.name} as the customer first."
        )
    return None


async def link_named_vouchers() -> None:
    """Startup repair: link every typed-name voucher to the one customer with that exact name.

    Idempotent, and it only ever fills an empty link — a voucher already linked is never moved to
    someone else. Vouchers whose name matches nobody, or matches more than one customer, are left
    alone and reported; the ownership check still refuses them, so leaving them unlinked is safe.
    """
    for voucher in await GiftVoucher.filter(party_id=None).exclude(issued_to_name=None):
        if not (voucher.issued_to_name or "").strip():
            continue
        party, reason = await _party_by_name(voucher.issued_to_name)
        if party:
            voucher.party_id = party.id
            await voucher.save(update_fields=["party_id"])
            print(f"  voucher {voucher.code} linked to {party.name} ({party.code})", flush=True)
        else:
            print(f"  voucher {voucher.code} left unlinked: {reason}", flush=True)


@atomic()
async def redeem(code: str, amount: Decimal, invoice_number: str, party_id: str | None = None) -> GiftVoucher:
    """Spend from a voucher, on a bill for `party_id`. Uses the same refusal rule as the lookup."""
    voucher = await GiftVoucher.get_or_none(code=code.strip().upper())
    if not voucher:
        raise VoucherError(f"No voucher with code {code.strip().upper()}.")
    reason = await refusal_for(voucher, party_id, link=True)
    if reason:
        raise VoucherError(reason)
    if voucher.balance < amount:
        raise VoucherError(
            f"Voucher {voucher.code} only has Rs {voucher.balance:,.0f} left, which is less than the Rs {amount:,.0f} being taken."
        )
    voucher.balance -= amount
    if voucher.balance <= 0:
        voucher.status = "redeemed"
    await voucher.save()
    await VoucherRedemption.create(voucher=voucher, invoice_number=invoice_number, amount=amount)
    return voucher
