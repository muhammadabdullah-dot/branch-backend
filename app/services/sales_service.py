"""The centerpiece of I3 (contracts.md §6). POST /sales must be one atomic transaction covering
the sale record, line items, stock movement posting, voucher redemption, and outbox evidence —
@atomic() rolls back all of it on any failure, so a partial sale can never exist.
"""
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.models import (
    Location,
    OutboxEvent,
    Party,
    Product,
    ProductAlias,
    SaleLine,
    SaleRecord,
    SaleTender,
    StockMovement,
    TillSession,
    User,
    next_value,
)
from app.schemas.sales import SaleCreateRequest
from app.schemas.types import money_str
from app.services import discount_approval_service, gift_voucher_service, media_service, members_service

DISCOUNT_LIMIT_PERCENT = Decimal("5")
# Payments that always need the customer's name and mobile number, and make them a member.
CARD_TENDERS = ("CARD",)
ONLINE_TENDERS = ("EASYPAISA", "JAZZCASH", "BANK")
WALLET_TENDERS = ("EASYPAISA", "JAZZCASH")
PROOF_FOLDER = "payment-proofs"
PROOF_MAX_BYTES = 6 * 1024 * 1024
DEFAULT_LOCATION_ID = "loc-1"
ZERO = Decimal("0")


class SaleError(Exception):
    def __init__(self, message: str):
        self.message = message


# Until this branch server has been verified it has no identity, and an invoice has to be numbered
# something. `BR` is deliberately not a real branch's code — a bill printed before setup is a bill
# that should be obvious as such, rather than one quietly filed under whichever branch the software
# was written in.
UNVERIFIED_PREFIX = "BR"


async def invoice_prefix() -> str:
    """This branch's own code, from the identity head office issued it.

    It used to be the literal string "HO". That was the head office assumption baked into the
    numbering: every branch in the chain would have printed bills numbered as if they were Head
    Office, and two branches' invoice numbers would have collided the moment a second one opened.
    A branch is a branch — its bills carry its own code.
    """
    from app.services import registration_service

    identity = await registration_service.current()
    return identity.code if identity else UNVERIFIED_PREFIX


async def peek_next_invoice_number() -> str:
    from app.models import Counter

    counter = await Counter.get_or_none(id="invoice")
    seq = counter.value if counter else 143
    return f"{await invoice_prefix()}-{_invoice_year()}-{seq:06d}"


def _invoice_year() -> int:
    """The branch's own trading year, not the server's UTC one — Pakistan is UTC+5, so a sale rung
    at half past nine in the evening on 31 December is still last year's bill."""
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=5))).year


async def _resolve_party(party_id: str | None) -> Party:
    if party_id:
        party = await Party.get_or_none(id=party_id)
        if not party:
            raise SaleError("Party not found")
        return party
    party = await Party.get_or_none(is_walk_in=True)
    if not party:
        raise SaleError("Walk-in party not seeded")
    return party


def save_payment_proof(content: bytes) -> str:
    """Keep a customer's transfer screenshot and return the id the sale will refer to."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return media_service.save_picture(PROOF_FOLDER, stamp, content, max_bytes=PROOF_MAX_BYTES)


async def payment_proof(invoice_number: str, code: str):
    tender = await SaleTender.get_or_none(
        sale__invoice_number=invoice_number.strip().upper(), code=code.strip().upper(), proof__not_isnull=True,
    )
    return media_service.picture_file(tender.proof) if tender else None


async def _resolve_member(payload: SaleCreateRequest, party: Party, cashier: User):
    """The member this bill is for, or None. Card and online payments sign the customer up if they're
    new; so does a customer paying another way who chose to join."""
    info = payload.member
    card = [c for c in CARD_TENDERS if payload.tenders.get(c, ZERO) > 0]
    online = [c for c in ONLINE_TENDERS if payload.tenders.get(c, ZERO) > 0]
    needs_member = bool(card or online)
    try:
        if info and info.code:
            member = await members_service.get(info.code)
            if not member.active:
                raise SaleError(f"Member {member.code} ({member.name}) is switched off.")
            if party and not party.is_walk_in and member.party_id is None:
                member, _ = await members_service.find_or_create(
                    member.name, member.phone, joined_via=member.joined_via, joined_method=member.joined_method,
                    user=cashier, party=party,
                )
            return member
        if info and (info.phone or info.name) and (needs_member or info.join):
            if not (info.name or "").strip() or not (info.phone or "").strip():
                raise SaleError(
                    "Card and online payments need the customer's name and mobile number." if needs_member
                    else "Enter the customer's name and mobile number to make them a member."
                )
            member, _ = await members_service.find_or_create(
                info.name, info.phone,
                joined_via="card" if card else "online" if online else "till",
                joined_method=(card or online or [None])[0], user=cashier, party=party,
            )
            return member
    except members_service.MemberError as exc:
        raise SaleError(exc.message) from exc
    if needs_member:
        raise SaleError("Card and online payments need the customer's name and mobile number.")
    return None


async def _tender_details(payload: SaleCreateRequest) -> dict[str, dict]:
    """Checks what each card and online payment has to carry, and returns it ready to store."""
    out: dict[str, dict] = {}
    details = payload.tenderDetails or {}
    for code in CARD_TENDERS + ONLINE_TENDERS:
        if payload.tenders.get(code, ZERO) <= 0:
            continue
        d = details.get(code)
        if code == "CARD":
            last4 = ((d.reference if d else None) or "").strip()
            if not (len(last4) == 4 and last4.isdigit()):
                raise SaleError("Enter the card's last 4 digits from the machine's shop copy.")
            out[code] = {"reference": last4}
        elif code in WALLET_TENDERS:
            name = "Easypaisa" if code == "EASYPAISA" else "JazzCash"
            try:
                number = members_service.normalize_phone(d.reference if d else None, f"the {name} account number")
            except members_service.MemberError as exc:
                raise SaleError(exc.message) from exc
            out[code] = {"reference": number, "transaction_id": ((d.transactionId if d else None) or "").strip() or None}
        else:  # BANK
            account = ((d.account if d else None) or "").strip()
            txn = ((d.transactionId if d else None) or "").strip()
            proof = ((d.proofId if d else None) or "").strip()
            if not account:
                raise SaleError("Pick the bank account the transfer went into.")
            if not txn:
                raise SaleError("Enter the bank transfer's transaction ID from the customer's screenshot.")
            used = await SaleTender.filter(code="BANK", transaction_id__iexact=txn).prefetch_related("sale").first()
            if used:
                raise SaleError(f"Transaction ID {txn} already paid bill {used.sale.invoice_number}. Check the customer's screenshot.")
            if not proof or not proof.startswith(f"{PROOF_FOLDER}/") or ".." in proof or not media_service.picture_file(proof):
                raise SaleError("Attach the customer's screenshot of the bank transfer.")
            out[code] = {"transaction_id": txn, "account": account[:80], "proof": proof}
    return out


def _line_gross(line, unit_price: Decimal) -> Decimal:
    sign = Decimal("-1") if line.isReturn else Decimal("1")
    return sign * line.qty * unit_price


def _item_disc(line, product: Product, alias: ProductAlias | None) -> Decimal:
    """The discount set on the Item (or on the pack barcode it was rung up by), signed like the line."""
    sign = Decimal("-1") if line.isReturn else Decimal("1")
    if alias and alias.qty and alias.qty > 0:
        percent, flat, flat_units = alias.disc_percent, alias.disc_flat, line.qty / alias.qty
    else:
        percent, flat, flat_units = product.disc_percent, product.disc_flat, line.qty
    amount = line.qty * line.unitPrice * (percent or ZERO) / Decimal("100") + flat_units * (flat or ZERO)
    # Never more than the line is worth.
    return sign * min(amount, line.qty * line.unitPrice)


@atomic()
async def create_sale(cashier: User, payload: SaleCreateRequest) -> SaleRecord:
    # Idempotent replay: checked FIRST, before any other validation or side effect, so a
    # retried request (lost response, double-click) returns the already-committed sale instead
    # of re-running credit-limit checks, re-posting stock movements, or re-redeeming a voucher.
    # Note: this closes the common case (a delayed/lost-response retry after the first request
    # already committed) but not a true simultaneous double-fire — two requests with the same
    # key landing in the same instant could both pass this check before either commits, and the
    # second would then fail on the column's unique constraint rather than returning the first
    # sale gracefully. Acceptable for now (a single till/cashier submitting sequentially can't
    # produce that race; only a client bug firing the exact same request twice in parallel could).
    if payload.clientRequestId:
        existing = await SaleRecord.get_or_none(client_request_id=payload.clientRequestId)
        if existing:
            await existing.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by", "member")
            return existing

    if not payload.lines:
        raise SaleError("A sale needs at least one line")
    # The bill goes into the drawer the person ringing it has open — that is what makes each counter
    # reconcile on its own.
    from app.services import till_service

    till = await till_service.session_for(cashier)
    if not till:
        raise SaleError("No Till is open. Open one at your counter before taking payment")

    # A payment method the branch has switched off (Branch Console > Lists and Settings) can't be taken.
    from app.services import masters_service

    try:
        await masters_service.refuse_switched_off_methods(code for code, amount in payload.tenders.items() if amount > 0)
    except masters_service.MastersError as exc:
        raise SaleError(exc.message) from exc

    party = await _resolve_party(payload.partyId)
    products: dict[str, Product] = {}
    aliases: dict[int, ProductAlias] = {}
    for index, line in enumerate(payload.lines):
        product = await Product.get_or_none(id=line.productId)
        if not product:
            raise SaleError(f"Unknown product {line.productId}")
        products[line.productId] = product
        if line.aliasCode:
            alias = await ProductAlias.get_or_none(code=line.aliasCode.strip(), product_id=product.id)
            if alias:
                aliases[index] = alias

    # Empty means empty: every Item on the bill must be in stock for its whole quantity. What this same bill takes back
    # (an exchange) is back on the shelf first, so it counts.
    from app.services import stock_guard

    wanted: dict[str, Decimal] = {}
    taken_back: dict[str, Decimal] = {}
    for line in payload.lines:
        bucket = taken_back if line.isReturn else wanted
        bucket[line.productId] = bucket.get(line.productId, ZERO) + line.qty
    if wanted:
        held = await stock_guard.on_hand(list(wanted))
        short = []
        for pid, qty in wanted.items():
            have = held.get(pid, ZERO) + taken_back.get(pid, ZERO)
            if have < qty:
                short.append(f"{products[pid].name} ({stock_guard.qty_text(have)} in stock, {stock_guard.qty_text(qty)} on the bill)")
        if short:
            raise SaleError("Not enough stock: " + "; ".join(short) + ". Lower the quantity or take the Item off the bill.")

    gross = sum((_line_gross(l, l.unitPrice) for l in payload.lines), ZERO)
    # The Item's own discount comes first and needs no one's approval — it's set on the Item. A line
    # rung up by a pack barcode takes that pack's discount instead (flat is per pack).
    item_discs = [_item_disc(l, products[l.productId], aliases.get(i)) for i, l in enumerate(payload.lines)]
    # A bill-wide discount never touches an Item with Lock Discount set.
    after_item = [_line_gross(l, l.unitPrice) - d for l, d in zip(payload.lines, item_discs)]
    discountable = sum((v for l, v in zip(payload.lines, after_item) if not products[l.productId].lock_disc), ZERO)
    if (payload.discPercent > 0 or payload.flatDisc > 0) and discountable <= 0:
        if all(products[l.productId].lock_disc for l in payload.lines):
            raise SaleError("Every Item on this bill has its discount locked, so no bill discount can be given.")
        raise SaleError("Nothing on this bill is being sold, so there's nothing for a bill discount to come off. Take the discount off.")
    percent_disc = discountable * payload.discPercent / Decimal("100")
    bill_disc = percent_disc + payload.flatDisc
    line_discs = [
        item_disc + (
            ZERO if products[l.productId].lock_disc or discountable == 0
            else value * payload.discPercent / Decimal("100") + payload.flatDisc * (value / discountable)
        )
        for l, item_disc, value in zip(payload.lines, item_discs, after_item)
    ]
    disc_total = sum(item_discs, ZERO) + bill_disc

    gst = sum(
        ((_line_gross(l, l.unitPrice) - line_disc) * products[l.productId].tax_rate / Decimal("100"))
        for l, line_disc in zip(payload.lines, line_discs)
    )
    grand_total = gross - disc_total + payload.fare + gst
    net_value = grand_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    # Only the salesperson's own bill discount counts against their authority. An Item's discount was
    # decided when the Item was set up, and asking a manager to re-approve it on every sale would
    # train everyone to click through the approval.
    effective_pct = (bill_disc / gross * Decimal("100")) if gross > 0 else ZERO
    override_user = None
    from app.services.rbac_service import discount_limit_of

    own_limit = discount_limit_of(cashier)
    if effective_pct > own_limit + discount_approval_service.PERCENT_TOLERANCE:
        if not payload.discountApprovalToken:
            raise SaleError(
                f"Discount {effective_pct:.1f}% is more than your {own_limit.normalize():f}% limit, so someone with a higher limit needs to approve it"
            )
        # A signed approval from POST /sales/discount-approvals, never a bare user id — see
        # discount_approval_service for what the old id-on-trust check let a salesperson do.
        try:
            override_user = await discount_approval_service.verify(
                payload.discountApprovalToken, cashier, payload.clientRequestId, effective_pct
            )
        except discount_approval_service.ApprovalError as exc:
            raise SaleError(exc.message) from exc

    received = sum(payload.tenders.values(), ZERO)
    if received < net_value:
        raise SaleError(f"Payment not covered. Still to pay: {money_str(net_value - received)}")
    cash_back = max(ZERO, received - net_value)

    # Members and points. Earned on what the customer actually paid — never on what points paid for.
    details = await _tender_details(payload)
    member = await _resolve_member(payload, party, cashier)
    loyalty = await members_service.settings()
    points_amount = payload.tenders.get("POINTS", ZERO)
    points_redeemed = 0
    if points_amount > 0:
        if not member:
            raise SaleError("Paying with points needs the member's code or mobile number.")
        if not loyalty.enabled:
            raise SaleError("Loyalty points are switched off.")
        try:
            points_redeemed = members_service.redeem_points(points_amount, loyalty)
        except members_service.MemberError as exc:
            raise SaleError(exc.message) from exc
        if points_redeemed > member.points_balance:
            raise SaleError(f"{member.name} has {member.points_balance} points, not {points_redeemed}.")
        if points_redeemed < loyalty.min_redeem_points:
            raise SaleError(f"Points can be used from {loyalty.min_redeem_points} at a time.")
        cap = (net_value * loyalty.max_redeem_percent / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if points_amount > cap:
            raise SaleError(f"Points can pay up to {loyalty.max_redeem_percent.normalize():f}% of a bill, which is Rs {cap} on this one.")
    earned_points = 0
    if member and loyalty.enabled:
        earned_points = members_service.points_for_amount(net_value - points_amount, loyalty)

    invoice_seq = await next_value("invoice", 143)
    invoice_number = f"{await invoice_prefix()}-{_invoice_year()}-{invoice_seq:06d}"
    fbr_invoice_number = f"7000-{invoice_number[-8:]}"
    credit_amount = payload.tenders.get("CREDIT", ZERO)
    is_credit_sale = credit_amount > 0

    # Credit-limit enforcement (previously: creditBalance was seeded but never checked or
    # updated anywhere — legacy's "Check Balance Limit" had no equivalent at all).
    if is_credit_sale:
        if not party.credit_allowed:
            raise SaleError(f"{party.name} is not allowed credit sales")
        new_balance = party.credit_balance + credit_amount
        if new_balance > party.credit_limit:
            headroom = party.credit_limit - party.credit_balance
            raise SaleError(
                f"Credit sale of {money_str(credit_amount)} exceeds {party.name}'s "
                f"remaining headroom of {money_str(headroom)}"
            )
        party.credit_balance = new_balance
        await party.save(update_fields=["credit_balance"])

    sale = await SaleRecord.create(
        invoice_number=invoice_number,
        cashier=cashier,
        party=party,
        till_session=till,
        gross=gross,
        disc_total=disc_total,
        fare=payload.fare,
        gst=gst,
        grand_total=grand_total,
        net_value=net_value,
        discount_override_by=override_user,
        earned_points=earned_points,
        member=member,
        points_redeemed=points_redeemed,
        received=received,
        cash_back=cash_back,
        is_credit_sale=is_credit_sale,
        fbr_invoice_number=fbr_invoice_number,
        client_request_id=payload.clientRequestId,
    )

    location = await Location.get(id=DEFAULT_LOCATION_ID)
    for index, (line, line_disc) in enumerate(zip(payload.lines, line_discs)):
        product = products[line.productId]
        await SaleLine.create(
            sale=sale, product=product, qty=line.qty, unit_price=line.unitPrice, is_return=line.isReturn,
            alias_code=aliases[index].code if index in aliases else None,
            disc_amount=line_disc.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            unit_cost=product.avg_cost,
            tax_amount=((_line_gross(line, line.unitPrice) - line_disc) * product.tax_rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )

    # Returns on the bill go back on the shelf first; each sale then takes from Main Store, then the branch's other
    # locations, never leaving any of them below zero.
    now = datetime.now(timezone.utc)
    for line in sorted(payload.lines, key=lambda l: not l.isReturn):
        product = products[line.productId]
        if line.isReturn:
            await StockMovement.create(product=product, location=location, kind="return", qty=line.qty, origin_user=cashier, at=now, unit_cost=product.avg_cost)
            continue
        for place, qty in await stock_guard.plan_sale(product, line.qty):
            await StockMovement.create(product=product, location=place, kind="sell", qty=-qty, origin_user=cashier, at=now, unit_cost=product.avg_cost)

    for code, amount in payload.tenders.items():
        if amount > 0:
            await SaleTender.create(sale=sale, code=code, amount=amount, **details.get(code, {}))

    if member:
        if points_redeemed:
            await members_service.add_entry(member, "redeem", -points_redeemed, invoice_number=invoice_number, note=None, user=cashier)
        if earned_points:
            await members_service.add_entry(member, "earn", earned_points, invoice_number=invoice_number, note=None, user=cashier)
        await member.refresh_from_db()

    voucher_amount = payload.tenders.get("VOUCHER", ZERO)
    if voucher_amount > 0:
        if not payload.voucherCode:
            raise SaleError("Voucher code required for a VOUCHER tender")
        try:
            # The sale's own customer, so a voucher that belongs to someone only goes on their bill.
            await gift_voucher_service.redeem(payload.voucherCode, voucher_amount, invoice_number, str(party.id))
        except gift_voucher_service.VoucherError as exc:
            raise SaleError(exc.message) from exc

    await OutboxEvent.create(
        aggregate_type="SaleRecord",
        aggregate_id=str(sale.id),
        payload={
            "invoiceNumber": invoice_number, "netValue": str(net_value), "partyId": str(party.id),
            "memberCode": member.code if member else None,
        },
        origin_user_id=str(cashier.id), origin_device_id=get_device_id(),
    )

    await sale.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by", "member")
    return sale


async def find_by_invoice(invoice_number: str) -> SaleRecord | None:
    sale = await SaleRecord.get_or_none(invoice_number=invoice_number.strip().upper())
    if sale:
        await sale.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by", "member")
    return sale


async def list_sales(
    from_at: datetime | None, to_at: datetime | None, limit: int, offset: int
) -> tuple[list[SaleRecord], int]:
    """Branch-wide, not terminal-scoped — the read path X/Z, the Dashboard and Reports need
    instead of each browser's own local sales journal."""
    qs = SaleRecord.all()
    if from_at:
        qs = qs.filter(at__gte=from_at)
    if to_at:
        qs = qs.filter(at__lte=to_at)
    total = await qs.count()
    sales = (
        await qs.order_by("-at")
        .offset(offset)
        .limit(limit)
        .prefetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by", "member")
    )
    return sales, total
